"""
И4 (docs/TRADER_PLAN.md): трендследование по времени — тренд и волатильность по закрытым 4h,
знак и размер позиции, предел на монету, издержки на оборот, критерии и чтение из кэша.
"""
import math

import pytest

from backtest import history
from backtest import trend_ts as tt
from backtest.portfolio import SLIPPAGE, TAKER_FEE

MON = tt.mx.day_ms("2021-01-04")  # понедельник
DAY, H4, WEEK = tt.DAY_MS, tt.H4_MS, tt.WEEK_MS
C = TAKER_FEE + SLIPPAGE


def coin(drift, swing, weeks=3, start_price=100.0):
    """Закрытия 4h: дневной снос drift, по дням колебание ±swing (лог); open 4h-бара = закрытие предыдущего."""
    c4, o4 = {}, {}
    base = MON - 130 * DAY
    for tau in range(base, MON + weeks * WEEK + DAY, H4):
        day = (tau + H4) // DAY
        c4[tau] = start_price * math.exp(drift * (tau + H4 - base) / DAY + (swing if day % 2 else -swing))
    for tau in c4:
        o4[tau + H4] = c4[tau]
    return {"c4": c4, "o4": o4, "fund": {"ts": [], "rate": []}}


def test_trend_uses_bars_closed_by_the_moment():
    closes = {MON - H4 - 30 * DAY: 100.0, MON - H4: 120.0, MON: 1.0}
    assert tt.trend(closes, MON, 30) == pytest.approx(0.20)
    assert tt.trend(closes, MON, 90) is None


def test_annual_vol_from_daily_closes():
    a = 0.01
    closes = {MON - k * DAY - H4: 100.0 * math.exp(a if k % 2 else -a) for k in range(31)}
    rets = [(-a if k % 2 else a) - (a if k % 2 else -a) for k in range(30)]  # лог-доходность дня k
    mean = sum(rets) / 30
    expected = math.sqrt(sum((r - mean) ** 2 for r in rets) / 29 * 365)
    assert tt.annual_vol(closes, MON) == pytest.approx(expected)
    del closes[MON - 10 * DAY - H4]
    assert tt.annual_vol(closes, MON) is None


def test_sign_follows_the_trend_and_size_is_inverse_to_volatility():
    data = {"UP": coin(0.01, 0.01), "DOWN": coin(-0.01, 0.02)}
    w = tt.targets(data, list(data), MON, MON + WEEK, 30)
    vol_up, vol_down = tt.annual_vol(data["UP"]["c4"], MON), tt.annual_vol(data["DOWN"]["c4"], MON)
    assert w["UP"] == pytest.approx(tt.TARGET / (vol_up * 2)) and w["UP"] > 0
    assert w["DOWN"] == pytest.approx(-tt.TARGET / (vol_down * 2))
    assert abs(w["DOWN"]) < w["UP"], "у волатильной монеты вес меньше"


def test_the_weight_is_capped_and_a_coin_without_prices_drops_out():
    data = {"CALM": coin(0.01, 0.0005), "UP": coin(0.01, 0.02)}  # у UP вес ниже предела и при K = 1
    w = tt.targets(data, list(data), MON, MON + WEEK, 30)
    assert w["CALM"] == pytest.approx(tt.CAP)
    del data["CALM"]["o4"][MON + WEEK]
    w = tt.targets(data, list(data), MON, MON + WEEK, 30)
    assert list(w) == ["UP"] and w["UP"] == pytest.approx(tt.TARGET / tt.annual_vol(data["UP"]["c4"], MON)), "K = 1"


def test_costs_on_turnover_and_the_final_exit_and_gains_follow_the_side():
    data = {"UP": coin(0.01, 0.01), "DOWN": coin(-0.01, 0.01)}
    weeks_t = [MON, MON + WEEK, MON + 2 * WEEK]
    weeks = tt.simulate(data, list(data), weeks_t, 30)
    first = tt.targets(data, list(data), MON, MON + WEEK, 30)
    assert weeks[0].costs == pytest.approx(sum(abs(v) for v in first.values()) * C)
    assert weeks[0].gross > 0, "лонг растущей и шорт падающей монеты зарабатывают"
    assert weeks[-1].costs > 0 and weeks[-1].r < weeks[-1].gross - weeks[-1].funding
    second = tt.targets(data, list(data), MON + WEEK, MON + 2 * WEEK, 30)
    drift = {s: w * data[s]["o4"][MON + WEEK] / data[s]["o4"][MON] for s, w in first.items()}
    close = {s: w * data[s]["o4"][MON + 2 * WEEK] / data[s]["o4"][MON + WEEK] for s, w in second.items()}
    rebalance = sum(abs(second.get(s, 0.0) - drift.get(s, 0.0)) for s in set(second) | set(drift))
    assert weeks[1].costs == pytest.approx((rebalance + sum(abs(v) for v in close.values())) * C), \
        "вторая неделя: подгонка весов после дрейфа цены + закрытие в конце"


def test_funding_is_paid_by_a_long_on_a_positive_rate():
    data = {"UP": coin(0.01, 0.01)}
    data["UP"]["fund"] = {"ts": [MON + 8 * 3_600_000], "rate": [0.001]}
    week = tt.simulate(data, ["UP"], [MON, MON + WEEK], 30)[0]
    w = tt.targets(data, ["UP"], MON, MON + WEEK, 30)["UP"]
    assert week.funding == pytest.approx(w * 0.001 * data["UP"]["o4"][MON + 8 * 3_600_000] / data["UP"]["o4"][MON])


def wk(r, i):
    return tt.mx.Week(MON + i * WEEK, MON + (i + 1) * WEEK, r, r, 0.0, 0.0, (), ())


def test_the_criteria_are_the_plan():
    good = [wk(0.004 if i % 4 else -0.002, i) for i in range(160)]
    assert tt.evaluate(good, bootstrap=300)["passed"]
    assert not tt.evaluate(good[:120], bootstrap=300)["checks"]["weeks"]
    assert not tt.evaluate([wk(0.001, i) for i in range(160)], bootstrap=300)["checks"]["mean"]
    assert not tt.evaluate(good[:50] + [wk(-0.2, 50)] + good[51:], bootstrap=300)["checks"]["drawdown"]
    assert tt.ALPHA == pytest.approx(0.025)


def test_load_reads_4h_closes_and_opens_and_funding(tmp_path):
    conn = history.connect(tmp_path / "history.db")
    conn.execute("INSERT INTO candles VALUES ('X', '4h', ?, 1.0, 1.0, 1.0, 2.0, 1.0, 1.0)", (MON,))
    conn.execute("INSERT INTO funding VALUES ('X', ?, 0.0001)", (MON + 3_600_000,))
    conn.commit()
    data = tt.load(conn, ["X"], MON, MON + DAY)
    conn.close()
    assert data["X"]["c4"][MON] == 2.0 and data["X"]["o4"][MON] == 1.0 and data["X"]["fund"]["ts"] == [MON + 3_600_000]
