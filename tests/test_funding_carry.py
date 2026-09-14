"""
И12 (backtest/funding_carry.py): окна фандинга без заглядывания вперёд, отбор, результат пары
(фандинг + базис), издержки на оборот, блочный бутстреп, критерии, докачка спота.
"""
import sqlite3

import pytest

from backtest import funding_carry as fc

W = fc.WEEK
T = fc.START


def coin(rates=(), perp=None, spot=None, since=T - fc.WARMUP - fc.DAY, weeks=3):
    """Монета с ценами на каждый час-понедельник и фандингом раз в 8 ч от since."""
    grid = [T + i * W for i in range(-1, weeks + 1)] + [since]
    return {"funding": sorted([(since, 0.0)] + list(rates)),
            "perp": perp or {g: 100.0 for g in grid}, "spot": spot or {g: 100.0 for g in grid}}


def test_funding_window_is_start_exclusive_end_inclusive():
    rows = [(10, 0.1), (20, 0.2), (30, 0.4)]
    assert fc.funding_sum(rows, 10, 30) == pytest.approx(0.6)
    assert fc.funding_sum(rows, 9, 20) == pytest.approx(0.3)


def test_selection_uses_only_past_funding_and_positive_sums():
    data = {
        "AUSDT": coin([(T - fc.DAY, 0.003)]),
        "BUSDT": coin([(T - fc.DAY, 0.002), (T + 1, 0.9)]),     # будущая ставка не должна поднять B выше A
        "CUSDT": coin([(T - fc.DAY, -0.001)]),                   # отрицательная сумма — не берётся
        "DUSDT": coin([(T - 8 * fc.DAY, 0.5)]),                  # вне окна 7 дней
    }
    assert fc.select(data, T, 5) == ["AUSDT", "BUSDT"]
    assert fc.select(data, T, 1) == ["AUSDT"]


def test_warmup_and_missing_exit_price_make_a_coin_ineligible():
    young = coin([(T - fc.DAY, 0.01)], since=T - 10 * fc.DAY)
    no_exit = coin([(T - fc.DAY, 0.01)])
    del no_exit["spot"][T + W]
    young_funding = coin([(T - fc.DAY, 0.01)])          # цены давно, а контракт с фандингом — 10 дней
    young_funding["funding"] = [(T - 10 * fc.DAY, 0.0), (T - fc.DAY, 0.01)]
    assert not fc.eligible(young, T) and not fc.eligible(no_exit, T)
    assert not fc.eligible(young_funding, T), "30 дней нужны и у фандинга, не только у цен"
    assert fc.eligible(coin([(T - fc.DAY, 0.01)]), T)


def test_pair_result_is_funding_plus_spot_minus_perp():
    d = coin([(T - fc.DAY, 0.01), (T + 8 * fc.H, 0.002), (T + W, 0.003), (T + W + 1, 0.5)])
    d["spot"][T + W] = 110.0
    d["perp"][T + W] = 109.0
    f, basis = fc.pair_parts(d, T)
    assert f == pytest.approx(0.005), "ставки в (t; t + 7 дн]"
    assert basis == pytest.approx(0.10 - 0.09)


def test_costs_are_charged_on_turnover_only():
    rates = [(T - fc.DAY + i * W, 0.01) for i in range(4)]
    data = {"AUSDT": coin(rates), "BUSDT": coin(rates)}
    weeks = fc.run(data, 5, [T, T + W, T + 2 * W])
    assert fc.SIDE_COST == pytest.approx(0.00255) and fc.CAPITAL_PER_NOTIONAL == 1.1, "числа из правила И12"
    n = 1 / (2 * fc.CAPITAL_PER_NOTIONAL)
    assert weeks[0].cost == pytest.approx(fc.SIDE_COST * 2 * n), "открытие двух пар"
    assert weeks[1].cost == pytest.approx(0.0), "состав тот же — без издержек"
    assert weeks[2].cost == pytest.approx(fc.SIDE_COST * 2 * n), "последняя неделя — закрытие всего"
    assert weeks[1].r == pytest.approx(2 * n * 0.01), "фандинг 0,01 на номинал, цены не менялись"


def test_reweighting_when_the_count_changes_is_paid():
    rates_a = [(T - fc.DAY + i * W, 0.01) for i in range(4)]
    rates_b = [(T - fc.DAY, 0.01)]               # у B фандинг только до первой недели
    data = {"AUSDT": coin(rates_a), "BUSDT": coin(rates_b)}
    weeks = fc.run(data, 5, [T, T + W, T + 2 * W])
    half, full = 1 / (2 * fc.CAPITAL_PER_NOTIONAL), 1 / fc.CAPITAL_PER_NOTIONAL
    assert weeks[1].pairs == 1
    assert weeks[1].cost == pytest.approx(fc.SIDE_COST * ((full - half) + half)), "A доливается, B закрывается"


def test_block_ci_brackets_the_mean_and_is_deterministic():
    rs = [0.004, 0.002, 0.003, 0.001] * 50
    low, high = fc.block_ci(rs)
    assert low <= 0.0025 <= high and low > 0
    assert fc.block_ci(rs) == (low, high)


def weeks_with(rs, start=fc.START):
    return [fc.Week(start + i * W, r, 5, r, 0.0, 0.0) for i, r in enumerate(rs)]


def test_criteria_on_the_visible_period_only():
    good = weeks_with([0.004, 0.003] * 92)                     # 184 недели до 09.03.2026
    tail = weeks_with([-0.5] * 5, fc.HOLDOUT_FIRST)
    st = fc.evaluate(good + tail)
    assert st["weeks"] == 184 and st["passed"], st["checks"]
    assert fc.holdout(good + tail)["contradiction"]


@pytest.mark.parametrize("rs, failed", [
    ([0.004, 0.003] * 70, "weeks"),         # 140 < 150
    ([0.003, 0.0027] * 92, "mean"),         # 0,285 % < 0,29 %
    (([0.06] * 4 + [-0.054] * 4) * 23, "ci"),   # средняя +0,3 %, но блоки по 4 недели очень разные
])
def test_each_criterion_can_fail(rs, failed):
    assert not fc.evaluate(weeks_with(rs))["checks"][failed]


def test_drawdown_limit():
    rs = [0.004] * 100 + [-0.06, -0.06] + [0.004] * 82
    st = fc.evaluate(weeks_with(rs))
    assert st["drawdown"] == pytest.approx(0.12) and not st["checks"]["drawdown"]


def test_spot_name_and_exclusion():
    assert fc.spot_name("1000PEPEUSDT") == "PEPEUSDT" and fc.spot_name("BTCUSDT") == "BTCUSDT"
    assert "SHIB1000USDT" in fc.EXCLUDE


class FakeApi:
    def __init__(self, hours):
        self.hours = hours
        self.calls = 0

    def get(self, path, params):
        self.calls += 1
        assert params["category"] == "spot"
        rows = [[str(ts), "1", "0", "0", "2", "0", "0"] for ts in self.hours if params["start"] <= ts <= params["end"]]
        return {"list": sorted(rows, key=lambda r: -int(r[0]))[:params["limit"]]}


def test_sync_spot_pages_forward_and_resumes_from_the_cache():
    start = 1_600_000_000_000 - 1_600_000_000_000 % fc.H
    hours = [start + i * fc.H for i in range(2500) if i not in range(1200, 1300)]   # дыра в данных биржи
    conn = sqlite3.connect(":memory:")
    api = FakeApi(hours)
    assert fc.sync_spot(conn, api, "XUSDT", start, start + 2500 * fc.H) == 2400
    assert api.calls == 3
    assert fc.sync_spot(conn, FakeApi(hours), "XUSDT", start, start + 2500 * fc.H) == 0, "докачка с последней записи"
