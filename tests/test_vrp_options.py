"""
И7 (docs/TRADER_PLAN.md): премия за волатильность — цена ATM-страддла, волатильность из DVOL
предыдущего дня минус 2 пункта, выплата на экспирации, издержки, размер транша, блочный
бутстреп, критерии, чтение DVOL из кэша.
"""
import math

import pytest

from backtest import history
from backtest import vrp_options as vo

DAY = vo.DAY_MS
MON8 = vo.mx.day_ms("2023-01-02") + 8 * vo.HOUR_MS  # понедельник 08:00 UTC


def test_atm_straddle_price_matches_the_textbook_approximation():
    price = vo.atm_straddle(0.5, 30 / 365)
    assert price == pytest.approx(math.sqrt(2 / math.pi) * 0.5 * math.sqrt(30 / 365), rel=0.01)
    assert vo.atm_straddle(0.8, 30 / 365) > price, "дороже при большей волатильности"


def test_volatility_is_yesterdays_dvol_close_minus_the_haircut():
    day0 = MON8 - MON8 % DAY
    dvol = {day0 - DAY: 62.0, day0: 90.0}
    assert vo.iv_at(dvol, MON8) == pytest.approx(0.60), "сегодняшний DVOL ещё не закрыт — берём вчерашний"
    assert vo.iv_at({}, MON8) is None


def test_a_tranche_earns_the_premium_minus_the_move_and_the_fees():
    day0 = MON8 - MON8 % DAY
    dvol = {day0 - DAY: 52.0}
    o4 = {MON8: 100.0, MON8 + 30 * DAY: 104.0}
    premium, payoff, fees = vo.tranche_result(o4, dvol, MON8)
    assert premium == pytest.approx(vo.atm_straddle(0.50, 30 / 365))
    assert payoff == pytest.approx(0.04) and fees == pytest.approx(2 * (0.0003 + 0.00015))
    o4[MON8 + 30 * DAY] = 70.0
    assert vo.tranche_result(o4, dvol, MON8)[1] == pytest.approx(0.30), "обвал на 30 % — выплата 30 % номинала"
    del o4[MON8 + 30 * DAY]
    assert vo.tranche_result(o4, dvol, MON8) is None


def test_the_week_sums_both_coins_at_the_tranche_size():
    day0 = MON8 - MON8 % DAY
    data = {"BTC": {"o4": {MON8: 100.0, MON8 + 30 * DAY: 100.0}, "dvol": {day0 - DAY: 52.0}},
            "ETH": {"o4": {MON8: 10.0, MON8 + 30 * DAY: 11.0}, "dvol": {day0 - DAY: 72.0}}}
    week = vo.simulate(data, [MON8])[0]
    btc = vo.atm_straddle(0.50, 30 / 365) - 0 - 2 * (0.0003 + 0.00015)
    eth = vo.atm_straddle(0.70, 30 / 365) - 0.10 - 2 * (0.0003 + 0.00015)
    assert week.r == pytest.approx(vo.TRANCHE * (btc + eth)) and week.coins == ("BTC", "ETH")
    assert week.exit_t == MON8 + 30 * DAY


def test_entries_are_monday_mornings_that_expire_before_the_end():
    starts = vo.entry_times(vo.mx.day_ms("2023-01-02"), vo.mx.day_ms("2023-03-01"))
    assert starts[0] == MON8 and all(t % vo.WEEK_MS == starts[0] % vo.WEEK_MS for t in starts)
    assert all(t + 30 * DAY <= vo.mx.day_ms("2023-03-01") for t in starts)


def test_the_block_bootstrap_is_wider_for_dependent_neighbours():
    runs = [0.01] * 5 + [-0.008] * 5
    dependent = runs * 20                      # соседние наблюдения похожи — блоками
    alternating = [0.01, -0.008] * 50          # то же среднее, соседи независимы
    dep = vo.block_ci(dependent, bootstrap=800)
    alt = vo.block_ci(alternating, bootstrap=800)
    assert dep[1] - dep[0] > 1.5 * (alt[1] - alt[0]), "блоки ловят зависимость соседей — интервал заметно шире"


def tr(r, i):
    return vo.Tranche(MON8 + i * vo.WEEK_MS, MON8 + i * vo.WEEK_MS + 30 * DAY, r, 0.0, 0.0, 0.0, ("BTC",))


def test_the_criteria_are_the_plan():
    good = [tr(0.004 if i % 4 else -0.002, i) for i in range(160)]
    assert vo.evaluate(good, bootstrap=300)["passed"]
    assert not vo.evaluate(good[:120], bootstrap=300)["checks"]["weeks"]
    assert not vo.evaluate([tr(0.001, i) for i in range(160)], bootstrap=300)["checks"]["mean"]
    assert not vo.evaluate(good[:50] + [tr(-0.2, 50)] + good[51:], bootstrap=300)["checks"]["drawdown"]


def test_load_reads_dvol_from_the_history_cache(tmp_path):
    conn = history.connect(tmp_path / "history.db")
    conn.execute("CREATE TABLE dvol (currency TEXT, ts INTEGER, open REAL, high REAL, low REAL, close REAL)")
    conn.execute("INSERT INTO dvol VALUES ('BTC', ?, 1.0, 1.0, 1.0, 55.0)", (MON8 - MON8 % DAY,))
    conn.execute("INSERT INTO candles VALUES ('BTCUSDT', '4h', ?, 100.0, 1.0, 1.0, 1.0, 1.0, 1.0)", (MON8,))
    conn.commit()
    data = vo.load(conn, MON8, MON8 + DAY)
    conn.close()
    assert data["BTC"]["dvol"][MON8 - MON8 % DAY] == 55.0 and data["BTC"]["o4"][MON8] == 100.0
