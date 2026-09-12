"""
И3 (docs/TRADER_PLAN.md): кросс-секционный моментум — понедельники, сигнал без последних
суток, корзины, издержки на оборот (включая подгонку весов и закрытие), знак фандинга,
критерии и чтение из кэша истории.
"""
import pytest

from backtest import history
from backtest import momentum_xs as mx
from backtest.portfolio import SLIPPAGE, TAKER_FEE

MON = mx.day_ms("2024-09-23")  # понедельник
C = TAKER_FEE + SLIPPAGE


def test_rebalances_are_mondays_at_midnight():
    assert mx.mondays(mx.day_ms("2024-09-17"), MON + 7 * mx.DAY_MS) == [MON, MON + mx.WEEK_MS]
    assert mx.mondays(MON, MON) == [MON]


def test_the_signal_skips_the_last_day():
    end_bar = MON - mx.DAY_MS - mx.H4_MS
    closes = {end_bar - 7 * mx.DAY_MS: 100.0, end_bar: 110.0, end_bar + mx.H4_MS: 500.0, MON - mx.H4_MS: 900.0}
    assert mx.momentum(closes, MON, 7) == pytest.approx(0.10), "рывок за последние сутки в сигнал не входит"
    assert mx.momentum(closes, MON, 28) is None


def flat_symbol(mom, price=100.0, weeks=3):
    """
    Символ с моментумом mom за 7 дней к каждой ребалансировке (ровный геометрический путь
    закрытий 4h) и ровной ценой входа/выхода.
    """
    c4, o1 = {}, {}
    base = MON - mx.DAY_MS - mx.H4_MS - 28 * mx.DAY_MS
    for k in range(weeks):
        t = MON + k * mx.WEEK_MS
        end_bar = t - mx.DAY_MS - mx.H4_MS
        for tau in (end_bar, end_bar - 7 * mx.DAY_MS, end_bar - 28 * mx.DAY_MS):
            c4[tau] = 100.0 * (1 + mom) ** ((tau - base) / mx.WEEK_MS)
        o1[t] = price
    return {"c4": c4, "o1": o1, "fund": {"ts": [], "rate": []}}


def test_the_ranking_follows_momentum_not_names():
    data = {name: flat_symbol(m) for name, m in zip("ZYXWVUTSRQPO", [i / 100 for i in range(12)])}
    longs, shorts = mx.baskets(data, list(data), MON, 7)
    assert set(longs) == {"S", "R", "Q", "P", "O"} and set(shorts) == {"Z", "Y", "X", "W", "V"}
    assert mx.momentum(data["O"]["c4"], MON, 7) == pytest.approx(0.11)


def universe(n=12):
    return {f"S{i:02}": flat_symbol(i / 100) for i in range(n)}


def test_baskets_are_the_five_strongest_and_the_five_weakest():
    data = universe()
    longs, shorts = mx.baskets(data, list(data), MON, 7)
    assert set(longs) == {"S07", "S08", "S09", "S10", "S11"} and set(shorts) == {"S00", "S01", "S02", "S03", "S04"}
    del data["S11"]["o1"][MON]
    assert "S11" not in mx.baskets(data, list(data), MON, 7)[0], "без цены входа символ не ранжируется"
    assert mx.baskets(universe(9), list(universe(9)), MON, 7) is None


def test_costs_are_charged_on_turnover_including_the_final_exit():
    data = universe()
    weeks = mx.simulate(data, list(data), [MON, MON + mx.WEEK_MS, MON + 2 * mx.WEEK_MS], 7)
    assert weeks[0].costs == pytest.approx(10 * mx.WEIGHT * C), "вход: 10 позиций по 10 %"
    assert weeks[1].costs == pytest.approx(10 * mx.WEIGHT * C), "корзина та же, цены ровные — только закрытие в конце"
    assert weeks[0].r == pytest.approx(-10 * mx.WEIGHT * C)
    assert weeks[1].r == pytest.approx(-10 * mx.WEIGHT * C), "закрытие в конце вычитается из итога"


def test_a_price_drift_is_rebalanced_back_to_ten_percent_at_a_cost():
    data = universe()
    data["S11"]["o1"][MON + mx.WEEK_MS] = 110.0   # лонг вырос на 10 % — к следующей неделе вес 11 %
    data["S11"]["o1"][MON + 2 * mx.WEEK_MS] = 110.0
    weeks = mx.simulate(data, list(data), [MON, MON + mx.WEEK_MS, MON + 2 * mx.WEEK_MS], 7)
    assert weeks[0].gross == pytest.approx(mx.WEIGHT * 0.10)
    assert weeks[1].costs == pytest.approx(0.01 * C + 10 * mx.WEIGHT * C), "подгонка 1 % + закрытие"


def test_longs_gain_on_rises_and_shorts_on_falls():
    data = universe()
    data["S11"]["o1"][MON + mx.WEEK_MS] = 110.0   # лонг +10 %
    data["S00"]["o1"][MON + mx.WEEK_MS] = 90.0    # шорт −10 %
    week = mx.simulate(data, list(data), [MON, MON + mx.WEEK_MS], 7)[0]
    assert week.gross == pytest.approx(2 * mx.WEIGHT * 0.10)


def test_funding_is_paid_by_longs_on_positive_rates_and_received_by_shorts():
    data = universe()
    stamp = MON + 8 * mx.HOUR_MS
    for s in data:
        data[s]["fund"] = {"ts": [MON, stamp, MON + mx.WEEK_MS + 8 * mx.HOUR_MS], "rate": [0.5, 0.001, 0.5]}
    week = mx.simulate(data, list(data), [MON, MON + mx.WEEK_MS], 7)[0]
    assert week.funding == pytest.approx(0.0), "лонги и шорты на равный номинал — фандинг взаимно гасится"
    data["S11"]["fund"]["rate"] = [0.5, -0.002, 0.5]
    week = mx.simulate(data, list(data), [MON, MON + mx.WEEK_MS], 7)[0]
    assert week.funding == pytest.approx(mx.WEIGHT * (-0.002 - 0.001)), "лонг получает отрицательную ставку"


def wk(r, i):
    return mx.Week(MON + i * mx.WEEK_MS, MON + (i + 1) * mx.WEEK_MS, r, r, 0.0, 0.0, (), ())


def test_the_criteria_are_the_plan():
    good = [wk(0.01 if i % 4 else -0.004, i) for i in range(90)]
    assert mx.evaluate(good, bootstrap=300)["passed"]
    assert not mx.evaluate(good[:70], bootstrap=300)["checks"]["weeks"]
    assert not mx.evaluate([wk(0.002, i) for i in range(90)], bootstrap=300)["checks"]["mean"]
    assert not mx.evaluate(good[:40] + [wk(-0.2, 40)] + good[41:], bootstrap=300)["checks"]["drawdown"]
    assert mx.ALPHA == pytest.approx(0.025)


def test_load_reads_4h_closes_1h_opens_and_funding(tmp_path):
    conn = history.connect(tmp_path / "history.db")
    conn.executemany("INSERT INTO candles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     [("X", "4h", MON - mx.DAY_MS, 1.0, 1.0, 1.0, 2.0, 1.0, 1.0), ("X", "1h", MON, 3.0, 3.0, 3.0, 3.0, 1.0, 1.0)])
    conn.execute("INSERT INTO funding VALUES ('X', ?, 0.0001)", (MON + mx.HOUR_MS,))
    conn.commit()
    data = mx.load(conn, ["X"], MON, MON + mx.DAY_MS)
    conn.close()
    assert data["X"]["c4"][MON - mx.DAY_MS] == 2.0 and data["X"]["o1"][MON] == 3.0
    assert data["X"]["fund"]["ts"] == [MON + mx.HOUR_MS]
