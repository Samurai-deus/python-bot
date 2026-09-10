"""
Плечо в бумажном учёте (3.4+, 10.09.2026) — на настоящей SQLite во временном файле.

Было: бумажный режим вычитал из свободного капитала весь номинал открытых
позиций, как будто они без плеча, а реальный (totalAvailableBalance) — только
маржу. Теперь бумажный свободный капитал — капитал минус маржа (номинал / плечо).
Номинал для экспозиции и пределов Risk Core не меняется.
"""
import pytest

import capital
import database
from database import PARTIAL_CLOSE_FRACTION


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


@pytest.fixture
def paper(monkeypatch):
    for name in ("LIVE_TRADING", "BYBIT_TESTNET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PAPER_TRADING", "true")


def test_open_margin_divides_notional_by_leverage(db):
    db.add_trade("SOLUSDT", "LONG", 100.0, 98.0, 104.0, position_size=30.0, leverage=3.0)
    db.add_trade("XRPUSDT", "SHORT", 2.0, 2.05, 1.9, position_size=6.0)  # плечо не записано — как 1
    db.add_trade("ETHUSDT", "LONG", 2000.0, 1980.0, 2040.0, position_size=5.0, leverage=0.5)  # меньше 1 — как 1
    assert db.get_open_margin() == pytest.approx(10.0 + 6.0 + 5.0)
    assert db.get_total_open_positions_size() == pytest.approx(41.0), "номинал для экспозиции прежний"


def test_open_margin_counts_the_remainder_after_partial_close(db):
    tid = db.add_trade("SOLUSDT", "LONG", 100.0, 98.0, 104.0, position_size=30.0, leverage=3.0)
    db.update_trade_partial(tid, 103.0, 0.7)
    assert db.get_open_margin() == pytest.approx(30.0 * (1 - PARTIAL_CLOSE_FRACTION) / 3.0)


def test_paper_available_capital_subtracts_margin_not_notional(db, paper):
    db.add_trade("SOLUSDT", "LONG", 100.0, 98.0, 104.0, position_size=30.0, leverage=3.0)
    equity = db.get_current_balance_from_db(capital.INITIAL_BALANCE)
    assert capital.get_available_capital() == pytest.approx(equity - 10.0)
