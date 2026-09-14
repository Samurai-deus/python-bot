"""
Исполнитель И14 (portfolio/): веса из замороженных сигналов И4/И3 с множителями правила, ордера до
цели с округлением биржи, окно ребалансировки, условие старта, повтор не прошедших ордеров,
стоп по просадке, отчёт. Биржа подменена FakeBybitPortfolio — позиции и свечи в памяти.
"""
import json
import sqlite3
from decimal import Decimal

import pytest

import config
from backtest import momentum_xs as mx
from backtest import trend_ts as tt
from exchange.bybit_client import InstrumentFilters
from portfolio import engine, health
from portfolio import __main__ as pm
from portfolio.store import Store

MONDAY = mx.day_ms("2026-09-21")           # понедельник 00:00 UTC
H4, DAY = mx.H4_MS, mx.DAY_MS


def series(t, days, start=100.0, step=0.0):
    """4h-бары за days дней до t включая живой бар в t: цена start растёт на step за бар."""
    c4, o4 = {}, {}
    n = days * 6
    for i in range(n, -1, -1):
        ts = t - i * H4
        o4[ts] = start + (n - i) * step
        if ts + H4 <= t:
            c4[ts] = start + (n - i + 1) * step
    return {"c4": c4, "o4": o4, "o1": {t: o4[t]}}


def filters(symbol, step="0.001", min_qty="0.001", min_notional="5"):
    return InstrumentFilters(symbol=symbol, status="Trading", tick_size=Decimal("0.1"), qty_step=Decimal(step),
                             min_qty=Decimal(min_qty), max_market_qty=Decimal("1000000"),
                             min_notional=Decimal(min_notional), max_leverage=Decimal("100"))


# --- чистая логика --------------------------------------------------------------------------

def test_combined_weights_use_frozen_signals_with_rule_multipliers():
    data = {s: series(MONDAY, 35, step=0.01 if i % 2 == 0 else -0.01) for i, s in enumerate(config.SYMBOLS)}
    w = engine.combined_weights(data, list(tt.SYMBOLS), list(config.SYMBOLS), MONDAY)
    trend = tt.targets(data, list(tt.SYMBOLS), MONDAY, MONDAY, 30)
    longs, shorts = mx.baskets(data, list(config.SYMBOLS), MONDAY, 28)
    assert trend and longs and shorts
    for s in tt.SYMBOLS:
        mom = engine.K_MOMENTUM * mx.WEIGHT * ((s in longs) - (s in shorts))
        assert w[s] == pytest.approx(engine.K_TREND * trend[s] + mom)
    assert engine.K_TREND == pytest.approx(2.01) and engine.K_MOMENTUM == pytest.approx(1.01)
    assert all(abs(w[s]) == pytest.approx(engine.K_MOMENTUM * mx.WEIGHT) for s in longs + shorts if s not in tt.SYMBOLS)


def test_orders_reach_targets_and_skip_dust():
    targets = {"AUSDT": 1000.0, "BUSDT": -500.0, "CUSDT": 0.0, "DUSDT": 12.0}
    positions = {"AUSDT": 400.0, "BUSDT": 300.0, "CUSDT": -200.0, "EUSDT": 15.0}
    marks = {s: 10.0 for s in ("AUSDT", "BUSDT", "CUSDT", "DUSDT", "EUSDT")}
    fl = {s: filters(s) for s in marks}
    assert engine.min_order_usdt(10_000) == 20.0 and engine.min_order_usdt(1_000) == 5.0, "0,2 % капитала, не меньше 5"
    orders = {o.symbol: o for o in engine.orders_to_target(targets, positions, marks, fl, engine.min_order_usdt(10_000))}
    assert orders["AUSDT"].side == "Buy" and orders["AUSDT"].qty == Decimal("60") and not orders["AUSDT"].reduce_only
    assert orders["BUSDT"].side == "Sell" and orders["BUSDT"].qty == Decimal("80"), "флип лонг → шорт одним ордером"
    assert orders["CUSDT"].side == "Buy" and orders["CUSDT"].reduce_only, "цель 0 — только закрытие"
    assert "DUSDT" not in orders and "EUSDT" not in orders, "разница меньше 20 USDT не торгуется"


def test_orders_respect_exchange_step():
    orders = engine.orders_to_target({"AUSDT": 100.0}, {}, {"AUSDT": 3.0}, {"AUSDT": filters("AUSDT", step="1", min_qty="1")}, 5.0)
    assert orders[0].qty == Decimal("33")


def test_rebalance_window_is_monday_after_two_minutes_until_midnight():
    assert engine.due_rebalance(MONDAY + 60_000, None) is None, "ещё нет 2 минут"
    assert engine.due_rebalance(MONDAY + 3 * 60_000, None) == MONDAY
    assert engine.due_rebalance(MONDAY + 20 * 3_600_000, None) == MONDAY, "повтор в течение понедельника"
    assert engine.due_rebalance(MONDAY + 3 * 60_000, MONDAY) is None, "уже сделана"
    assert engine.due_rebalance(MONDAY + DAY + 3_600_000, None) is None, "вторник — не окно"
    assert engine.next_rebalance(MONDAY - 3 * DAY) == MONDAY and engine.next_rebalance(MONDAY) == MONDAY + 7 * DAY


def test_drawdown_halt_threshold():
    assert not engine.drawdown_halt(12_000, 9_500, 10_000)
    assert engine.drawdown_halt(12_000, 9_499, 10_000) and engine.MAX_DRAWDOWN == 0.25


# --- цикл -------------------------------------------------------------------------------------

class FakeBybitPortfolio:
    def __init__(self, t, step_by_symbol=None):
        self.t = t
        self.prices = {s: 100.0 for s in set(config.SYMBOLS) | set(tt.SYMBOLS)}
        self.qty = {}
        self.equity = 2_000_000.0
        self.orders = []
        self.leverage = {}
        self.fail_symbols = set()
        self.steps = step_by_symbol or {}

    def market_data(self, symbols, t):
        return {s: series(t, 35, start=100.0, step=self.steps.get(s, 0.01)) for s in symbols}

    def positions_qty(self):
        return {s: q for s, q in self.qty.items() if q}

    def positions_usdt(self):
        return {s: q * self.prices[s] for s, q in self.qty.items() if q}

    def get_mark_price(self, s):
        return self.prices[s]

    def get_instrument_filters(self, s):
        return filters(s)

    def set_leverage(self, s, lev):
        self.leverage[s] = lev

    def place_order(self, symbol, side, qty, reduce_only=False, **kw):
        if symbol in self.fail_symbols:
            raise RuntimeError("биржа отклонила")
        self.orders.append((symbol, side, float(qty), reduce_only))
        self.qty[symbol] = self.qty.get(symbol, 0.0) + (float(qty) if side == "Buy" else -float(qty))

    def total_equity(self):
        return self.equity

    def settlements(self, start_ms):
        return [{"id": "f1", "symbol": "BTCUSDT", "change": "0.5", "transactionTime": str(self.t)}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_DIR", str(tmp_path))
    monkeypatch.setenv("PORTFOLIO_CAPITAL_USDT", "10000")
    monkeypatch.setattr(pm, "notify", lambda text: None)
    import database
    monkeypatch.setattr(database, "get_open_trades", lambda: [])
    return Store(str(tmp_path / "portfolio.db")), tmp_path


def test_start_waits_for_the_bot_journal_and_positions(env, monkeypatch):
    store, _ = env
    cli = FakeBybitPortfolio(MONDAY)
    import database
    monkeypatch.setattr(database, "get_open_trades", lambda: [{"id": 1}])
    pm.cycle(cli, store, MONDAY + 3 * 60_000)
    assert store.get("started_at") is None and cli.orders == []
    monkeypatch.setattr(database, "get_open_trades", lambda: [])
    cli.qty["TIAUSDT"] = -600.0
    pm.cycle(cli, store, MONDAY + 2 * 3_600_000)
    assert store.get("started_at") is None, "позиция бота ещё на счёте"
    cli.qty.clear()
    pm.cycle(cli, store, MONDAY + 3 * 3_600_000)
    assert store.get("started_at") and float(store.get("start_equity")) == 2_000_000.0


def test_rebalance_places_orders_to_target_weights_and_only_once(env):
    store, _ = env
    cli = FakeBybitPortfolio(MONDAY)
    pm.cycle(cli, store, MONDAY - 2 * DAY)                 # старт в субботу — позиций нет
    assert cli.orders == []
    pm.cycle(cli, store, MONDAY + 3 * 60_000)
    assert cli.orders and store.get("last_rebalance_t") == str(MONDAY)
    weights = engine.combined_weights(cli.market_data(pm.symbols(), MONDAY), list(tt.SYMBOLS), list(config.SYMBOLS), MONDAY)
    for s, w in weights.items():
        assert cli.qty.get(s, 0.0) * 100.0 == pytest.approx(w * 10_000, abs=25), s
    assert all(lev == engine.LEVERAGE for lev in cli.leverage.values())
    n = len(cli.orders)
    pm.cycle(cli, store, MONDAY + 5 * 3_600_000)
    assert len(cli.orders) == n, "в тот же понедельник второй раз не торгуем"


def test_failed_orders_are_retried_next_hour_only_for_those_symbols(env):
    store, _ = env
    cli = FakeBybitPortfolio(MONDAY)
    pm.cycle(cli, store, MONDAY - DAY)
    cli.fail_symbols = {"BTCUSDT"}
    pm.cycle(cli, store, MONDAY + 3 * 60_000)
    assert store.get("last_rebalance_t") is None and not any(o[0] == "BTCUSDT" for o in cli.orders)
    n = len(cli.orders)
    cli.fail_symbols = set()
    pm.cycle(cli, store, MONDAY + 3_600_000 + 3 * 60_000)
    assert store.get("last_rebalance_t") == str(MONDAY)
    new = cli.orders[n:]
    assert [o[0] for o in new] == ["BTCUSDT"], "повторяется только не прошедшее"


def test_drawdown_halt_closes_everything_and_stops(env):
    store, tmp = env
    cli = FakeBybitPortfolio(MONDAY)
    pm.cycle(cli, store, MONDAY - DAY)
    pm.cycle(cli, store, MONDAY + 3 * 60_000)
    cli.equity = 2_000_000.0 - 2_600.0
    pm.cycle(cli, store, MONDAY + DAY)
    assert store.get("halted") and cli.positions_qty() == {}
    n = len(cli.orders)
    pm.cycle(cli, store, MONDAY + 7 * DAY + 3 * 60_000)
    assert len(cli.orders) == n, "после остановки — только учёт"
    conn = sqlite3.connect(str(tmp / "portfolio.db"))
    assert conn.execute("SELECT COUNT(*) FROM funding").fetchone()[0] == 1
    assert (tmp / "heartbeat").exists()
    assert json.loads(conn.execute("SELECT positions FROM snapshots ORDER BY ts DESC").fetchone()[0]) == {}


def test_client_market_data_keeps_only_closed_bars_in_c4(monkeypatch):
    from portfolio.client import PortfolioClient
    cli = PortfolioClient(api_key="k", api_secret="s", demo=True)
    rows = [[str(MONDAY - 2 * H4), "10", "0", "0", "11", "0", "0"], [str(MONDAY - H4), "11", "0", "0", "12", "0", "0"],
            [str(MONDAY), "12", "0", "0", "13", "0", "0"]]                     # последний бар ещё идёт
    monkeypatch.setattr(cli, "get_klines", lambda s, i, n: rows)
    d = cli.market_data(["BTCUSDT"], MONDAY)["BTCUSDT"]
    assert d["c4"] == {MONDAY - 2 * H4: 11.0, MONDAY - H4: 12.0}, "close живого бара в сигнал не идёт"
    assert d["o4"][MONDAY] == 12.0 and d["o1"] == {MONDAY: 12.0}


def test_client_positions_are_signed(monkeypatch):
    from types import SimpleNamespace
    from portfolio.client import PortfolioClient
    cli = PortfolioClient(api_key="k", api_secret="s", demo=True)
    monkeypatch.setattr(cli, "get_positions", lambda symbol=None: [
        SimpleNamespace(symbol="AUSDT", side="Buy", size=2.0, entry_price=10.0),
        SimpleNamespace(symbol="BUSDT", side="Sell", size=3.0, entry_price=5.0),
        SimpleNamespace(symbol="CUSDT", side="", size=0.0, entry_price=0.0)])
    assert cli.positions_usdt() == {"AUSDT": 20.0, "BUSDT": -15.0}
    assert cli.positions_qty() == {"AUSDT": 2.0, "BUSDT": -3.0}


def test_health_by_heartbeat_age(tmp_path):
    hb = tmp_path / "hb"
    assert not health.check(hb, 1000.0)
    hb.write_text("1000\n", encoding="utf-8")
    assert health.check(hb, 1000.0 + health.MAX_AGE) and not health.check(hb, 1001.0 + health.MAX_AGE)
