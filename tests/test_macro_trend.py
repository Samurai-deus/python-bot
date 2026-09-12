"""
И8 (docs/TRADER_PLAN.md): трендследование по многим рынкам — конец месяца из дневного ряда,
волатильность, знак и вес, рынок без данных, издержки на оборот с подгонкой, критерии, отложенный
конец, разбор FRED/LBMA и чтение из кэша.
"""
import math

import pytest

from backtest import macro_data as md
from backtest import macro_trend as mt


def daily_path(start_year, months, monthly_ret, noise=0.01):
    """Дневной ряд: 21 торговый день в месяц, дневной снос под monthly_ret и колебание ±noise (лог)."""
    out, v = [], 100.0
    for k in range(months):
        y, m = start_year + k // 12, k % 12 + 1
        for day in range(1, 22):
            v *= math.exp(math.log(1 + monthly_ret) / 21 + (noise if day % 2 else -noise))
            out.append((f"{y:04d}-{m:02d}-{day:02d}", v))
    return out


def market(**series):
    return {s: {"daily": d, "ends": mt.month_ends(d)} for s, d in series.items()}


def test_month_end_is_the_last_trading_day():
    d = [("2000-01-03", 1.0), ("2000-01-31", 2.0), ("2000-02-01", 3.0), ("2000-02-28", 4.0)]
    assert mt.month_ends(d) == {"2000-01": 1, "2000-02": 3}
    assert mt.month_add("2000-11", 3) == "2001-02" and mt.month_add("2000-01", -1) == "1999-12"


def test_annual_vol_from_daily_log_returns():
    d = daily_path(2000, 6, 0.0, noise=0.01)
    v = mt.annual_vol(d, len(d) - 1)
    rets = [math.log(d[k][1] / d[k - 1][1]) for k in range(len(d) - 60, len(d))]
    mean = sum(rets) / 60
    assert v == pytest.approx(math.sqrt(sum((r - mean) ** 2 for r in rets) / 59 * 252))
    assert mt.annual_vol(d, 30) is None


def test_sign_follows_the_lookback_return_and_size_is_inverse_to_volatility():
    data = market(UP=daily_path(2000, 30, 0.02, noise=0.004), DOWN=daily_path(2000, 30, -0.02, noise=0.008))
    w = mt.targets(data, "2001-12", 12)
    vol_up = mt.annual_vol(data["UP"]["daily"], data["UP"]["ends"]["2001-12"])
    assert w["UP"] == pytest.approx(mt.TARGET / vol_up) and w["UP"] > 0 and w["DOWN"] < 0
    assert abs(w["DOWN"]) < w["UP"], "у волатильного рынка вес меньше"


def test_the_signal_uses_only_prices_known_at_the_rebalance():
    # 12 месяцев ровно, в месяце ребалансировки +5 %, в следующем −20 %: в конце месяца тренд вверх
    d = daily_path(2000, 14, 0.0, noise=0.003)
    ends = mt.month_ends(d)
    bump = [(day, v * (1.05 if day[:7] >= "2001-01" else 1.0) * (0.80 if day[:7] >= "2001-02" else 1.0)) for day, v in d]
    w = mt.targets(market(X=bump), "2001-01", 12)
    assert ends and w["X"] > 0, "цена следующего месяца в сигнал не входит"


def test_the_weight_is_capped_and_a_market_without_history_is_skipped():
    calm = daily_path(2000, 30, 0.02, noise=0.0001)
    late = daily_path(2001, 18, 0.02)
    w = mt.targets(market(CALM=calm, LATE=late), "2001-12", 12)
    assert w == {"CALM": pytest.approx(mt.CAP)}, "у LATE нет цены 12 месяцев назад"


def test_month_result_and_costs_on_turnover_with_drift():
    data = market(UP=daily_path(2000, 30, 0.02, noise=0.004))
    months = mt.simulate(data, ["2001-12", "2002-01"], 12)
    w0 = months[0].weights["UP"]
    ends, d = data["UP"]["ends"], data["UP"]["daily"]
    p = {m: d[ends[m]][1] for m in ("2001-12", "2002-01", "2002-02")}
    assert months[0].gross == pytest.approx(w0 * (p["2002-01"] / p["2001-12"] - 1))
    assert months[0].costs == pytest.approx(w0 * mt.COST)
    drifted = w0 * p["2002-01"] / p["2001-12"]
    assert months[1].costs == pytest.approx(abs(months[1].weights["UP"] - drifted) * mt.COST)


def mo(r, i):
    return mt.Month(mt.month_add("1980-01", i), r, r, 0.0, {})


def test_the_criteria_are_the_plan():
    good = [mo(0.01 if i % 3 else -0.004, i) for i in range(400)]
    assert mt.evaluate(good, bootstrap=300)["passed"]
    assert not mt.evaluate(good[:250], bootstrap=300)["checks"]["months"]
    assert not mt.evaluate([mo(0.003, i) for i in range(400)], bootstrap=300)["checks"]["mean"]
    assert not mt.evaluate(good[:100] + [mo(-0.3, 100)] + good[101:], bootstrap=300)["checks"]["drawdown"]
    assert mt.ALPHA == pytest.approx(0.025)


def test_holdout_verdict_needs_a_positive_mean_and_a_bounded_drawdown():
    assert mt.holdout_verdict([mo(0.005, i) for i in range(160)])[0]
    assert not mt.holdout_verdict([mo(-0.001, i) for i in range(160)])[0]
    assert not mt.holdout_verdict([mo(0.01, 0), mo(-0.3, 1)] + [mo(0.01, i) for i in range(2, 160)])[0]


def test_fred_and_lbma_are_parsed_and_stored(tmp_path):
    fred = "observation_date,DEXJPUS\n1971-01-04,357.73\n1971-01-05,.\n1971-01-06,357.81\n"
    lbma = '[{"d":"1968-04-01","v":[37.7,15.68,null]},{"d":"1968-04-02","v":[null,1,2]}]'
    assert md.parse_fred(fred, "DEXJPUS") == [("1971-01-04", 357.73), ("1971-01-06", 357.81)]
    assert md.parse_lbma(lbma) == [("1968-04-01", 37.7)]
    conn = md.connect(tmp_path / "history.db")
    assert md.store(conn, "USDJPY", md.parse_fred(fred, "DEXJPUS")) == 2
    data = mt.load(conn)
    conn.close()
    assert data["USDJPY"]["daily"][-1] == ("1971-01-06", 357.81)
