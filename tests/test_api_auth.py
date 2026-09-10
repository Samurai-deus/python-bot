"""
Аутентификация и авторизация API (аудит 10.09.2026: B-5, C-6, H-9, H-13).

Роуты проверяются на маленьком приложении с теми же зависимостями verify_auth
и verify_admin, что у боевых роутов, — без базы и без Redis. initData
подписывается здесь же тем же алгоритмом, что у Telegram.
"""
import hashlib
import hmac
import json
import time
from urllib.parse import quote

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

BOT_TOKEN = "123456:TEST-token-used-only-for-hmac"
OWNER = 1001
FRIEND = 2002
STRANGER = 3003


def make_init_data(user_id, bot_token=BOT_TOKEN, auth_date=None, tamper=False):
    params = {
        "auth_date": str(int(time.time() if auth_date is None else auth_date)),
        "query_id": "AAHtest",
        "user": json.dumps({"id": user_id, "first_name": "T"}, separators=(",", ":")),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if tamper:
        digest = ("0" if digest[0] != "0" else "1") + digest[1:]
    fields = {**params, "hash": digest}
    return "&".join(f"{k}={quote(v, safe='')}" for k, v in fields.items())


def headers_for(user_id, **kw):
    return {"X-Telegram-Init-Data": make_init_data(user_id, **kw)}


@pytest.fixture(autouse=True)
def auth_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("ADMIN_CHAT_ID", str(OWNER))
    monkeypatch.setenv("ALLOWED_USER_IDS", str(FRIEND))
    for name in ("DISABLE_AUTH", "DISABLE_WS_AUTH", "ENVIRONMENT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def client():
    from api.deps import verify_admin, verify_auth

    app = FastAPI()

    @app.get("/read")
    async def read(user=Depends(verify_auth)):
        return {"user_id": user["user_id"]}

    @app.put("/write")
    async def write(user=Depends(verify_admin)):
        return {"ok": True}

    return TestClient(app)


# ---------------------------------------------------------------------------
# Подпись и свежесть — это работало и раньше, проверяем, что не сломали
# ---------------------------------------------------------------------------

def test_missing_init_data_is_401(client):
    assert client.get("/read").status_code == 401


def test_tampered_signature_is_401(client):
    assert client.get("/read", headers=headers_for(OWNER, tamper=True)).status_code == 401


def test_signed_with_other_bot_token_is_401(client):
    h = headers_for(OWNER, bot_token="999:other-bot")
    assert client.get("/read", headers=h).status_code == 401


def test_expired_init_data_is_401(client):
    h = headers_for(OWNER, auth_date=time.time() - 3 * 86400)
    assert client.get("/read", headers=h).status_code == 401


def test_rejections_look_the_same_from_outside(client):
    """Разная причина отказа не должна подсказывать атакующему, что менять."""
    bad_sig = client.get("/read", headers=headers_for(OWNER, tamper=True)).json()
    expired = client.get("/read", headers=headers_for(OWNER, auth_date=0)).json()
    assert bad_sig == expired


# ---------------------------------------------------------------------------
# Кто пришёл — новое
# ---------------------------------------------------------------------------

def test_stranger_with_valid_signature_cannot_read(client):
    """
    C-6: валидную подпись Telegram выдаёт ЛЮБОМУ, кто открыл Mini App бота.
    Раньше этого хватало, чтобы видеть баланс, позиции и историю сделок.
    """
    r = client.get("/read", headers=headers_for(STRANGER))
    assert r.status_code == 403


def test_observer_can_read_but_not_write(client):
    assert client.get("/read", headers=headers_for(FRIEND)).status_code == 200
    assert client.put("/write", headers=headers_for(FRIEND)).status_code == 403


def test_owner_can_read_and_write(client):
    r = client.get("/read", headers=headers_for(OWNER))
    assert r.status_code == 200
    assert r.json() == {"user_id": OWNER}
    assert client.put("/write", headers=headers_for(OWNER)).status_code == 200


@pytest.mark.parametrize("admin_value", ["", None])
def test_empty_admin_config_denies_write_to_everyone(client, monkeypatch, admin_value):
    """
    B-5: при пустом ADMIN_CHAT_ID раньше любой вошедший становился админом
    и мог подменить ключи Bybit через PUT /api/settings/keys.
    """
    if admin_value is None:
        monkeypatch.delenv("ADMIN_CHAT_ID")
    else:
        monkeypatch.setenv("ADMIN_CHAT_ID", admin_value)
    for uid in (OWNER, FRIEND, STRANGER):
        assert client.put("/write", headers=headers_for(uid)).status_code == 403
    assert client.get("/read", headers=headers_for(STRANGER)).status_code == 403


# ---------------------------------------------------------------------------
# DISABLE_AUTH — H-9
# ---------------------------------------------------------------------------

def test_disable_auth_ignored_without_dev_environment(client, monkeypatch):
    """
    DISABLE_AUTH=true, скопированный в прод-.env из примера в CLAUDE.md,
    раньше открывал все роуты. Теперь без ENVIRONMENT из dev-списка он
    игнорируется — и незаданный ENVIRONMENT тоже считается боевым.
    """
    monkeypatch.setenv("DISABLE_AUTH", "true")
    assert client.get("/read").status_code == 401
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert client.get("/read").status_code == 401


def test_disable_auth_works_in_test_environment(client, monkeypatch):
    monkeypatch.setenv("DISABLE_AUTH", "true")
    monkeypatch.setenv("ENVIRONMENT", "test")
    assert client.get("/read").status_code == 200


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

def test_ws_token_requires_allowed_user():
    """Раньше живой поток позиций и баланса получал любой пользователь Telegram."""
    from api.deps import verify_ws_token
    assert verify_ws_token(make_init_data(OWNER)) is True
    assert verify_ws_token(make_init_data(FRIEND)) is True
    assert verify_ws_token(make_init_data(STRANGER)) is False
    assert verify_ws_token(make_init_data(OWNER, tamper=True)) is False
    assert verify_ws_token("") is False


# ---------------------------------------------------------------------------
# Отказ стартовать с опасной конфигурацией
# ---------------------------------------------------------------------------

def test_production_refuses_disable_auth(monkeypatch):
    from api.main import enforce_security_config
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DISABLE_AUTH", "true")
    with pytest.raises(RuntimeError, match="DISABLE_AUTH"):
        enforce_security_config()


def test_production_refuses_missing_admin(monkeypatch):
    from api.main import enforce_security_config
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ADMIN_CHAT_ID")
    with pytest.raises(RuntimeError, match="ADMIN_CHAT_ID"):
        enforce_security_config()


def test_production_refuses_missing_bot_token(monkeypatch):
    from api.main import enforce_security_config
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        enforce_security_config()


def test_production_with_sane_config_starts(monkeypatch):
    from api.main import enforce_security_config
    monkeypatch.setenv("ENVIRONMENT", "production")
    enforce_security_config()


def test_production_hides_api_schema(monkeypatch):
    from api.main import create_app
    monkeypatch.setenv("ENVIRONMENT", "production")
    app = create_app()
    assert app.docs_url is None and app.openapi_url is None


# ---------------------------------------------------------------------------
# Лимитер при отказе Redis — H-13
# ---------------------------------------------------------------------------

class _FailingPipe:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, *a):
        pass

    def incr(self, *a):
        pass

    async def execute(self):
        raise ConnectionError("redis down")


class _FailingRedis:
    def pipeline(self, transaction=False):
        return _FailingPipe()


def _rate_limited_app():
    from api.rate_limit import RateLimitMiddleware
    app = FastAPI()

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    app.add_middleware(RateLimitMiddleware, client=_FailingRedis())
    return TestClient(app)


def test_rate_limiter_fails_closed_in_production(monkeypatch):
    """Раньше отказ Redis молча выключал лимит — в бою теперь 503."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert _rate_limited_app().get("/ping").status_code == 503


def test_rate_limiter_fails_open_outside_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    assert _rate_limited_app().get("/ping").status_code == 200
