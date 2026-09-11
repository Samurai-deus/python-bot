"""
Обмен initData на серверную сессию (задача 2.8, пункт 4 docs/DEFERRED_PLAN.md).

initData принимается здесь только в первый час после открытия Mini App и только
один раз; дальше приложение ходит с токеном сессии (api/sessions.py).
"""
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.deps import InitDataError, auth_disabled, verify_init_data
from api.sessions import EXCHANGE_MAX_AGE, IDLE_SECONDS, TOKEN_PREFIX, SessionUnavailable, get_store
from utils import principals
from utils.env import env_str

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SessionResponse(BaseModel):
    token: str
    expires_in: int


@router.post("/session", response_model=SessionResponse)
async def create_session(request: Request):
    if auth_disabled("http"):
        # dev-окружение: verify_auth и так пускает заглушку, токен — формальность
        return SessionResponse(token=TOKEN_PREFIX + "dev", expires_in=IDLE_SECONDS)

    init_data = request.headers.get("X-Telegram-Init-Data")
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing Telegram InitData")
    bot_token = env_str("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        logger.error("auth: TELEGRAM_BOT_TOKEN не задан — проверить подпись нечем")
        raise HTTPException(status_code=500, detail="Authentication service unavailable")

    try:
        user = verify_init_data(init_data, bot_token, max_age=EXCHANGE_MAX_AGE)
    except InitDataError as exc:
        logger.info("auth: обмен отклонён — %s", exc)
        raise HTTPException(status_code=401, detail="Invalid Telegram InitData")
    if not principals.is_allowed(user["user_id"]):
        logger.warning("auth: user_id=%s не в списке допущенных", user["user_id"])
        raise HTTPException(status_code=403, detail="Access denied")

    store = get_store()
    try:
        if not await store.claim_init_data(user["init_hash"]):
            # тот же initData второй раз — ответ как на неверный: различие подсказало бы атакующему
            logger.warning("auth: повторный обмен initData user_id=%s", user["user_id"])
            raise HTTPException(status_code=401, detail="Invalid Telegram InitData")
        token, expires_in = await store.create(user["user_id"], user.get("username"))
    except SessionUnavailable as exc:
        logger.error("auth: хранилище сессий недоступно: %s", exc)
        raise HTTPException(status_code=503, detail="Session service unavailable")
    logger.info("auth: выдана сессия user_id=%s", user["user_id"])
    return SessionResponse(token=token, expires_in=expires_in)
