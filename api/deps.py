"""
Зависимости FastAPI: аутентификация (кто это) и авторизация (что ему можно).

Проверка подписи Telegram initData здесь была реализована верно и такой осталась:
секрет HMAC("WebAppData", bot_token), строка проверки из отсортированных пар без
hash, сравнение через compare_digest. Изменилось то, что происходит ПОСЛЕ подписи.

Раньше валидная подпись была единственным условием чтения. Но подпись доказывает
только одно: initData выдал Telegram. Любой пользователь Telegram, открывший
Mini App бота, получает такой же валидный initData — и видел баланс, позиции со
стопами, историю сделок. А при пустом ADMIN_CHAT_ID (как в .env.example) ещё и
менял ключи биржи. Теперь после подписи проверяется, КТО пришёл: читать могут
допущенные (utils.principals.is_allowed), менять — только владелец.

Разбор initData для HTTP и WebSocket раньше был продублирован построчно; теперь
одна функция verify_init_data.
"""
import asyncio
import functools
import hashlib
import hmac
import json
import logging
import time
from typing import Optional
from urllib.parse import unquote

from fastapi import HTTPException, Request

from utils import principals
from utils.env import env_flag, env_str

logger = logging.getLogger(__name__)

# initData выдаётся один раз при открытии Mini App и без переоткрытия не
# обновляется, поэтому окно — сутки. Сокращение окна требует серверной сессии,
# это задача 2.8 плана; здесь окно только вынесено в одну константу.
INIT_DATA_MAX_AGE = 86400

# В каких окружениях разрешено отключать аутентификацию. Всё остальное, включая
# НЕзаданный ENVIRONMENT, считается боевым: DISABLE_AUTH=true, скопированный в
# прод-.env из примера в CLAUDE.md, раньше открывал все роуты и давал админа
# любому (заглушка user_id=0 проходила verify_admin).
_DEV_ENVIRONMENTS = frozenset({"development", "dev", "local", "test"})


async def run_sync(fn, *args, **kwargs):
    """Run a synchronous function in the default thread pool executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(fn, *args, **kwargs))


def auth_disabled(kind: str = "http") -> bool:
    """
    Отключена ли аутентификация. Флаг действует только в dev-окружении;
    в любом другом он игнорируется с ошибкой в логе (а api.main на старте
    ещё и откажется запускаться, см. enforce_security_config).
    """
    requested = env_flag("DISABLE_AUTH") or (kind == "ws" and env_flag("DISABLE_WS_AUTH"))
    if not requested:
        return False
    environment = env_str("ENVIRONMENT").lower()
    if environment not in _DEV_ENVIRONMENTS:
        logger.error(
            "DISABLE_AUTH/DISABLE_WS_AUTH проигнорирован: ENVIRONMENT=%r не из %s",
            environment, sorted(_DEV_ENVIRONMENTS),
        )
        return False
    logger.warning("DISABLE_AUTH: аутентификация %s отключена (ENVIRONMENT=%s)", kind, environment)
    return True


class InitDataError(Exception):
    """initData не прошёл проверку. Текст — для лога, наружу не отдаётся."""


def verify_init_data(init_data: str, bot_token: str, max_age: int = INIT_DATA_MAX_AGE,
                     now: Optional[float] = None) -> dict:
    """
    Проверяет подпись и свежесть initData, возвращает разобранные поля плюс
    user_id (int). Бросает InitDataError при любом несоответствии.
    """
    if not init_data:
        raise InitDataError("пустой initData")
    if not bot_token:
        raise InitDataError("не задан TELEGRAM_BOT_TOKEN")

    params = {}
    hash_value = None
    for part in init_data.split("&"):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        key, value = unquote(key), unquote(value)
        if key == "hash":
            hash_value = value
        else:
            params[key] = value

    if hash_value is None:
        raise InitDataError("нет поля hash")

    check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, hash_value):
        raise InitDataError("подпись не совпала")

    # Свежесть — после подписи: auth_date входит в подписанную строку, так что
    # проверять его до подписи значило бы доверять неподтверждённому значению.
    try:
        auth_date = int(params.get("auth_date", 0))
    except (TypeError, ValueError):
        raise InitDataError("некорректный auth_date")
    current = time.time() if now is None else now
    if abs(current - auth_date) > max_age:
        raise InitDataError("initData просрочен")

    try:
        user = json.loads(params.get("user") or "{}")
        user_id = int(user["id"])
    except (ValueError, TypeError, KeyError):
        raise InitDataError("в initData нет корректного user.id")

    # init_hash — чтобы обмен на сессию мог пометить этот initData использованным
    return {**params, "user_id": user_id, "username": user.get("username"), "init_hash": hash_value}


def _dev_stub() -> dict:
    return {"user_id": principals.admin_id() or 0, "username": "dev", "dev": True}


async def verify_auth(request: Request) -> dict:
    """
    Кто пришёл и можно ли ему смотреть. 401 — не удалось удостовериться,
    403 — удостоверились, но доступа нет.
    """
    if auth_disabled("http"):
        return _dev_stub()

    # Сессия (2.8) — основной способ. initData в заголовке пока тоже принимается:
    # выкладка 1 из 3, открытое приложение и закэшированный фронт не ломаются.
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        return await _session_user(authorization[len("Bearer "):].strip())

    init_data = request.headers.get("X-Telegram-Init-Data")
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing Telegram InitData")

    bot_token = env_str("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        logger.error("verify_auth: TELEGRAM_BOT_TOKEN не задан — проверить подпись нечем")
        raise HTTPException(status_code=500, detail="Authentication service unavailable")

    try:
        user = verify_init_data(init_data, bot_token)
    except InitDataError as exc:
        # Причину пишем в лог, наружу — одинаковый ответ: различие между
        # «плохая подпись» и «просрочен» подсказывает атакующему, что менять.
        logger.info("verify_auth: отказ — %s", exc)
        raise HTTPException(status_code=401, detail="Invalid Telegram InitData")

    if not principals.is_allowed(user["user_id"]):
        logger.warning("verify_auth: user_id=%s не в списке допущенных", user["user_id"])
        raise HTTPException(status_code=403, detail="Access denied")

    return user


async def verify_admin(request: Request) -> dict:
    """То же, что verify_auth, плюс требование быть владельцем."""
    user = await verify_auth(request)
    if user.get("dev"):
        return user
    if not principals.is_admin(user["user_id"]):
        logger.warning("verify_admin: user_id=%s — не владелец, запись запрещена", user["user_id"])
        raise HTTPException(status_code=403, detail="Admin access required")
    return user



async def _session_user(token: str) -> dict:
    """Пользователь по токену сессии; допуск перепроверяется на каждом запросе."""
    from api.sessions import SessionUnavailable, get_store, is_session_token
    if not is_session_token(token):
        raise HTTPException(status_code=401, detail="Invalid session")
    try:
        record = await get_store().resolve(token)
    except SessionUnavailable as exc:
        logger.error("verify_auth: хранилище сессий недоступно: %s", exc)
        raise HTTPException(status_code=503, detail="Session service unavailable")
    if record is None:
        raise HTTPException(status_code=401, detail="Invalid session")
    user = {"user_id": int(record["user_id"]), "username": record.get("username"), "session": True}
    if not principals.is_allowed(user["user_id"]):
        # доступ отозван в конфиге — прежняя сессия больше не пускает
        logger.warning("verify_auth: сессия user_id=%s — пользователь больше не допущен", user["user_id"])
        raise HTTPException(status_code=403, detail="Access denied")
    return user


async def verify_admin_fresh(request: Request) -> dict:
    """
    verify_admin плюс initData того же пользователя не старше 10 минут — для
    записи ключей биржи. Даже украденная сессия (или initData, утёкший час назад)
    ключи не меняет: для этого нужно только что открытое приложение владельца.
    """
    user = await verify_admin(request)
    if user.get("dev"):
        return user
    from api.sessions import STEP_UP_MAX_AGE
    try:
        fresh = verify_init_data(request.headers.get("X-Telegram-Init-Data") or "",
                                 env_str("TELEGRAM_BOT_TOKEN"), max_age=STEP_UP_MAX_AGE)
    except InitDataError as exc:
        logger.info("verify_admin_fresh: нет свежего initData — %s", exc)
        raise HTTPException(status_code=403, detail="Fresh Telegram InitData required")
    if fresh["user_id"] != user["user_id"]:
        logger.warning("verify_admin_fresh: initData другого пользователя (%s ≠ %s)", fresh["user_id"], user["user_id"])
        raise HTTPException(status_code=403, detail="Fresh Telegram InitData required")
    return user


def ws_user(init_data: str) -> Optional[dict]:
    """
    Пользователь WebSocket: подпись, свежесть и допуск — или None. Раньше
    проверялись только подпись и свежесть — живой поток позиций и баланса
    получал любой пользователь Telegram. Пользователь нужен и для лимита
    соединений на него (2.7).
    """
    if auth_disabled("ws"):
        return _dev_stub()
    try:
        user = verify_init_data(init_data, env_str("TELEGRAM_BOT_TOKEN"))
    except InitDataError as exc:
        logger.info("verify_ws_token: отказ — %s", exc)
        return None
    if not principals.is_allowed(user["user_id"]):
        logger.warning("verify_ws_token: user_id=%s не в списке допущенных", user["user_id"])
        return None
    return user


def verify_ws_token(init_data: str) -> bool:
    """Проверка initData для WebSocket (см. ws_user)."""
    return ws_user(init_data) is not None


async def ws_user_async(token: str) -> Optional[dict]:
    """Пользователь WebSocket: токен сессии (2.8) или, пока идёт переход, initData."""
    from api.sessions import SessionUnavailable, get_store, is_session_token
    if auth_disabled("ws") or not is_session_token(token):
        return ws_user(token)
    try:
        record = await get_store().resolve(token)
    except SessionUnavailable as exc:
        logger.error("ws: хранилище сессий недоступно: %s", exc)
        return None
    if record is None or not principals.is_allowed(record["user_id"]):
        return None
    return {"user_id": int(record["user_id"]), "username": record.get("username"), "session": True}
