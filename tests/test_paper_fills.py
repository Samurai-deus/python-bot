"""
Бумажные стоп и тейк-профит (10.09.2026).

Было: стоп определялся по минимуму свечи и исполнялся по нему же — убыток больше,
чем допустил бы стоп на бирже; тейк тенью не засчитывался вовсе; минимум,
случившийся до подтягивания трейлинга, закрывал сделку задним числом.
"""
from datetime import datetime, UTC
from types import SimpleNamespace

import pytest

import trade_manager
from paper_fills import CandleWatermark

SLIP = 0.0005  # PAPER_STOP_SLIPPAGE_PCT=0.05


@pytest.fixture
def book(monkeypatch):
    monkeypatch.setenv("PAPER_TAKER_FEE_PCT", "0.055")
    monkeypatch.setenv("PAPER_STOP_SLIPPAGE_PCT", "0.05")
    trades, closed = [], []
    monkeypatch.setattr(trade_manager, "get_open_trades", lambda: [dict(t) for t in trades])

    def close(trade_id, price, reason, pnl):
        closed.append(SimpleNamespace(id=trade_id, price=price, reason=reason, pnl=pnl))
        return True

    monkeypatch.setattr(trade_manager, "db_close_trade", close)
    monkeypatch.setattr(trade_manager, "update_trade_stop", lambda *a, **k: None)
    monkeypatch.setattr(trade_manager, "update_trade_partial", lambda *a, **k: None)
    return SimpleNamespace(trades=trades, closed=closed)


def long_trade(**kw):
    t = {"id": 1, "symbol": "XRPUSDT", "side": "LONG", "entry": 100.0, "stop": 98.0,
         "target": 104.0, "position_size": 10.0, "timestamp": datetime.now(UTC).isoformat()}
    t.update(kw)
    return t


def short_trade(**kw):
    return long_trade(side="SHORT", stop=102.0, target=96.0, **kw)


# ---------------------------------------------------------------------------
# Исполнение
# ---------------------------------------------------------------------------

def test_long_stop_touched_by_wick_fills_at_stop_not_at_the_low(book):
    t = long_trade()
    book.trades.append(t)
    trade_manager.check_trades("XRPUSDT", 99.0, low=95.0, high=99.5)
    [c] = book.closed
    fill = 98.0 * (1 - SLIP)
    assert c.reason == "STOP_LOSS"
    assert c.price == pytest.approx(fill)
    assert c.pnl == pytest.approx(trade_manager._calc_pnl(t, fill))


def test_short_stop_fills_at_stop_plus_slippage(book):
    book.trades.append(short_trade())
    trade_manager.check_trades("XRPUSDT", 101.0, low=100.5, high=105.0)
    [c] = book.closed
    assert c.reason == "STOP_LOSS"
    assert c.price == pytest.approx(102.0 * (1 + SLIP))


def test_take_profit_touched_by_wick_fills_at_target(book):
    book.trades.append(long_trade())
    trade_manager.check_trades("XRPUSDT", 102.5, low=102.2, high=104.5)
    [c] = book.closed
    assert c.reason == "TAKE_PROFIT"
    assert c.price == pytest.approx(104.0)


def test_stop_and_target_in_one_interval_counts_as_stop(book):
    book.trades.append(long_trade())
    trade_manager.check_trades("XRPUSDT", 100.0, low=97.0, high=105.0)
    [c] = book.closed
    assert c.reason == "STOP_LOSS", "порядок внутри свечи неизвестен — пессимистично"


def test_without_extremes_the_close_decides(book):
    book.trades.append(long_trade())
    trade_manager.check_trades("XRPUSDT", 99.0)
    assert book.closed == []
    trade_manager.check_trades("XRPUSDT", 97.5)
    [c] = book.closed
    assert c.reason == "STOP_LOSS"
    assert c.price == pytest.approx(98.0 * (1 - SLIP))


def test_untouched_levels_close_nothing(book):
    book.trades.append(long_trade())
    trade_manager.check_trades("XRPUSDT", 100.5, low=98.5, high=101.0)
    assert book.closed == []


# ---------------------------------------------------------------------------
# Новые экстремумы
# ---------------------------------------------------------------------------

def test_first_sight_of_a_symbol_counts_the_whole_candle():
    wm = CandleWatermark()
    assert wm.fresh_extremes("XRPUSDT", 1000, 95.0, 105.0) == (95.0, 105.0)


def test_same_candle_old_extremes_are_not_fresh():
    """Минимум уже учтён прошлой проверкой, когда стоп стоял ниже."""
    wm = CandleWatermark()
    wm.fresh_extremes("XRPUSDT", 1000, 95.0, 105.0)
    assert wm.fresh_extremes("XRPUSDT", 1000, 95.0, 105.0) == (None, None)


def test_same_candle_new_low_is_fresh():
    wm = CandleWatermark()
    wm.fresh_extremes("XRPUSDT", 1000, 95.0, 105.0)
    assert wm.fresh_extremes("XRPUSDT", 1000, 94.0, 105.0) == (94.0, None)
    assert wm.fresh_extremes("XRPUSDT", 1000, 94.0, 106.0) == (None, 106.0)


def test_new_candle_is_fresh_again():
    wm = CandleWatermark()
    wm.fresh_extremes("XRPUSDT", 1000, 95.0, 105.0)
    assert wm.fresh_extremes("XRPUSDT", 1300, 99.0, 101.0) == (99.0, 101.0)


def test_trailing_raised_after_the_low_does_not_stop_out_retroactively(book):
    """
    Сценарий бага: первая проверка видит минимум 99,1 при стопе 98 — сделка жива.
    Потом стоп подтянут до 99,5. Вторая проверка той же свечи с тем же минимумом
    не должна закрыть сделку по новому стопу.
    """
    wm = CandleWatermark()
    book.trades.append(long_trade())
    low, high = wm.fresh_extremes("XRPUSDT", 1000, 99.1, 101.0)
    trade_manager.check_trades("XRPUSDT", 100.8, low=low, high=high)
    assert book.closed == []

    book.trades[0]["stop"] = 99.5
    low, high = wm.fresh_extremes("XRPUSDT", 1000, 99.1, 101.0)
    trade_manager.check_trades("XRPUSDT", 100.8, low=low, high=high)
    assert book.closed == []
