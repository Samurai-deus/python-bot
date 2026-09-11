"""
Частичное закрытие в учёте (10.09.2026) — на настоящей SQLite во временном файле.

Было: после частичного тейка занятый капитал, экспозиция и позиции в Mini App
считались по полному размеру, а частичный PnL доходил до баланса только с
финальным закрытием.
"""
import pathlib

import pytest

import database
from database import PARTIAL_CLOSE_FRACTION, open_notional

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def db(tmp_path, monkeypatch):
    if getattr(database, "_PG_MODE", False):
        pytest.skip("тест для SQLite")
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "trades.db"))
    monkeypatch.setattr(database._thread_local, "conn", None, raising=False)
    yield database
    conn = getattr(database._thread_local, "conn", None)
    if conn is not None:
        conn.close()
    database._thread_local.conn = None


def test_open_notional_is_the_remainder_after_partial():
    assert open_notional({"position_size": 10.0}) == 10.0
    assert open_notional({"position_size": 10.0, "partial_closed": 1}) == pytest.approx(10.0 * (1 - PARTIAL_CLOSE_FRACTION))
    assert open_notional({"position_size": None}) == 0.0


def test_locked_capital_counts_only_the_remainder(db):
    tid = db.add_trade("SOLUSDT", "LONG", 100.0, 98.0, 104.0, position_size=10.0)
    db.add_trade("XRPUSDT", "SHORT", 2.0, 2.05, 1.9, position_size=6.0)
    assert db.get_total_open_positions_size() == pytest.approx(16.0)
    db.update_trade_partial(tid, 103.0, 0.7)
    assert db.get_total_open_positions_size() == pytest.approx(5.0 + 6.0)


def test_partial_profit_reaches_the_balance_now_and_only_once(db):
    tid = db.add_trade("SOLUSDT", "LONG", 100.0, 98.0, 104.0, position_size=10.0)
    assert db.get_current_balance_from_db(100.0) == pytest.approx(100.0)
    db.update_trade_partial(tid, 103.0, 0.7)
    assert db.get_current_balance_from_db(100.0) == pytest.approx(100.7), "частичный тейк — уже полученные деньги"
    # финальный pnl включает частичный (trade_manager складывает их)
    assert db.close_trade(tid, 104.0, "TAKE_PROFIT", 1.9)
    assert db.get_current_balance_from_db(100.0) == pytest.approx(101.9), "частичный PnL не должен посчитаться дважды"


def test_open_positions_show_remaining_qty(db):
    tid = db.add_trade("SOLUSDT", "LONG", 100.0, 98.0, 104.0, position_size=10.0)
    db.update_trade_partial(tid, 103.0, 0.7)
    [pos] = db.get_open_positions()
    assert pos["qty"] == pytest.approx(0.05)


@pytest.mark.parametrize("path", [
    "execution/gatekeeper.py", "brains/risk_exposure_brain.py", "core/portfolio_brain.py",
])
def test_exposure_consumers_use_the_remainder(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    assert "open_notional(trade)" in text
    assert 'float(trade.get("position_size", 0))' not in text
    assert 'sum(trade.get("position_size", 0)' not in text


def test_paper_monitor_keeps_the_database_off_the_event_loop():
    import ast
    text = (ROOT / "loops" / "paper_monitor.py").read_text(encoding="utf-8")
    node = next(n for n in ast.walk(ast.parse(text))
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "paper_trading_monitor_loop")
    src = ast.get_source_segment(text, node)
    assert "await asyncio.to_thread(get_open_trades)" in src
    assert "await asyncio.to_thread(\n                                check_trades" in src
