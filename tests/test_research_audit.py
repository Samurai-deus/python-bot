"""
Исследовательский слой после аудита 29.09.2026: замороженный код оценки И14 (честность исполнения,
14.12) и И18 (бумага, 16.03.2027), фандинг разных интервалов, интервал с учётом автокорреляции.
"""
import inspect
import math
import sqlite3
from types import SimpleNamespace

import pytest

from backtest import h18, report
from backtest import momentum_xs as mx
from backtest import trend_ts as tt
from backtest import wide_search as ws
from portfolio import paper
from portfolio.store import Store

MON = mx.day_ms("2026-09-21")
WEEK = mx.WEEK_MS
H4 = mx.H4_MS


def price_data(symbols, weeks=4, drift=None, funding=0.0001):
    """4h-открытия и ставки фандинга каждые 8 ч; цена монеты i растёт на drift[i] за неделю."""
    data = {}
    for i, s in enumerate(symbols):
        g = (drift or {}).get(s, 0.01 * (i + 1) * (-1) ** i)
        o4, c4 = {}, {}
        for k in range(-40 * 6, (weeks + 1) * 42 + 1):
            ts = MON + k * H4
            o4[ts] = 100.0 * (1 + g) ** (k / 42)
            c4[ts] = 100.0 * (1 + g) ** ((k + 1) / 42)
        fts = list(range(MON - 40 * mx.DAY_MS, MON + (weeks + 1) * WEEK, 8 * 3_600_000))
        data[s] = {"o4": o4, "c4": c4, "fund": {"ts": fts, "rate": [funding] * len(fts)}}
    return data


def test_paper_uses_the_same_arithmetic_as_the_frozen_trend_backtest():
    """Бумага И14 = backtest.trend_ts.simulate на тех же весах (кроме издержек выхода в конце срока)."""
    symbols = list(tt.SYMBOLS)
    data = price_data(symbols)
    weeks_t = [MON + k * WEEK for k in range(4)]
    weights = {t: tt.targets(data, symbols, t, t_next, 30) for t, t_next in zip(weeks_t, weeks_t[1:])}
    ours = paper.paper_weeks(weights, data, weeks_t)
    ref = tt.simulate(data, symbols, weeks_t, 30)
    assert [t for t, _ in ours] == [w.entry_t for w in ref]
    assert [r for _, r in ours[:-1]] == pytest.approx([w.r for w in ref[:-1]])
    held = sum(abs(w) * data[s]["o4"][weeks_t[-1]] / data[s]["o4"][weeks_t[-2]] for s, w in weights[weeks_t[-2]].items())
    assert ours[-1][1] - ref[-1].r == pytest.approx(held * (paper.TAKER_FEE + paper.SLIPPAGE)), \
        "разница только в издержках выхода: счёт на конец срока позиции не закрывает"


def fill_store(path, runs, snaps, halted=False):
    s = Store(str(path))
    for t, done, weights, failed in runs:
        s.rebalance(t, done, weights, [], failed)
    for ts, eq in snaps:
        s.snapshot(ts, eq, {})
    if halted:
        s.set("halted", "просадка")
    s.conn.close()
    return sqlite3.connect(str(path))


def test_honesty_verdict_account_versus_paper_minus_slack(tmp_path):
    data = price_data(["AUSDT", "BUSDT"], weeks=2, drift={"AUSDT": 0.02, "BUSDT": -0.01}, funding=0.0)
    w = {"AUSDT": 0.5, "BUSDT": -0.5}
    runs = [(MON, MON + 60_000, w, [["BUSDT", "Sell", "1", "биржа"]]), (MON, MON + 3_660_000, w, []),
            (MON + WEEK, MON + WEEK + 60_000, w, [])]
    conn = fill_store(tmp_path / "p.db", runs, [(MON - 3_600_000, 1000.0), (MON + 2 * WEEK - 3_600_000, 1015.0)])
    res = paper.evaluate(conn, 1000.0, lambda syms, a, b: data, "2026-09-21", "2026-09-28")
    cost = paper.TAKER_FEE + paper.SLIPPAGE
    week1 = 0.5 * 0.02 + 0.5 * 0.01 - 1.0 * cost                        # вход с нуля: оборот 1,0
    drift_turnover = abs(0.5 - 0.5 * 1.02) + abs(-0.5 + 0.5 * 0.99)       # доводка после дрейфа цены
    week2 = 0.5 * 0.02 + 0.5 * 0.01 - drift_turnover * cost
    assert res["paper_usdt"] == pytest.approx(1000 * (week1 + week2)), "бумага: две недели по целям первых прогонов"
    assert res["account_usdt"] == pytest.approx(15.0) and res["unfinished_weeks"] == []
    assert res["honest"] is (15.0 >= res["paper_usdt"] - 15.0)
    assert paper.verdict(100.0, 110.0, 1000.0) and not paper.verdict(94.9, 110.0, 1000.0), "допуск — 1,5 % капитала"


def test_week_without_a_clean_run_counts_as_unfinished_and_first_run_sets_the_targets(tmp_path):
    runs = [(MON, MON + 60_000, {"AUSDT": 0.3}, [["AUSDT", "Buy", "1", "x"]]),
            (MON, MON + 3_660_000, {"AUSDT": 0.9}, [["AUSDT", "Buy", "1", "x"]])]
    conn = fill_store(tmp_path / "p.db", runs, [(MON - 1, 1.0), (MON + WEEK - 1, 1.0)], halted=True)
    assert paper.week_weights(conn, [MON]) == {MON: {"AUSDT": 0.3}}
    data = price_data(["AUSDT"], weeks=1)
    res = paper.evaluate(conn, 1000.0, lambda syms, a, b: data, "2026-09-21", "2026-09-21")
    assert res["unfinished_weeks"] == [MON] and res["halted"]


class FakeAtlas:
    def __init__(self, top, missing_next=()):
        self._top = top
        self.data = {s: SimpleNamespace(open={MON: 1.0, MON + WEEK: (None if s in missing_next else 1.0)})
                     for s in top + ["BTCUSDT"]}

    def top(self, d, n=30):
        return self._top[:n]


def old_rule(atlas):
    """Правило И18 до выноса (atlas.section_candidates, 8а) — для сверки."""
    def fn(t, t_next):
        alts = [s for s in atlas.top(t // mx.DAY_MS, 31) if s != "BTCUSDT"
                and atlas.data[s].open.get(t) and atlas.data[s].open.get(t_next)][:30]
        if len(alts) < 10 or not atlas.data.get("BTCUSDT").open.get(t_next):
            return {}
        return {"BTCUSDT": 0.5, **{s: -0.5 / len(alts) for s in alts}}
    return fn


@pytest.mark.parametrize("top, missing", [
    (["BTCUSDT"] + [f"A{i}USDT" for i in range(40)], ()),
    ([f"A{i}USDT" for i in range(12)], ("A3USDT", "A4USDT")),
    ([f"A{i}USDT" for i in range(11)], ("A1USDT", "A2USDT")),
])
def test_h18_rule_is_the_same_as_before_it_was_frozen(top, missing):
    atl = FakeAtlas(top, missing)
    assert h18.btc_vs_alts(atl)(MON, MON + WEEK) == old_rule(atl)(MON, MON + WEEK)
    from backtest import atlas, listings
    assert "h18.btc_vs_alts(atlas)" in inspect.getsource(atlas.section_candidates)
    assert "h18.btc_vs_alts(atl)" in inspect.getsource(listings.h18_weeks)


def test_h18_criteria_by_the_written_definitions():
    rs = [0.01, -0.02, 0.03, 0.0, 0.01, -0.01]
    c = h18.criteria(rs)
    mean = sum(rs) / 6
    sd = math.sqrt(sum((r - mean) ** 2 for r in rs) / 5)
    assert c["mean"] == pytest.approx(mean) and c["se"] == pytest.approx(sd / math.sqrt(6))
    assert c["lower95"] == pytest.approx(mean - 1.96 * sd / math.sqrt(6))
    assert c["drawdown"] == pytest.approx(0.02), "накопленная сумма: 0,01 → −0,01"
    assert c["passed"] is False, "нижняя граница ниже −0,10 %"
    assert h18.criteria([0.004] * 10 + [0.003] * 10)["passed"] is True
    assert h18.criteria([-0.01, -0.012, -0.011, -0.009])["stop"] is True


def test_funding_mean_brings_hourly_payments_to_eight_hours():
    """Часовой контракт с той же суммой за 8 ч не должен выглядеть в 8 раз «легче» 8-часового."""
    t = MON
    hourly = ws.Series(fund_ts=[t - k * 3_600_000 for k in range(48, 0, -1)], fund_rate=[0.0001] * 48)
    eight = ws.Series(fund_ts=[t - k * 8 * 3_600_000 for k in range(6, 0, -1)], fund_rate=[0.0008] * 6)
    assert ws.funding_mean(hourly, t, 2) == pytest.approx(ws.funding_mean(eight, t, 2)) == pytest.approx(0.0008)


def test_block_bootstrap_widens_the_interval_of_a_trending_series():
    rs = [0.02] * 10 + [-0.015] * 10 + [0.02] * 10 + [-0.015] * 10
    lo1, hi1 = report.weekly_block_ci(rs, block=1)
    lo4, hi4 = report.weekly_block_ci(rs, block=8)
    assert hi4 - lo4 > hi1 - lo1, "подряд идущие недели связаны — блоки шире"
    mean = sum(rs) / len(rs)
    assert lo4 <= mean <= hi4
