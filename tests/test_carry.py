"""
Исполнитель И13 (carry/): чистая логика, свой ключ, чистый старт, открытие пар без повтора,
подгонка хеджа, экстренное закрытие, запись исполнений и фандинга без дублей, отчёт.
Биржа подменена FakeCarry — состояние демо-субсчёта в памяти.
"""
import json
import math
import sqlite3

import pytest

from carry import engine, report
from carry import __main__ as cm
from carry.client import CarryClient
from carry.store import Store

NOW = 1_789_400_000_000


class FakeCarry:
    """Демо-субсчёт: кошелёк монет, шорты, цены; ордера исполняются сразу по цене."""

    def __init__(self, coins=None, prices=None, mm=0.05):
        self.coins = dict(coins or {"USDT": 150_000.0, "BTC": 15.0, "ETH": 200.0})
        self.prices = prices or {"BTCUSDT": 80_000.0, "ETHUSDT": 3_000.0}
        self.shorts = {s: 0.0 for s in self.prices}
        self.mm = mm
        self.orders = []
        self.fail_short_once = False
        self.funds = 0.0
        self.leverage = {}

    def coin_balances(self):
        return dict(self.coins)

    def total_equity(self):
        return self.coins["USDT"] + sum(self.coins.get(s[:-4], 0) * p for s, p in self.prices.items())

    def account_mm_rate(self):
        return self.mm

    def apply_demo_usdt(self, amount):
        self.funds += amount
        self.coins["USDT"] += amount

    def spot_price(self, s):
        return self.prices[s]

    def spot_base_step(self, s):
        return 0.000001

    def get_qty_step(self, s):
        return {"BTCUSDT": 0.001, "ETHUSDT": 0.01}[s]

    def set_leverage(self, s, lev):
        self.leverage[s] = lev

    def spot_market(self, s, side, qty):
        self.orders.append(("spot", s, side, qty))
        coin, px = s[:-4], self.prices[s]
        sign = 1 if side == "Buy" else -1
        self.coins[coin] = self.coins.get(coin, 0) + sign * qty
        self.coins["USDT"] -= sign * qty * px

    def perp_market(self, s, side, qty, reduce_only=False):
        if side == "Sell" and self.fail_short_once:
            self.fail_short_once = False
            raise RuntimeError("биржа не ответила")
        self.orders.append(("perp", s, side, qty, reduce_only))
        self.shorts[s] += qty if side == "Sell" else -qty

    def short_qty(self, s):
        return self.shorts[s]

    def executions(self, category, since):
        return [{"execId": f"{category}-1", "symbol": "BTCUSDT", "side": "Buy", "execQty": "0.125",
                 "execPrice": "80000", "execFee": "0.000125", "feeCurrency": "BTC", "execTime": str(NOW)}]

    def settlements(self, since):
        return [{"id": "f1", "symbol": "BTCUSDT", "change": "1.0", "funding": "-1.0", "transactionTime": str(NOW)},
                {"id": "f2", "symbol": "ETHUSDT", "change": "-0.2", "funding": "0.2", "transactionTime": str(NOW)}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CARRY_DIR", str(tmp_path))
    monkeypatch.setenv("CARRY_SYMBOLS", "BTCUSDT,ETHUSDT")
    monkeypatch.setenv("CARRY_NOTIONAL_USDT", "10000")
    return Store(str(tmp_path / "carry.db")), tmp_path


# --- чистая логика ------------------------------------------------------

def test_pair_qty_and_floor_step():
    assert engine.pair_qty(10_000, 80_000, 0.001, 0.001) == 0.125
    assert engine.pair_qty(10_000, 3_333, 0.01, 0.01) == 3.0
    assert engine.pair_qty(1, 80_000, 0.001, 0.001) == 0.0
    assert engine.floor_step(0.3, 0.1) == 0.3


def test_hedge_adjustment_in_and_out_of_tolerance():
    assert engine.hedge_adjustment(1.0, 0.99, 0.001) is None, "1 % — в допуске 2 %"
    assert engine.hedge_adjustment(1.0, 0.97, 0.001) == pytest.approx(0.03), "3 % — вне допуска 2 %, подгоняется"
    assert engine.hedge_adjustment(1.0, 0.95, 0.001) == pytest.approx(0.05)
    assert engine.hedge_adjustment(1.0, 1.05, 0.001) == pytest.approx(-0.05)
    assert engine.hedge_adjustment(0.0, 0.5, 0.001) == pytest.approx(-0.5), "спота нет — шорт убрать"
    assert engine.hedge_deviation(0.0, 0.0) == 0.0 and math.isinf(engine.hedge_deviation(0.0, 1.0))


def test_margin_danger_threshold():
    assert not engine.margin_danger(None) and not engine.margin_danger(0.66)
    assert engine.margin_danger(0.67) and engine.DANGER_MM_RATE == pytest.approx(1 / 1.5)


def test_client_refuses_to_run_without_its_own_key(monkeypatch):
    monkeypatch.setenv("BYBIT_API_KEY", "bot-key")
    monkeypatch.setenv("BYBIT_API_SECRET", "bot-secret")
    with pytest.raises(RuntimeError, match="ключ основного бота"):
        CarryClient("", "")
    with pytest.raises(RuntimeError):
        CarryClient("k", "  ")


# --- цикл ---------------------------------------------------------------

def test_first_cycle_cleans_then_opens_hedged_pairs(env):
    store, _ = env
    cli = FakeCarry()
    cm.cycle(cli, store, NOW)
    assert ("spot", "BTCUSDT", "Sell", 15.0) in cli.orders and ("spot", "ETHUSDT", "Sell", 200.0) in cli.orders
    assert cli.coins["BTC"] == pytest.approx(0.125) and cli.shorts["BTCUSDT"] == pytest.approx(0.125)
    assert cli.coins["ETH"] == pytest.approx(3.33) and cli.shorts["ETHUSDT"] == pytest.approx(3.33)
    assert cli.leverage == {"BTCUSDT": 1, "ETHUSDT": 1}
    assert store.get("opened_at") == str(NOW) and float(store.get("start_equity")) == pytest.approx(cli.total_equity())


def test_pairs_are_not_reopened_and_failed_short_is_fixed_by_rehedge(env):
    store, _ = env
    cli = FakeCarry()
    cli.fail_short_once = True
    with pytest.raises(RuntimeError):
        cm.cycle(cli, store, NOW)
    assert cli.coins["BTC"] == pytest.approx(0.125) and cli.shorts["BTCUSDT"] == 0
    assert cli.coins.get("ETH", 0) == 0, "до ETH первый цикл не дошёл"
    cm.cycle(cli, store, NOW + 3_600_000)
    buys = [o for o in cli.orders if o[:3] == ("spot", "BTCUSDT", "Buy")]
    assert len(buys) == 1, "спот не покупается второй раз"
    assert cli.shorts["BTCUSDT"] == pytest.approx(0.125), "шорт догнала подгонка хеджа"
    assert cli.coins["ETH"] == pytest.approx(3.33) and cli.shorts["ETHUSDT"] == pytest.approx(3.33), "ETH открыт"
    assert store.get("opened_at") == str(NOW + 3_600_000)


def test_demo_funds_are_requested_when_usdt_is_short(env):
    store, _ = env
    cli = FakeCarry(coins={"USDT": 1_000.0})
    cm.cycle(cli, store, NOW)
    assert cli.funds >= 24_000 - 1_000


def test_danger_margin_closes_everything_and_halts(env):
    store, _ = env
    cli = FakeCarry()
    cm.cycle(cli, store, NOW)
    cli.mm = 0.7
    cm.cycle(cli, store, NOW + 3_600_000)
    assert cli.shorts == {"BTCUSDT": 0.0, "ETHUSDT": 0.0}
    assert cli.coins["BTC"] == pytest.approx(0.0) and store.get("halted")
    n = len(cli.orders)
    cli.mm = 0.05
    cm.cycle(cli, store, NOW + 7_200_000)
    assert len(cli.orders) == n, "после остановки ордеров нет — только учёт"


def test_sync_is_idempotent_and_report_uses_wallet_change(env):
    store, tmp = env
    cli = FakeCarry()
    cm.cycle(cli, store, NOW)
    cm.cycle(cli, store, NOW + 3_600_000)
    conn = sqlite3.connect(str(tmp / "carry.db"))
    assert conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM funding").fetchone()[0] == 2
    s = report.summary(conn)
    assert s["funding"] == pytest.approx(0.8), "по change: +1,0 − 0,2"
    assert s["fees_usdt"] == pytest.approx(2 * 0.000125 * 80_000), "комиссия в BTC пересчитана в USDT"
    assert (tmp / "heartbeat").exists()
    devs = json.loads(conn.execute("SELECT deviations FROM snapshots ORDER BY ts DESC").fetchone()[0])
    assert devs["BTCUSDT"] == pytest.approx(0.0)


def test_health_by_heartbeat_age(tmp_path):
    from carry import health
    hb = tmp_path / "hb"
    assert not health.check(hb, 1000.0)
    hb.write_text("1000\n", encoding="utf-8")
    assert health.check(hb, 1000.0 + health.MAX_AGE) and not health.check(hb, 1001.0 + health.MAX_AGE)
