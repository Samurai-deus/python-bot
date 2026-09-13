"""
И9б (backtest/binance_launch.py): разбор заголовков, контракт Bybit до анонса, одно событие в
сутки, вход через 2 минуты, выход и издержки, критерии. Bybit подменён словарём свечей.
"""
import pytest

from backtest import binance_launch as bl

MIN = bl.MIN_MS
T0 = 1_700_000_000_000 - 1_700_000_000_000 % MIN + 30_000  # анонс в середине минуты


class FakeApi:
    """Свечи 1m по символу: {symbol: {ts: (open, close)}}; kline отдаёт от новых к старым, как Bybit."""

    def __init__(self, bars):
        self.bars = bars
        self.calls = 0

    def get(self, path, params):
        self.calls += 1
        rows = [[str(ts), str(o), "0", "0", str(c), "0", "0"]
                for ts, (o, c) in self.bars.get(params["symbol"], {}).items()
                if params["start"] <= ts <= params["end"]]
        return {"list": sorted(rows, key=lambda r: -int(r[0]))[:params["limit"]]}


def flat(start, minutes, price=1.0):
    return {start + i * MIN: (price, price) for i in range(minutes)}


# ---------------------------------------------------------------------------
# Заголовки
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title, expected", [
    ("Binance Futures Will Launch USDⓈ-Margined GPROUSDT Perpetual Contract (2026-09-03)", ["GPRO"]),
    ("Binance Futures Will Launch USDⓈ-Margined PONSUSDT and 哈基米USDT Perpetual Contracts", ["PONS"]),
    ("Binance Futures Will Launch USDT-Margined AXS, ALICE & DYDX Perpetual Contracts", ["AXS", "ALICE", "DYDX"]),
    ("Binance Futures Will List USDⓈ-M & COIN-Margined 1000PEPEUSDT Perpetual", ["1000PEPE"]),
    ("Binance Futures Will Launch Multiple USDⓈ-Margined TradFi Perpetual Contracts", []),
    ("Binance Will List Foo (FOO)", []),
    ("Binance Futures Will Launch USDⓈ-Margined FOOUSDT Quarterly Contract", []),
])
def test_bases_from_titles(title, expected):
    assert bl.bases(title) == expected


def test_entry_is_the_first_full_minute_two_minutes_after():
    assert bl.entry_minute(T0) == T0 - 30_000 + 3 * MIN, "анонс в :30 → вход через 2,5 мин"
    on_minute = T0 - 30_000
    assert bl.entry_minute(on_minute) == on_minute + 2 * MIN


# ---------------------------------------------------------------------------
# События
# ---------------------------------------------------------------------------

def test_event_needs_a_bybit_contract_before_the_announcement():
    pre = (T0 // MIN - bl.PRE_MIN) * MIN
    api = FakeApi({"FOOUSDT": flat(pre, 30), "1000BARUSDT": flat(pre, 30),
                   "LATEUSDT": flat(pre + MIN, 30)})  # запущен уже после отметки «за 10 минут»
    title = "Binance Futures Will Launch USDⓈ-Margined FOOUSDT, BARUSDT, LATEUSDT and NEWUSDT Perpetual Contracts"
    assert bl.events(api, [{"releaseDate": T0, "title": title}], T0 + bl.DAY_MS) == [
        ("FOOUSDT", T0), ("1000BARUSDT", T0)]


def test_one_event_per_coin_per_day_and_end_excluded():
    pre = (T0 // MIN - bl.PRE_MIN) * MIN
    api = FakeApi({"FOOUSDT": flat(pre, 3 * 24 * 60)})
    title = "Binance Futures Will Launch USDⓈ-Margined FOOUSDT Perpetual Contract"
    cat = [{"releaseDate": T0, "title": title}, {"releaseDate": T0 + 3600_000, "title": title},
           {"releaseDate": T0 + bl.DAY_MS, "title": title}, {"releaseDate": T0 + 2 * bl.DAY_MS, "title": title}]
    assert bl.events(api, cat, T0 + 2 * bl.DAY_MS) == [("FOOUSDT", T0), ("FOOUSDT", T0 + bl.DAY_MS)]


# ---------------------------------------------------------------------------
# Сделка
# ---------------------------------------------------------------------------

def test_trade_enters_at_open_and_exits_at_close_after_the_horizon_minus_costs():
    entry = bl.entry_minute(T0)
    bars = [(entry - MIN, 90.0, 90.0)] + [(entry + i * MIN, 100.0 + i, 100.5 + i) for i in range(120)]
    t = bl.trade("FOOUSDT", T0, bars, 1)
    assert t.entry == 100.0, "вход по open минуты входа — не по close и не раньше"
    assert t.exit == 159.5, "выход — close последней минуты внутри часа"
    assert t.exit_t == entry + bl.HOUR_MS
    assert t.r == pytest.approx(159.5 / 100.0 - 1 - bl.COST)
    assert bl.COST == pytest.approx(0.0041)


def test_delisted_contract_exits_at_the_last_close_and_missing_entry_is_skipped():
    entry = bl.entry_minute(T0)
    bars = [(entry + i * MIN, 100.0, 80.0) for i in range(10)]
    t = bl.trade("FOOUSDT", T0, bars, 24)
    assert t.exit == 80.0 and t.exit_t == entry + 10 * MIN
    assert bl.trade("FOOUSDT", T0, [(entry + MIN, 1.0, 1.0)], 1) is None


def test_candles_are_paged_and_sorted():
    api = FakeApi({"FOOUSDT": flat(T0 - 30_000, 2500)})
    start = T0 - 30_000
    got = bl.candles(api, "FOOUSDT", start, start + 2499 * MIN)
    assert len(got) == 2500 and got[0][0] == start and got == sorted(got)
    assert api.calls == 3


# ---------------------------------------------------------------------------
# Критерии
# ---------------------------------------------------------------------------

def trades_with(rs, start, step=bl.DAY_MS):
    return [bl.Trade("X", start + i * step, start + i * step, start + i * step + MIN, 1.0, 1.0, r)
            for i, r in enumerate(rs)]


def test_criteria_and_the_holdout_is_not_visible():
    start = 1_600_000_000_000 - 1_600_000_000_000 % bl.DAY_MS
    hold = start + 300 * bl.DAY_MS
    end = hold + 100 * bl.DAY_MS
    good = trades_with([0.02, 0.01] * 75, start, bl.DAY_MS * 2)     # 150 событий, всё до hold
    tail = trades_with([-0.5] * 30, hold + bl.DAY_MS)                # отложенный конец — не виден
    stats = bl.evaluate(good + tail, start, end, hold)
    assert stats["events"] == 150 and stats["passed"], stats["checks"]
    assert bl.holdout_mean(good + tail, hold) == pytest.approx(-0.5)


@pytest.mark.parametrize("rs, failed", [
    ([0.02, 0.01] * 55, "events"),          # 110 < 120
    ([0.004, 0.005] * 75, "mean"),          # средняя 0,45 % < 0,5 %
    ([0.2, -0.17] * 75, "ci"),              # средняя +1,5 %, но интервал через ноль
])
def test_each_criterion_can_fail(rs, failed):
    start = 1_600_000_000_000 - 1_600_000_000_000 % bl.DAY_MS
    stats = bl.evaluate(trades_with(rs, start), start, start + 500 * bl.DAY_MS, start + 400 * bl.DAY_MS)
    assert not stats["checks"][failed]


def test_drawdown_uses_the_ten_percent_notional():
    start = 1_600_000_000_000 - 1_600_000_000_000 % bl.DAY_MS
    rs = [0.05] * 100 + [-1.0] * 2 + [0.05] * 60                  # две сделки по −100 % номинала = −20 % капитала
    stats = bl.evaluate(trades_with(rs, start), start, start + 400 * bl.DAY_MS, start + 300 * bl.DAY_MS)
    assert stats["drawdown"] == pytest.approx(0.2) and not stats["checks"]["drawdown"]
