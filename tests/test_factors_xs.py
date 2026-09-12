"""
И5 (docs/TRADER_PLAN.md): факторы между монетами — оборот и волатильность по закрытым 4h,
корзины (лонг — наименьшее значение фактора), издержки на оборот, знак фандинга, критерии,
чтение оборота из кэша.
"""
import math

import pytest

from backtest import factors_xs as fx
from backtest import history
from backtest.portfolio import SLIPPAGE, TAKER_FEE

MON = fx.mx.day_ms("2023-01-02")  # понедельник
DAY, H4, WEEK = fx.DAY_MS, fx.H4_MS, fx.WEEK_MS
C = TAKER_FEE + SLIPPAGE


def coin(level, swing, weeks=3, price=100.0):
    """4h-бары: оборот level на бар, дневное колебание закрытий ±swing (лог), ровные open для входа/выхода."""
    c4, o4, t4 = {}, {}, {}
    for tau in range(MON - 40 * DAY, MON + weeks * WEEK + DAY, H4):
        day = (tau + H4) // DAY
        c4[tau] = price * math.exp(swing if day % 2 else -swing)
        t4[tau] = level
    for k in range(weeks + 1):
        o4[MON + k * WEEK] = price
    return {"c4": c4, "o4": o4, "t4": t4, "fund": {"ts": [], "rate": []}}


def test_turnover_sums_28_days_of_closed_bars():
    t4 = {MON - k * H4: 1.0 for k in range(1, 28 * 6 + 1)}
    t4[MON] = 1000.0  # бар, открывшийся в MON, ещё не закрыт
    assert fx.turnover(t4, MON) == pytest.approx(168.0)
    del t4[MON - 5 * H4]
    assert fx.turnover(t4, MON) is None


def test_volatility_from_daily_closes():
    a = 0.02
    closes = {MON - k * DAY - H4: 100.0 * math.exp(a if k % 2 else -a) for k in range(29)}
    rets = [2 * a if k % 2 == 0 else -2 * a for k in range(28)]
    mean = sum(rets) / 28
    assert fx.volatility(closes, MON) == pytest.approx(math.sqrt(sum((r - mean) ** 2 for r in rets) / 27 * 365))


def universe():
    return {f"S{i:02}": coin(level=100.0 * (i + 1), swing=0.001 * (i + 1)) for i in range(12)}


@pytest.mark.parametrize("factor", ["liquidity", "lowvol"])
def test_long_the_lowest_short_the_highest(factor):
    data = universe()
    longs, shorts = fx.baskets(data, list(data), MON, MON + WEEK, factor)
    assert set(longs) == {"S00", "S01", "S02", "S03", "S04"} and set(shorts) == {"S07", "S08", "S09", "S10", "S11"}


def test_the_two_factors_rank_by_their_own_measure():
    # оборот растёт с номером, волатильность падает — корзины двух факторов обязаны разойтись
    data = {f"S{i:02}": coin(level=100.0 * (i + 1), swing=0.001 * (12 - i)) for i in range(12)}
    assert set(fx.baskets(data, list(data), MON, MON + WEEK, "liquidity")[0]) == {"S00", "S01", "S02", "S03", "S04"}
    assert set(fx.baskets(data, list(data), MON, MON + WEEK, "lowvol")[0]) == {"S07", "S08", "S09", "S10", "S11"}


def test_a_symbol_without_prices_is_not_ranked_and_too_few_symbols_skip_the_week():
    data = universe()
    del data["S00"]["o4"][MON + WEEK]
    assert "S00" not in fx.baskets(data, list(data), MON, MON + WEEK, "liquidity")[0]
    small = {k: v for k, v in list(universe().items())[:9]}
    assert fx.baskets(small, list(small), MON, MON + WEEK, "liquidity") is None


def test_costs_on_turnover_and_the_final_exit():
    data = universe()
    weeks = fx.simulate(data, list(data), [MON, MON + WEEK, MON + 2 * WEEK], "liquidity")
    assert weeks[0].costs == pytest.approx(10 * fx.WEIGHT * C)
    assert weeks[1].costs == pytest.approx(10 * fx.WEIGHT * C), "корзина та же, цены ровные — только закрытие"
    assert weeks[1].r == pytest.approx(-10 * fx.WEIGHT * C)


def test_a_price_drift_is_rebalanced_back_to_ten_percent():
    data = universe()
    data["S00"]["o4"][MON + WEEK] = 110.0   # лонг вырос на 10 % — к следующей неделе вес 11 %
    data["S00"]["o4"][MON + 2 * WEEK] = 110.0
    weeks = fx.simulate(data, list(data), [MON, MON + WEEK, MON + 2 * WEEK], "liquidity")
    assert weeks[0].gross == pytest.approx(fx.WEIGHT * 0.10)
    assert weeks[1].costs == pytest.approx(0.01 * C + 10 * fx.WEIGHT * C)


def test_funding_is_paid_by_longs_on_positive_rates():
    data = universe()
    stamp = MON + 8 * 3_600_000
    for s in data:
        data[s]["fund"] = {"ts": [stamp], "rate": [0.001]}
    data["S00"]["fund"] = {"ts": [stamp], "rate": [-0.002]}
    week = fx.simulate(data, list(data), [MON, MON + WEEK], "liquidity")[0]
    assert week.funding == pytest.approx(fx.WEIGHT * (-0.002 - 0.001))


def wk(r, i):
    return fx.mx.Week(MON + i * WEEK, MON + (i + 1) * WEEK, r, r, 0.0, 0.0, (), ())


def test_the_criteria_are_the_plan():
    good = [wk(0.004 if i % 4 else -0.002, i) for i in range(160)]
    assert fx.evaluate(good, bootstrap=300)["passed"]
    assert not fx.evaluate(good[:120], bootstrap=300)["checks"]["weeks"]
    assert not fx.evaluate([wk(0.001, i) for i in range(160)], bootstrap=300)["checks"]["mean"]
    assert not fx.evaluate(good[:50] + [wk(-0.2, 50)] + good[51:], bootstrap=300)["checks"]["drawdown"]
    assert fx.ALPHA == pytest.approx(0.025)


def test_load_reads_turnover_from_the_history_cache(tmp_path):
    conn = history.connect(tmp_path / "history.db")
    conn.execute("INSERT INTO candles VALUES ('X', '4h', ?, 1.0, 1.0, 1.0, 2.0, 5.0, 7.0)", (MON,))
    conn.commit()
    data = fx.load(conn, ["X"], MON, MON + DAY)
    conn.close()
    assert data["X"]["t4"][MON] == 7.0 and data["X"]["c4"][MON] == 2.0 and data["X"]["o4"][MON] == 1.0
