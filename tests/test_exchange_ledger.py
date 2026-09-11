"""
Единый журнал в TESTNET/LIVE (10.09.2026, задачи 3.6, 3.8, 3.9) — на временной
SQLite и подменной бирже (tests/fake_bybit.py).
"""
import ast
import pathlib
import time
from datetime import datetime, timedelta, UTC

import pytest

import database
from exchange.bybit_client import BybitUnavailable
from execution import exchange_ledger as ledger
from execution.position_tracker import PositionTracker, TrackedPosition
from tests.fake_bybit import FakeBybit, make_client

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def db(tmp_path, monkeypatch):
    if getattr(database, "_PG_MODE", False):
        pytest.skip("тест для SQLite")
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ledger.db"))
    monkeypatch.setattr(database._thread_local, "conn", None, raising=False)
    yield database
    conn = getattr(database._thread_local, "conn", None)
    if conn is not None:
        conn.close()
    database._thread_local.conn = None


@pytest.fixture
def fake():
    return FakeBybit()


@pytest.fixture
def client(fake):
    return make_client(fake)


@pytest.fixture
def tracker(client):
    return PositionTracker(client=client)


def position(symbol, side="Buy", size="1", price="100", sl="98"):
    return {"symbol": symbol, "side": side, "size": size, "avgPrice": price, "unrealisedPnl": "0",
            "leverage": "5", "stopLoss": sl, "takeProfit": ""}


def closed_pnl(symbol, pnl, exit_price, when_ms):
    return {"symbol": symbol, "closedPnl": str(pnl), "avgExitPrice": str(exit_price), "updatedTime": str(when_ms)}


def now_ms(offset_s=0):
    return int((time.time() + offset_s) * 1000)


def trade_row(trade_id):
    conn = database.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
        return dict(cur.fetchone())
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Сверка при старте
# ---------------------------------------------------------------------------

def test_trade_closed_while_offline_gets_the_exchange_pnl(db, fake, client, tracker):
    tid = db.add_trade("SOLUSDT", "LONG", 100.0, 98.0, 104.0, position_size=10.0, exchange_order_id="oid-1")
    fake.closed_pnl = [closed_pnl("SOLUSDT", -0.42, 97.9, now_ms(+1))]
    report = ledger.reconcile_on_startup(client, tracker)
    row = trade_row(tid)
    assert report.closed == ["SOLUSDT"]
    assert row["status"] == "CLOSED" and row["close_reason"] == "CLOSED_WHILE_OFFLINE"
    assert row["pnl"] == pytest.approx(-0.42) and row["close_price"] == pytest.approx(97.9)
    assert "-0.42" in report.messages[0]
    assert tracker.active_count() == 0


def test_exchange_position_without_a_journal_row_is_adopted(db, fake, client, tracker):
    fake.positions = [position("XRPUSDT", side="Sell", sl="")]
    report = ledger.reconcile_on_startup(client, tracker)
    assert report.adopted == ["XRPUSDT"]
    [trade] = db.get_open_trades()
    assert trade["symbol"] == "XRPUSDT" and trade["side"] == "SHORT"
    assert tracker.active_count() == 1
    assert "СТОПА НА БИРЖЕ НЕТ" in report.messages[0]


def test_matching_position_goes_to_the_tracker_quietly(db, fake, client, tracker):
    db.add_trade("SOLUSDT", "LONG", 100.0, 98.0, 104.0, position_size=10.0, exchange_order_id="oid-1")
    fake.positions = [position("SOLUSDT")]
    report = ledger.reconcile_on_startup(client, tracker)
    assert report.tracked == ["SOLUSDT"] and report.messages == []
    [tracked] = tracker.get_all_active()
    assert tracked.order_id == "oid-1"


def test_reconcile_fails_loudly_when_the_exchange_is_unreachable(db, fake, client, tracker):
    tid = db.add_trade("SOLUSDT", "LONG", 100.0, 98.0, 104.0, position_size=10.0)
    fake.fault("GET", "/v5/position/list", "timeout", times=10)
    with pytest.raises(BybitUnavailable):
        ledger.reconcile_on_startup(client, tracker)
    assert trade_row(tid)["status"] == "OPEN", "без ответа биржи журнал не трогаем"


# ---------------------------------------------------------------------------
# Закрытие по бирже
# ---------------------------------------------------------------------------

def tracked_sol(**kw):
    base = dict(symbol="SOLUSDT", side="LONG", entry_price=100.0, qty=0.1, stop_loss=98.0, take_profit=104.0,
                order_id="oid-1", opened_at=datetime.now(UTC) - timedelta(minutes=5), unrealised_pnl=5.0)
    base.update(kw)
    return TrackedPosition(**base)


def test_close_uses_the_exchange_pnl_not_the_last_poll(db, fake, client):
    tid = db.add_trade("SOLUSDT", "LONG", 100.0, 98.0, 104.0, position_size=10.0, exchange_order_id="oid-1")
    db.open_position("oid-1", "SOLUSDT", "LONG", 0.1, 100.0, 98.0, 104.0)
    fake.closed_pnl = [closed_pnl("SOLUSDT", -0.3, 98.0, now_ms())]
    pnl = ledger.record_close(tracked_sol(), client, sleep=lambda s: None)
    row = trade_row(tid)
    assert pnl == pytest.approx(-0.3)
    assert row["status"] == "CLOSED" and row["close_reason"] == "EXCHANGE_CLOSE"
    assert row["pnl"] == pytest.approx(-0.3)


def test_close_without_exchange_pnl_is_marked_as_estimate(db, fake, client):
    tid = db.add_trade("SOLUSDT", "LONG", 100.0, 98.0, 104.0, position_size=10.0)
    waits = []
    pnl = ledger.record_close(tracked_sol(), client, sleep=waits.append)
    row = trade_row(tid)
    assert pnl == pytest.approx(5.0)
    assert row["close_reason"] == "EXCHANGE_CLOSE_PNL_ESTIMATED"
    assert len(waits) == ledger.CLOSED_PNL_ATTEMPTS - 1


# ---------------------------------------------------------------------------
# Одна позиция на символ
# ---------------------------------------------------------------------------

def test_open_position_reason(db, fake, client, tracker):
    assert ledger.open_position_reason("SOLUSDT", client, tracker) is None

    fake.positions = [position("SOLUSDT")]
    assert "на бирже" in ledger.open_position_reason("SOLUSDT", client, tracker)
    fake.positions = []

    db.add_trade("SOLUSDT", "LONG", 100.0, 98.0, 104.0, position_size=10.0)
    assert "в журнале" in ledger.open_position_reason("SOLUSDT", client, tracker)

    tracker.add(tracked_sol(symbol="XRPUSDT"))
    assert "отслеживается" in ledger.open_position_reason("XRPUSDT", client, tracker)


def test_open_position_reason_refuses_when_the_exchange_is_unreachable(db, fake, client, tracker):
    fake.fault("GET", "/v5/position/list", "timeout", times=10)
    assert "не удалось проверить" in ledger.open_position_reason("SOLUSDT", client, tracker)


# ---------------------------------------------------------------------------
# Проводка по коду
# ---------------------------------------------------------------------------

def _function(path, name):
    text = (ROOT / path).read_text(encoding="utf-8")
    node = next(n for n in ast.walk(ast.parse(text))
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    return ast.get_source_segment(text, node)


def test_no_paper_trade_next_to_a_real_order():
    src = (ROOT / "signal_generator.py").read_text(encoding="utf-8")
    assert src.index("if sends_real_orders():") < src.index("log_demo_trade(\n")


def test_paper_monitor_idles_in_real_orders_mode():
    src = _function("loops/paper_monitor.py", "paper_trading_monitor_loop")
    guard = src.index("if sends_real_orders():")
    assert guard < src.index("await shutdown_evt.wait()") < src.index("while get_state()")


def test_startup_reconciles_and_pauses_on_failure():
    src = _function("runner.py", "main")
    assert "reconcile_on_startup" in src
    assert src.index("reconcile_on_startup") < src.index("pause_trading_manually()") < src.index("# Создаём и отслеживаем все фоновые задачи")


def test_gatekeeper_checks_before_and_records_after_the_order():
    src = _function("execution/gatekeeper.py", "_execute_order")
    assert src.index("open_position_reason(") < src.index("executor.execute(") < src.index("record_open(")
