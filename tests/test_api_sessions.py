"""
Серверная сессия вместо initData на сутки (задача 2.8, пункт 4
docs/DEFERRED_PLAN.md), выкладка 1: API принимает и сессию, и initData, как
раньше. Redis подменён, часы сессий — управляемые; initData подписывается тем же
алгоритмом, что у Telegram (tests/test_api_auth.py).
"""
import asyncio
import pathlib
import time

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from api import sessions
from tests.test_api_auth import BOT_TOKEN, FRIEND, OWNER, STRANGER, headers_for, make_init_data

ROOT = pathlib.Path(__file__).resolve().parent.parent


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


class FakeRedis:
    """set/get/expire/delete с истечением по управляемым часам."""

    def __init__(self, clock):
        self.data = {}
        self.clock = clock

    def _live(self, key):
        item = self.data.get(key)
        if item and item[1] is not None and item[1] <= self.clock():
            del self.data[key]
            return None
        return item

    async def set(self, key, value, ex=None, nx=False):
        if nx and self._live(key):
            return None
        self.data[key] = (value, self.clock() + ex if ex else None)
        return True

    async def get(self, key):
        item = self._live(key)
        return item[0] if item else None

    async def expire(self, key, seconds):
        item = self._live(key)
        if not item:
            return False
        self.data[key] = (item[0], self.clock() + seconds)
        return True

    async def delete(self, key):
        self.data.pop(key, None)
        return 1


class BrokenRedis:
    async def _down(self, *args, **kwargs):
        raise ConnectionError("redis down")

    set = get = expire = delete = _down


@pytest.fixture(autouse=True)
def auth_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("ADMIN_CHAT_ID", str(OWNER))
    monkeypatch.setenv("ALLOWED_USER_IDS", str(FRIEND))
    for name in ("DISABLE_AUTH", "DISABLE_WS_AUTH", "ENVIRONMENT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def redis(clock):
    fake = FakeRedis(clock)
    sessions.set_store(sessions.SessionStore(fake, now=clock))
    yield fake
    sessions.set_store(None)


@pytest.fixture
def client(redis):
    from api.deps import verify_admin_fresh, verify_auth
    from api.routers import auth

    app = FastAPI()
    app.include_router(auth.router)

    @app.get("/read")
    async def read(user=Depends(verify_auth)):
        return {"user_id": user["user_id"], "session": bool(user.get("session"))}

    @app.put("/keys")
    async def keys(user=Depends(verify_admin_fresh)):
        return {"ok": True}

    return TestClient(app)


def exchange(client, user_id=OWNER, **kw):
    return client.post("/api/auth/session", headers=headers_for(user_id, **kw))


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Обмен
# ---------------------------------------------------------------------------

def test_exchange_gives_a_session_that_opens_the_api(client):
    response = exchange(client)
    assert response.status_code == 200
    token = response.json()["token"]
    assert token.startswith("s1.") and response.json()["expires_in"] == sessions.IDLE_SECONDS
    read = client.get("/read", headers=bearer(token))
    assert read.status_code == 200 and read.json() == {"user_id": OWNER, "session": True}


def test_the_same_init_data_is_exchanged_only_once(client):
    headers = headers_for(OWNER)
    assert client.post("/api/auth/session", headers=headers).status_code == 200
    assert client.post("/api/auth/session", headers=headers).status_code == 401


def test_init_data_older_than_an_hour_is_not_exchanged(client):
    old = time.time() - sessions.EXCHANGE_MAX_AGE - 60
    assert exchange(client, auth_date=old).status_code == 401


def test_stranger_and_forged_init_data_get_no_session(client):
    assert exchange(client, STRANGER).status_code == 403
    assert exchange(client, tamper=True).status_code == 401
    assert client.post("/api/auth/session").status_code == 401


# ---------------------------------------------------------------------------
# Жизнь сессии
# ---------------------------------------------------------------------------

def test_session_outlives_the_init_data_exchange_window(client, clock):
    token = exchange(client).json()["token"]
    clock.now += 2 * 3600
    assert client.get("/read", headers=bearer(token)).status_code == 200


def test_idle_session_expires(client, clock):
    token = exchange(client).json()["token"]
    clock.now += sessions.IDLE_SECONDS + 1
    assert client.get("/read", headers=bearer(token)).status_code == 401


def test_active_session_ends_at_the_absolute_limit(client, clock):
    token = exchange(client).json()["token"]
    for _ in range(2):
        clock.now += 11 * 3600
        assert client.get("/read", headers=bearer(token)).status_code == 200
    clock.now += 3 * 3600  # 25 ч от выдачи
    assert client.get("/read", headers=bearer(token)).status_code == 401


@pytest.mark.parametrize("value", ["s1.unknown", "not-a-session", "s1."])
def test_unknown_session_is_401(client, value):
    assert client.get("/read", headers=bearer(value)).status_code == 401


def test_access_revoked_in_config_ends_the_session(client, monkeypatch):
    token = exchange(client, FRIEND).json()["token"]
    assert client.get("/read", headers=bearer(token)).status_code == 200
    monkeypatch.setenv("ALLOWED_USER_IDS", "")
    assert client.get("/read", headers=bearer(token)).status_code == 403


def test_redis_keeps_only_a_digest_of_the_token(client, redis):
    token = exchange(client).json()["token"]
    stored = " ".join(f"{key} {value[0]}" for key, value in redis.data.items())
    assert token not in stored and token[3:] not in stored


# ---------------------------------------------------------------------------
# Переход и сбои
# ---------------------------------------------------------------------------

def test_init_data_header_still_works_during_the_transition(client):
    """Выкладка 1 из 3: открытое приложение и закэшированный фронт не ломаются."""
    assert client.get("/read", headers=headers_for(OWNER)).json() == {"user_id": OWNER, "session": False}


def test_redis_down_means_503_for_sessions_while_init_data_still_works(client):
    sessions.set_store(sessions.SessionStore(BrokenRedis()))
    assert exchange(client).status_code == 503
    assert client.get("/read", headers=bearer("s1.whatever")).status_code == 503
    assert client.get("/read", headers=headers_for(OWNER)).status_code == 200


# ---------------------------------------------------------------------------
# Запись ключей биржи
# ---------------------------------------------------------------------------

def test_writing_exchange_keys_needs_fresh_init_data_of_the_same_owner(client):
    token = exchange(client).json()["token"]
    assert client.put("/keys", headers=bearer(token)).status_code == 403, "одной сессии мало"
    fresh = {**bearer(token), **headers_for(OWNER)}
    assert client.put("/keys", headers=fresh).status_code == 200
    stale = {**bearer(token), **headers_for(OWNER, auth_date=time.time() - sessions.STEP_UP_MAX_AGE - 60)}
    assert client.put("/keys", headers=stale).status_code == 403
    someone_else = {**bearer(token), **headers_for(FRIEND)}
    assert client.put("/keys", headers=someone_else).status_code == 403


def test_owner_with_fresh_init_data_alone_can_still_write_keys(client):
    """Как до сих пор: заголовок initData, открытый только что."""
    assert client.put("/keys", headers=headers_for(OWNER)).status_code == 200


# ---------------------------------------------------------------------------
# WebSocket и подключение
# ---------------------------------------------------------------------------

def test_websocket_accepts_a_session_and_still_init_data(redis):
    from api.deps import ws_user_async
    token, _ = asyncio.run(sessions.get_store().create(OWNER, "owner"))
    assert asyncio.run(ws_user_async(token))["user_id"] == OWNER
    assert asyncio.run(ws_user_async("s1.nope")) is None
    assert asyncio.run(ws_user_async(make_init_data(OWNER)))["user_id"] == OWNER


def test_api_serves_the_exchange_and_allows_the_header():
    text = (ROOT / "api" / "main.py").read_text(encoding="utf-8")
    assert "app.include_router(auth.router)" in text
    assert '"Authorization"' in text
    ws = (ROOT / "api" / "routers" / "ws.py").read_text(encoding="utf-8")
    assert "await ws_user_async(token)" in ws
    keys = (ROOT / "api" / "routers" / "settings.py").read_text(encoding="utf-8")
    assert "store_keys(body: StoreKeysRequest, user=Depends(verify_admin_fresh))" in keys
