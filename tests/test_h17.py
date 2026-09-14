"""И17 (docs/TRADER_PLAN.md): продолжение — зеркало разворота; недельный результат из сделок; веса по обратному разбросу; пороги."""
import pytest

from backtest import bar_rules as br
from backtest import h17
from backtest import wide_search as ws
from tests.test_wide_search import D0, MON, series


def test_continuation_is_the_mirror_of_reversal():
    data = {f"S{i:02}": series(drift=i / 1000) for i in range(12)}
    data["S00"].close[D0 - 1] *= 3  # рывок в последние сутки — для продолжения это лучший
    w = ws.weights(data, h17.CONT, MON, MON + ws.WEEK_MS)
    r = ws.weights(data, {**h17.CONT, "family": "reversal"}, MON, MON + ws.WEEK_MS)
    assert {s for s, v in w.items() if v > 0} == {s for s, v in r.items() if v < 0}
    assert "S00" in {s for s, v in w.items() if v > 0}
    assert sum(abs(v) for v in w.values()) == pytest.approx(1.0)


def test_weekly_result_from_trades_sums_by_entry_week_and_zero_fills():
    weeks = [MON + k * ws.WEEK_MS for k in range(4)]
    trades = [br.Trade("A", MON + 3 * br.HOUR_MS, MON + 5 * br.DAY_MS, 0.02, 1, "time"),
              br.Trade("B", MON + 2 * br.DAY_MS, MON + 9 * br.DAY_MS, -0.01, -1, "time"),
              br.Trade("C", MON + 2 * ws.WEEK_MS + 1, MON + 3 * ws.WEEK_MS, 0.05, 1, "stop"),
              br.Trade("D", MON - 1, MON, 1.0, 1, "time")]  # до первой недели — не учитывается
    w = h17.weekly_from_trades(trades, weeks)
    assert w == {weeks[0]: pytest.approx(0.1 * 0.01), weeks[1]: 0.0, weeks[2]: pytest.approx(0.005)}


def test_inverse_sd_weights_and_combination():
    legs = {"calm": [0.01, -0.01, 0.01, -0.01], "wild": [0.03, -0.03, 0.03, -0.03]}
    w = h17.inverse_sd_weights(legs)
    assert w["calm"] == pytest.approx(0.75) and w["wild"] == pytest.approx(0.25)
    weeks = [MON, MON + ws.WEEK_MS]
    combo = h17.combine({"calm": {MON: 0.01, MON + ws.WEEK_MS: 0.0}, "wild": {MON: -0.03}}, w, weeks)
    assert combo == [(MON, pytest.approx(0.0075 - 0.0075)), (MON + ws.WEEK_MS, 0.0)]


def test_holdout_thresholds_follow_the_plan():
    weeks = [MON + k * ws.WEEK_MS for k in range(40)]
    legs = {"cont1d": {t: 0.0055 + 0.02 * (-1) ** k for k, t in enumerate(weeks[:-1])},
            "trend30w": {t: 0.002 + 0.01 * (-1) ** k for k, t in enumerate(weeks[:-1])},
            "ma_daily": {t: 0.001 for t in weeks[:-1]}}
    holdout_start = weeks[30]
    out = h17.run(legs, weeks, holdout_start, show_holdout=False)
    assert "holdout" not in out["legs"]["cont1d"], "без --holdout отложенный конец не открывается"
    out = h17.run(legs, weeks, holdout_start, show_holdout=True)
    h = out["legs"]["cont1d"]["holdout"]
    assert h["weeks"] == 9 and h["not_contradicted"] and h["demo"], "средняя ≈ +0,55 % выше порога демо +0,30 %"
    legs["cont1d"] = {t: (0.0055 + 0.02 * (-1) ** k) if t < holdout_start else -0.05 for k, t in enumerate(weeks[:-1])}
    h = h17.run(legs, weeks, holdout_start, show_holdout=True)["legs"]["cont1d"]["holdout"]
    assert not h["not_contradicted"] and not h["demo"]
    legs["cont1d"] = {t: (0.0055 + 0.02 * (-1) ** k) if t < holdout_start else 0.001 + 0.01 * (-1) ** k
                      for k, t in enumerate(weeks[:-1])}
    h = h17.run(legs, weeks, holdout_start, show_holdout=True)["legs"]["cont1d"]["holdout"]
    assert 0 < h["mean"] < h17.DEMO_MIN_MEAN and h["threshold"] < 0
    assert h["not_contradicted"] and not h["demo"], "около +0,2 % не противоречит (порог < 0), но ниже порога демо +0,30 %"
