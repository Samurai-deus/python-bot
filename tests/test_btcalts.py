"""
Исполнитель И18 (btcalts/): веса лонг BTC / шорт top30 альтов, запрос демо-средств на пустом
субсчёте, первая ребалансировка в ближайший понедельник, уведомления, стоп; календарь программы.
"""
import pytest

from btcalts import __main__ as bm
from btcalts import engine
from portfolio import calendar
from portfolio import engine as pe
from portfolio.store import Store
from tests.test_portfolio import DAY, MONDAY, FakeBybitPortfolio, series


def cont(t, turn30=1e7, age_d=500):
    return {**series(t, 35), "turn30": turn30, "age_d": age_d}


def test_weights_long_btc_and_short_top30_alts_equally():
    data = {"BTCUSDT": cont(MONDAY, turn30=1e9), **{f"A{i:02}USDT": cont(MONDAY, turn30=1e7 + i * 1e5) for i in range(40)},
            "YOUNGUSDT": cont(MONDAY, turn30=9e8, age_d=30)}
    w = engine.weights(data, list(data))
    shorts = {s for s, v in w.items() if v < 0}
    assert w["BTCUSDT"] == pytest.approx(0.5) and len(shorts) == 30 and "YOUNGUSDT" not in shorts and "A09USDT" not in shorts
    assert all(v == pytest.approx(-0.5 / 30) for s, v in w.items() if s != "BTCUSDT")
    assert sum(abs(v) for v in w.values()) == pytest.approx(1.0)
    assert engine.weights({k: data[k] for k in list(data)[:8]}, list(data)[:8]) == {}, "меньше 10 альтов — позиций нет"
    assert engine.weights({k: v for k, v in data.items() if k != "BTCUSDT"}, list(data)) == {}, "без BTC ноги нет"


class FakeBtcAlts(FakeBybitPortfolio):
    def __init__(self, t, usdt=0.0):
        super().__init__(t)
        self.equity = usdt
        self.applied = []
        self.cands = [f"A{i:02}USDT" for i in range(35)]
        for s in self.cands + ["BTCUSDT"]:
            self.prices[s] = 100.0

    def available_usd(self):
        return self.equity

    def apply_demo_usdt(self, amount):
        self.applied.append(amount)
        self.equity += amount

    def continuation_candidates(self, ages):
        return self.cands


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BTCALTS_DIR", str(tmp_path))
    monkeypatch.setenv("BTCALTS_CAPITAL_USDT", "5000")
    sent = []
    monkeypatch.setattr(bm, "notify", lambda text: sent.append(text))
    return Store(str(tmp_path / "btcalts.db")), sent


def test_empty_subaccount_requests_demo_funds_and_starts(env):
    store, sent = env
    cli = FakeBtcAlts(MONDAY, usdt=0.0)
    bm.cycle(cli, store, MONDAY - 3 * DAY)                       # пятница
    assert cli.applied == [10_000] and store.get("started_at") and float(store.get("start_equity")) == 10_000
    assert store.get("last_rebalance_t") == str(MONDAY - 7 * DAY) and cli.orders == []
    assert sent and "И18" in sent[0] and "запущен" in sent[0]


def test_first_rebalance_is_the_next_monday_with_notification(env):
    store, sent = env
    cli = FakeBtcAlts(MONDAY, usdt=20_000.0)
    bm.cycle(cli, store, MONDAY - 3 * DAY)
    assert cli.applied == [] and cli.orders == []
    bm.cycle(cli, store, MONDAY + 3 * 60_000)
    assert cli.qty["BTCUSDT"] * 100.0 == pytest.approx(2500, abs=25), "лонг BTC 50 % капитала"
    shorts = {s: q for s, q in cli.qty.items() if q < 0}
    assert len(shorts) == 30 and all(q * 100.0 == pytest.approx(-2500 / 30, abs=25) for q in shorts.values())
    assert all(lev == engine.LEVERAGE for lev in cli.leverage.values())
    assert store.get("last_rebalance_t") == str(MONDAY)
    assert any("ребалансировка" in s and "шорт 30 альтов" in s for s in sent)
    n = len(cli.orders)
    bm.cycle(cli, store, MONDAY + 5 * 3_600_000)
    assert len(cli.orders) == n, "второй раз в тот же понедельник не торгуем"


def test_drawdown_halt_closes_everything_and_notifies(env):
    store, sent = env
    cli = FakeBtcAlts(MONDAY, usdt=20_000.0)
    bm.cycle(cli, store, MONDAY - 3 * DAY)
    bm.cycle(cli, store, MONDAY + 3 * 60_000)
    cli.equity = 20_000.0 - 0.25 * 5000 - 1
    bm.cycle(cli, store, MONDAY + 2 * 3_600_000)
    assert store.get("halted") and all(q == 0 for q in cli.qty.values()) and any("остановлен" in s for s in sent)


def test_calendar_notifies_on_the_day_and_three_days_before_once():
    day = calendar.day_ms = __import__("backtest.momentum_xs", fromlist=["day_ms"]).day_ms
    sent = set()
    t = day("2026-12-14") + 8 * 3_600_000
    msgs = calendar.due(t, sent.__contains__)
    assert any(k == "today:2026-12-14" and "И14: итог 12 недель" in txt for k, txt in msgs)
    assert calendar.due(t - 3_600_000, sent.__contains__) == [] or all(k != "today:2026-12-14" for k, _ in calendar.due(t - 3_600_000, sent.__contains__)), "до 08:00 UTC не шлём"
    sent.add("today:2026-12-14")
    assert all(k != "today:2026-12-14" for k, _ in calendar.due(t + 3_600_000, sent.__contains__)), "второй раз не шлём"
    r = calendar.due(day("2026-12-11") + 9 * 3_600_000, sent.__contains__)
    assert any(k == "remind:2026-12-14" and "Через 3 дня" in txt for k, txt in r)
    assert pe.monday_of(day("2026-09-21")) == day("2026-09-21")


def test_start_this_week_flag_rebalances_once_with_this_mondays_signals(env, monkeypatch):
    store, sent = env
    monkeypatch.setenv("BTCALTS_START_THIS_WEEK", "true")
    cli = FakeBtcAlts(MONDAY, usdt=20_000.0)
    bm.cycle(cli, store, MONDAY + DAY + 7 * 3_600_000)          # вторник 07:00 — окно понедельника закрыто
    assert cli.qty.get("BTCUSDT", 0.0) > 0 and sum(1 for q in cli.qty.values() if q < 0) == 30
    assert store.has_rebalance(MONDAY) and any("ребалансировка" in s for s in sent)
    def rebalances():
        return store.conn.execute("SELECT COUNT(*) FROM events WHERE kind = 'rebalance'").fetchone()[0]
    assert rebalances() == 1
    bm.cycle(cli, store, MONDAY + DAY + 9 * 3_600_000)
    assert rebalances() == 1, "догоняющая — один раз (считаем события, не ордера: позиции уже у цели)"
    monkeypatch.delenv("BTCALTS_START_THIS_WEEK")
    store2 = Store(str(store.conn.execute("PRAGMA database_list").fetchone()[2]).replace("btcalts.db", "b2.db"))
    cli2 = FakeBtcAlts(MONDAY, usdt=20_000.0)
    bm.cycle(cli2, store2, MONDAY + DAY + 7 * 3_600_000)
    assert cli2.orders == [], "без флага — ждём понедельника"
