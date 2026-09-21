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
        self.coins = dict({"USDT": 150_000.0, "BTC": 15.0, "ETH": 200.0} if coins is None else coins)
        self.margin = 0.0                  # маржа шортов при плече 1× = их номинал
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

    def available_usd(self):
        return self.coins.get("USDT", 0.0) - self.margin

    def apply_demo_usdt(self, amount):
        self.funds += amount
        self.coins["USDT"] = self.coins.get("USDT", 0.0) + amount

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
        if side == "Buy" and qty * px > self.available_usd():
            raise RuntimeError("Bybit API error 170131 on /v5/order/create: Insufficient balance.")
        sign = 1 if side == "Buy" else -1
        self.coins[coin] = self.coins.get(coin, 0) + sign * qty
        self.coins["USDT"] = self.coins.get("USDT", 0.0) - sign * qty * px

    def perp_market(self, s, side, qty, reduce_only=False):
        if side == "Sell" and self.fail_short_once:
            self.fail_short_once = False
            raise RuntimeError("биржа не ответила")
        if side == "Sell" and qty * self.prices[s] > self.available_usd():
            raise RuntimeError("Bybit API error 110007: ab not enough for new order")
        self.orders.append(("perp", s, side, qty, reduce_only))
        self.shorts[s] += qty if side == "Sell" else -qty
        self.margin = max(0.0, self.margin + (1 if side == "Sell" else -1) * qty * self.prices[s])

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


def test_empty_subaccount_gets_enough_funds_for_both_pairs(env):
    """Как на проде 14.09: пустой субсчёт. Спот оплачивается полностью, шорт 1× держит маржу = номинал."""
    store, _ = env
    cli = FakeCarry(coins={})
    cm.cycle(cli, store, NOW)
    assert cli.shorts["BTCUSDT"] == pytest.approx(0.125) and cli.shorts["ETHUSDT"] == pytest.approx(3.33)
    assert cli.coins["ETH"] == pytest.approx(3.33) and store.get("opened_at") == str(NOW)


def test_recovery_after_insufficient_balance_tops_up_and_opens_the_rest(env):
    """Состояние прода 14.09: чистый старт был, BTC открыт, на ETH денег не хватило."""
    store, _ = env
    cli = FakeCarry(coins={"USDT": 5_000.0, "BTC": 0.125})
    cli.shorts["BTCUSDT"] = 0.125
    cli.margin = 10_000.0
    store.set("cleaned_at", NOW - 1)
    store.set("bought:BTCUSDT", NOW - 1)
    cm.cycle(cli, store, NOW)
    assert cli.shorts["ETHUSDT"] == pytest.approx(3.33) and cli.funds > 0
    assert len([o for o in cli.orders if o[:3] == ("spot", "BTCUSDT", "Buy")]) == 0, "BTC не докупается"
    assert store.get("opened_at") == str(NOW)


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
    devs, pos = conn.execute("SELECT deviations, positions FROM snapshots ORDER BY ts DESC").fetchone()
    assert json.loads(devs)["BTCUSDT"] == pytest.approx(0.0)
    assert json.loads(pos)["BTCUSDT"] == {"spot": pytest.approx(0.125), "short": pytest.approx(0.125), "price": 80_000.0}


def test_old_snapshot_table_gets_the_positions_column(tmp_path):
    p = str(tmp_path / "old.db")
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE snapshots (ts INTEGER PRIMARY KEY, equity REAL, mm_rate REAL, deviations TEXT)")
    c.commit()
    c.close()
    Store(p).snapshot(1, 2.0, 0.1, {}, {"BTCUSDT": {"spot": 1, "short": 1, "price": 1}})
    assert sqlite3.connect(p).execute("SELECT positions FROM snapshots").fetchone()[0].startswith("{")


def test_health_by_heartbeat_age(tmp_path):
    from carry import health
    hb = tmp_path / "hb"
    assert not health.check(hb, 1000.0)
    hb.write_text("1000\n", encoding="utf-8")
    assert health.check(hb, 1000.0 + health.MAX_AGE) and not health.check(hb, 1001.0 + health.MAX_AGE)


def test_weekly_summary_is_sent_once_on_monday(tmp_path):
    """Сводка И13 владельцу — понедельник 01:00–02:00 UTC, один раз в неделю; текст — стоимость, фандинг, хедж."""
    from carry import __main__ as cm
    from carry.store import Store
    from portfolio import engine as pe
    monday = pe.monday_of(1_789_400_000_000)
    store = Store(str(tmp_path / "carry.db"))
    store.set("start_equity", 40_000.0)
    store.add_funding([{"id": "f1", "symbol": "BTCUSDT", "change": "3.5", "transactionTime": str(monday)}])
    assert not cm.weekly_summary_due(monday + 30 * 60_000, store), "00:30 — рано"
    assert cm.weekly_summary_due(monday + 90 * 60_000, store)
    text = cm.weekly_summary(store, 40_010.0, {"BTCUSDT": 0.004, "ETHUSDT": -0.01}, monday + 90 * 60_000)
    assert "+10.00 USDT" in text and "+3.50 USDT" in text and "BTC +0.4 %" in text and "ETH -1.0 %" in text
    store.set(f"weekly:{monday}", monday + 90 * 60_000)
    assert not cm.weekly_summary_due(monday + 100 * 60_000, store), "уже отправлена"
    assert cm.weekly_summary_due(monday + 7 * 86_400_000 + 90 * 60_000, store), "следующая неделя — снова"


def test_weekly_summary_not_sent_is_retried_the_same_day(tmp_path, monkeypatch):
    """21.09.2026: сводка не ушла (у контейнера не было выхода к прокси), а отметка встала — неделя пропала."""
    from carry import __main__ as cm
    from carry.store import Store
    from portfolio import engine as pe
    monday = pe.monday_of(1_789_400_000_000)
    store = Store(str(tmp_path / "carry.db"))
    monkeypatch.setattr(cm, "notify", lambda text: False)
    assert not cm.send_weekly_summary(store, 1.0, {}, monday + 90 * 60_000)
    assert store.get(f"weekly:{monday}") is None
    sent = []
    monkeypatch.setattr(cm, "notify", lambda text: sent.append(text) or True)
    assert cm.send_weekly_summary(store, 1.0, {}, monday + 3 * 3_600_000 + 60_000), "03:01 того же дня — повтор"
    assert store.get(f"weekly:{monday}") and len(sent) == 1
    assert not cm.send_weekly_summary(store, 1.0, {}, monday + 4 * 3_600_000), "ушла — второй раз нет"
    assert not cm.weekly_summary_due(monday + 24 * 3_600_000 + 60_000, Store(str(tmp_path / "c2.db"))), "вторник — не день сводки"
