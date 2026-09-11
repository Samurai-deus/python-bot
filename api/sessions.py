"""
Серверная сессия Mini App вместо initData на сутки (задача 2.8, пункт 4
docs/DEFERRED_PLAN.md).

initData выдаётся при открытии Mini App и без переоткрытия не обновляется, поэтому
API принимал его сутки — и тот же initData давал права владельца: утёкший initData
означал сутки записи ключей биржи. Теперь initData обменивается на сессию:
- только в первый час после открытия (auth_date) и однократно: hash initData
  помечается в Redis, повторный обмен отвергается;
- сессия — непрозрачный токен «s1.…»; в Redis лежит sha256 токена, а не он сам,
  так что дамп Redis не даёт готовых токенов;
- скользящее окно 12 ч без активности и абсолютный предел 24 ч от выдачи.

Redis без персистентности: после его перезапуска сессии теряются, и Mini App
нужно открыть заново — так же, как при истечении initData сейчас.
"""
import hashlib
import json
import logging
import os
import secrets
import time
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "s1."
EXCHANGE_MAX_AGE = 3600       # initData обменивается на сессию не позже часа после открытия
IDLE_SECONDS = 12 * 3600      # сессия без активности
ABSOLUTE_SECONDS = 24 * 3600  # сессия от выдачи, как прежнее окно initData
STEP_UP_MAX_AGE = 600         # свежий initData для записи ключей биржи

_SESSION_KEY = "session:"
_USED_KEY = "initdata-used:"


class SessionUnavailable(Exception):
    """Хранилище сессий недоступно."""


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def is_session_token(value: Optional[str]) -> bool:
    return bool(value) and value.startswith(TOKEN_PREFIX)


class SessionStore:
    def __init__(self, client, now: Callable[[], float] = time.time):
        self._client = client
        self._now = now

    async def claim_init_data(self, init_hash: str) -> bool:
        """Пометить initData использованным. False — его уже обменивали."""
        try:
            claimed = await self._client.set(_USED_KEY + _digest(init_hash), "1", nx=True, ex=EXCHANGE_MAX_AGE)
        except Exception as exc:
            raise SessionUnavailable(f"{type(exc).__name__}: {exc}") from exc
        return bool(claimed)

    async def create(self, user_id: int, username: Optional[str]) -> Tuple[str, int]:
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        record = json.dumps({"user_id": int(user_id), "username": username, "created": self._now()})
        try:
            await self._client.set(_SESSION_KEY + _digest(token), record, ex=IDLE_SECONDS)
        except Exception as exc:
            raise SessionUnavailable(f"{type(exc).__name__}: {exc}") from exc
        return token, IDLE_SECONDS

    async def resolve(self, token: str) -> Optional[dict]:
        """Запись сессии или None (нет, истекла, испорчена). Продлевает окно без активности."""
        if not is_session_token(token):
            return None
        key = _SESSION_KEY + _digest(token)
        try:
            raw = await self._client.get(key)
        except Exception as exc:
            raise SessionUnavailable(f"{type(exc).__name__}: {exc}") from exc
        if raw is None:
            return None
        try:
            record = json.loads(raw)
            age = self._now() - float(record["created"])
            int(record["user_id"])
        except (ValueError, KeyError, TypeError):
            logger.warning("sessions: испорченная запись сессии — удаляю")
            await self._delete(key)
            return None
        if age > ABSOLUTE_SECONDS:
            await self._delete(key)
            return None
        try:
            await self._client.expire(key, max(1, int(min(IDLE_SECONDS, ABSOLUTE_SECONDS - age))))
        except Exception as exc:
            raise SessionUnavailable(f"{type(exc).__name__}: {exc}") from exc
        return record

    async def revoke(self, token: str) -> None:
        if is_session_token(token):
            await self._delete(_SESSION_KEY + _digest(token))

    async def _delete(self, key: str) -> None:
        try:
            await self._client.delete(key)
        except Exception as exc:
            raise SessionUnavailable(f"{type(exc).__name__}: {exc}") from exc


_store: Optional[SessionStore] = None


def get_store() -> SessionStore:
    """Хранилище на Redis из REDIS_URL — тот же, что у лимита запросов."""
    global _store
    if _store is None:
        import redis.asyncio as aioredis
        url = os.environ.get("REDIS_URL", "redis://redis:6379")
        _store = SessionStore(aioredis.from_url(url, decode_responses=True, socket_connect_timeout=2))
    return _store


def set_store(store: Optional[SessionStore]) -> None:
    """Подменить хранилище (тесты) или сбросить его."""
    global _store
    _store = store
