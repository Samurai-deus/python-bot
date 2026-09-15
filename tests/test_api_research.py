"""
/api/research/overview — снимок программы для мини-аппа: читается из баз исполнителей (только
чтение), журнала записи и базы бота; отсутствующая база — null, а не 500; авторизация та же.
"""
import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
from api import research_data as rd
from carry.store import Store as CarryStore
from portfolio.store import Store as PortfolioStore
from tests.test_api_auth import BOT_TOKEN, OWNER, make_init_data

NOW = 1_789_400_000_000


@pytest.fixture
def env(tmp_path, monkeypatch):
    if getattr(database, "_PG_MODE", False):
        pytest.skip("тест для SQLite")
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "bot.db"))
    monkeypatch.setattr(database._thread_local, "conn", None, raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("ADMIN_CHAT_ID", str(OWNER))
    for name in ("DISABLE_AUTH", "ENVIRONMENT", "ALLOWED_USER_IDS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RESEARCH_PORTFOLIO_DB", str(tmp_path / "portfolio.db"))
    monkeypatch.setenv("RESEARCH_CARRY_DB", str(tmp_path / "carry.db"))
    monkeypatch.setenv("RESEARCH_RECORDER_DIR", str(tmp_path / "recorder"))
    monkeypatch.setenv("PORTFOLIO_CAPITAL_USDT", "1000")
    monkeypatch.setenv("RESEARCH_BTCALTS_DB", str(tmp_path / "btcalts.db"))
    monkeypatch.setenv("BTCALTS_CAPITAL_USDT", "5000")
    yield tmp_path
    conn = getattr(database._thread_local, "conn", None)
    if conn is not None:
        conn.close()
    database._thread_local.conn = None


def fill_portfolio(path):
    s = PortfolioStore(str(path))
    s.set("started_at", NOW - 3 * rd.DAY_MS)
    s.set("start_equity", 1000.0)
    s.set("last_rebalance_t", NOW - 3 * rd.DAY_MS)
    s.rebalance(NOW - 3 * rd.DAY_MS, NOW - 3 * rd.DAY_MS + 60_000, {"AUSDT": 0.1, "BUSDT": -0.1}, [["AUSDT", "Buy", "1"]], [])
    s.snapshot(NOW - 2 * rd.DAY_MS, 1010.0, {"AUSDT": 101.0, "BUSDT": -99.0})
    s.snapshot(NOW - rd.DAY_MS, 990.0, {"AUSDT": 100.0, "BUSDT": -100.0})
    s.event(NOW - 3 * rd.DAY_MS, "start", "")
    s.conn.close()


def fill_carry(path):
    s = CarryStore(str(path))
    s.set("opened_at", NOW - 5 * rd.DAY_MS)
    s.set("start_equity", 40_000.0)
    s.add_funding([{"id": "f1", "symbol": "BTCUSDT", "change": "3.5", "transactionTime": str(NOW - rd.DAY_MS)},
                   {"id": "f0", "symbol": "BTCUSDT", "change": "9.9", "transactionTime": str(NOW - 9 * rd.DAY_MS)}])
    s.add_fills("spot", [{"execId": "e1", "symbol": "BTCUSDT", "side": "Buy", "execQty": "0.1", "execPrice": "80000",
                          "execFee": "0.0001", "feeCurrency": "BTC", "execTime": str(NOW - 5 * rd.DAY_MS)}])
    s.snapshot(NOW - rd.DAY_MS, 40_010.0, 0.004, {"BTCUSDT": 0.001, "ETHUSDT": 0.06},
               {"BTCUSDT": {"spot": 0.128, "short": 0.128, "price": 80_000.0}})
    s.conn.close()


def fill_recorder(root):
    root.mkdir()
    (root / "events.jsonl").write_text(
        json.dumps({"t": NOW - 2 * rd.DAY_MS, "event": "connect", "symbols": ["BTCUSDT"]}) + "\n"
        + json.dumps({"t": NOW - rd.DAY_MS, "event": "disconnect", "reason": "closed"}) + "\n"
        + json.dumps({"t": NOW - rd.DAY_MS + 2000, "event": "connect", "symbols": ["BTCUSDT"]}) + "\n", encoding="utf-8")
    (root / "heartbeat").write_text(f"{NOW / 1000 - 30:.0f}\n", encoding="utf-8")
    d = root / "book" / "BTCUSDT" / "2026-09-14"
    d.mkdir(parents=True)
    (d / "06.jsonl.gz").write_bytes(b"x" * 2_000_000)


def test_portfolio_summary_from_executor_db(env):
    fill_portfolio(env / "portfolio.db")
    p = rd.read_portfolio(str(env / "portfolio.db"), 1000.0, NOW)
    assert p["status"] == "running" and p["change"] == pytest.approx(-10.0)
    assert p["drawdown"] == pytest.approx(20.0), "пик 1010 → 990"
    assert p["gross"] == pytest.approx(200.0)
    assert p["positions"][0] == {"symbol": "AUSDT", "side": "LONG", "notional": 100.0, "weight": pytest.approx(0.1)}
    assert p["positions"][1]["side"] == "SHORT"
    assert p["next_rebalance"] == rd._iso(NOW + 4 * rd.DAY_MS)
    assert p["rebalances"] == [{"t": rd._iso(NOW - 3 * rd.DAY_MS), "done_at": rd._iso(NOW - 3 * rd.DAY_MS + 60_000),
                                "coins": 2, "orders": 1, "failed": 0, "runs": 1}]


def test_carry_summary_counts_funding_only_since_opening(env):
    fill_carry(env / "carry.db")
    c = rd.read_carry(str(env / "carry.db"), NOW)
    assert c["status"] == "running" and c["funding"] == pytest.approx(3.5), "начисление до открытия не в счёт"
    assert c["fees"] == pytest.approx(0.0001 * 80_000)
    assert c["change"] == pytest.approx(10.0) and c["outside_share"] == pytest.approx(0.5)
    assert c["positions"] == [{"symbol": "BTCUSDT", "spot": 0.128, "short": 0.128, "price": 80_000.0,
                               "notional": pytest.approx(10_240.0), "deviation": 0.001}]


def test_carry_db_without_positions_column_still_reads(env):
    p = env / "carry.db"
    c = sqlite3.connect(str(p))
    c.execute("CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("CREATE TABLE snapshots (ts INTEGER PRIMARY KEY, equity REAL, mm_rate REAL, deviations TEXT)")
    c.execute("CREATE TABLE funding (id TEXT PRIMARY KEY, symbol TEXT, change REAL, funding REAL, ts INTEGER)")
    c.execute("CREATE TABLE fills (exec_id TEXT PRIMARY KEY, category TEXT, symbol TEXT, side TEXT, qty REAL, price REAL, fee REAL, fee_coin TEXT, ts INTEGER)")
    c.execute("CREATE TABLE events (ts INTEGER, kind TEXT, detail TEXT)")
    c.execute("INSERT INTO snapshots VALUES (1, 5.0, 0.1, '{}')")
    c.commit()
    c.close()
    assert rd.read_carry(str(p), NOW)["positions"] == []


def test_recorder_summary(env):
    fill_recorder(env / "recorder")
    r = rd.read_recorder(str(env / "recorder"), NOW)
    assert r["events"] == {"connect": 2, "disconnect": 1} and r["gaps"] == 0
    assert r["size_mb"] == pytest.approx(2.0) and r["mb_per_day"] == pytest.approx(1.0)
    assert r["last_message_age_s"] == pytest.approx(30.0, abs=1)


def test_missing_sources_are_null_not_errors(env):
    assert rd.read_portfolio(str(env / "nope.db"), 1000.0, NOW) is None
    assert rd.read_carry(str(env / "nope.db"), NOW) is None
    assert rd.read_recorder(str(env / "nope"), NOW) is None


def test_overview_endpoint_requires_auth_and_returns_all_blocks(env):
    fill_portfolio(env / "portfolio.db")
    from api.routers import research
    app = FastAPI()
    app.include_router(research.router)
    client = TestClient(app)
    assert client.get("/api/research/overview").status_code == 401
    r = client.get("/api/research/overview", headers={"X-Telegram-Init-Data": make_init_data(OWNER)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["portfolio"]["status"] == "running" and body["carry"] is None and body["recorder"] is None
    assert body["news"]["items"] == 0 and body["program"]["portfolio"]["verdict"] == "2026-12-14"


def test_btcalts_and_calendar_in_overview(env):
    """И18 читается той же схемой, что И14 (капитал 5 000); календарь — даты плана с днями до события."""
    fill_portfolio(env / "btcalts.db")
    o = rd.overview(NOW / 1000)
    assert o["btcalts"]["capital"] == 5000.0 and o["btcalts"]["status"] == "running" and len(o["btcalts"]["positions"]) == 2
    assert o["portfolio"] is None, "база И14 в этом тесте не создана — null, не 500"
    cal = o["calendar"]
    assert [c["date"] for c in cal][:2] == ["2026-09-21", "2026-10-11"] and all("text" in c for c in cal)
    first = next(c for c in cal if c["date"] == "2026-09-21")
    assert first["days_left"] == (__import__("datetime").date(2026, 9, 21) - __import__("datetime").datetime.fromtimestamp(NOW / 1000, __import__("datetime").UTC).date()).days
    assert o["program"]["btcalts"]["verdict"] == "2027-03-16"


def test_reruns_are_counted_from_the_run_log(env):
    """Второй прогон недели (14.09) виден как runs = 2, запись в rebalances — последняя."""
    fill_portfolio(env / "portfolio.db")
    s = PortfolioStore(str(env / "portfolio.db"))
    s.rebalance(NOW - 3 * rd.DAY_MS, NOW - 3 * rd.DAY_MS + 7_200_000, {"AUSDT": 0.2}, [["AUSDT", "Buy", "2"]], [])
    s.conn.close()
    p = rd.read_portfolio(str(env / "portfolio.db"), 1000.0, NOW)
    assert [r["runs"] for r in p["rebalances"]] == [2] and p["rebalances"][0]["coins"] == 1
