"""
Баланс в Mini App (аудит 10.09.2026, продолжение находки о двух источниках истины).

/api/system/health и поток WebSocket вызывали функцию баланса из базы без
аргумента, а у неё было значение по умолчанию 10 000. При бумажном счёте в 100 $
Mini App показал бы 10 000 $: третья копия константы пережила сведение двух
других в config.py. Теперь оба места берут баланс у capital — он знает режим
(кошелёк в TESTNET/LIVE, бумажный счёт иначе) и стартовый баланс из config.
"""
import inspect

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_api_auth import BOT_TOKEN, OWNER, make_init_data


@pytest.fixture(autouse=True)
def auth_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("ADMIN_CHAT_ID", str(OWNER))
    for name in ("DISABLE_AUTH", "ENVIRONMENT", "ALLOWED_USER_IDS"):
        monkeypatch.delenv(name, raising=False)


def test_db_balance_function_has_no_hidden_default():
    """Вызов без стартового баланса должен падать, а не молча подставлять 10 000."""
    import database
    param = inspect.signature(database.get_current_balance_from_db).parameters["initial_balance"]
    assert param.default is inspect.Parameter.empty


def test_health_reports_balance_from_capital(monkeypatch):
    import capital
    monkeypatch.setattr(capital, "get_current_balance", lambda: 123.45)

    from api.routers import system
    app = FastAPI()
    app.include_router(system.router)

    response = TestClient(app).get(
        "/api/system/health", headers={"X-Telegram-Init-Data": make_init_data(OWNER)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["balance_usdt"] == pytest.approx(123.45)


async def test_live_stream_reports_balance_from_capital(monkeypatch):
    import capital
    import database
    monkeypatch.setattr(capital, "get_current_balance", lambda: 99.5)
    monkeypatch.setattr(database, "get_open_positions", lambda: [])

    from api.routers import ws
    snapshot = await ws._build_snapshot()
    assert snapshot["balance_usdt"] == pytest.approx(99.5)
