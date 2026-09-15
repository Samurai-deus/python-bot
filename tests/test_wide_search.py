"""
И15 (docs/TRADER_PLAN.md): сетка 70 вариантов, вселенная на момент t (история и оборот на тот
момент), веса семейств при валовой 1, выбор варианта только по прошлым годам, критерии и
правило отложенного конца, сборка дневных рядов из 4h-свечей кэша.
"""
import pytest

from backtest import history
from backtest import momentum_xs as mx
from backtest import wide_search as ws

MON = mx.day_ms("2024-09-23")
D0 = MON // ws.DAY_MS


def series(drift=0.0, days=200, turnover=5e6, funding=None, jump_last_day=0.0, path=None):
    """Дневной ряд к понедельнику MON: закрытия с дрейфом и шумом (волатильность > 0), оборот, входы."""
    s = ws.Series()
    for k in range(days, 0, -1):
        d = D0 - k
        base = 100.0 * (1 + drift) ** (days - k) * (1 + 0.01 * (-1) ** k)
        s.close[d] = path(k) if path else base
        s.turnover[d] = turnover
    s.close[D0 - 1] *= 1 + jump_last_day
    s.first_day = D0 - days
    s.open[MON] = s.close[D0 - 1]
    s.open[MON + ws.WEEK_MS] = s.close[D0 - 1]
    if funding is not None:
        s.fund_ts = [MON - k * 8 * mx.HOUR_MS for k in range(30, 0, -1)]
        s.fund_rate = [funding] * 30
    return s


def test_grid_has_70_variants_by_family():
    g = ws.grid()
    counts = {f: sum(1 for c in g if c["family"] == f) for f in ws.FAMILIES}
    assert counts == {"trend": 18, "momentum": 20, "reversal": 12, "funding": 12, "lowvol": 4, "breakout": 4}
    assert len(g) == 70 and len({ws.label(c) for c in g}) == 70


def test_non_crypto_contracts_are_excluded():
    assert not ws.is_crypto("XAUUSDT") and not ws.is_crypto("TSLAUSDT") and not ws.is_crypto("SOXLUSDT")
    assert not ws.is_crypto("SKHYNIXUSDT") and not ws.is_crypto("TSMCUSDT"), "токенизированные акции (Bybit 110126)"
    assert not ws.is_crypto("USDCUSDT") and not ws.is_crypto("PAXGUSDT") and not ws.is_crypto("ETHBTCUSDT"), "стейблы, золото, кросс-курс"
    assert ws.is_crypto("1000PEPEUSDT") and ws.is_crypto("BTCUSDT") and ws.is_crypto("SHIB1000USDT")
    assert ws.is_crypto("SPXUSDT") and ws.is_crypto("STRKUSDT"), "SPX6900 и Starknet — крипта, не индекс и не акция"


def test_universe_is_judged_at_that_moment_by_history_and_turnover():
    data = {"A": series(), "young": series(days=50), "thin": series(turnover=1e6), "big": series(turnover=2e7)}
    assert ws.eligible(data, MON, MON + ws.WEEK_MS) == ["big", "A"], "молодой и тонкий не входят; порядок по обороту"
    assert ws.universe(data, MON, MON + ws.WEEK_MS, "top10") == ["big", "A"]
    del data["A"].open[MON + ws.WEEK_MS]
    assert ws.eligible(data, MON, MON + ws.WEEK_MS) == ["big"], "без цены выхода неделю торговать нельзя"


def test_trend_weights_follow_the_sign_and_gross_is_one():
    data = {"up": series(drift=0.01), "down": series(drift=-0.01), "flat": series(drift=0.01)}
    w = ws.weights(data, {"family": "trend", "L": 30, "universe": "all"}, MON, MON + ws.WEEK_MS)
    assert w["up"] > 0 and w["down"] < 0 and w["flat"] > 0
    assert sum(abs(v) for v in w.values()) == pytest.approx(1.0)


def test_momentum_ignores_the_last_day_and_reversal_is_its_mirror():
    data = {f"S{i:02}": series(drift=i / 1000) for i in range(12)}
    data["S00"].close[D0 - 1] *= 3  # рывок в последние сутки — в моментум не входит
    cfg = {"family": "momentum", "L": 28, "frac": 0.2, "universe": "all"}
    w = ws.weights(data, cfg, MON, MON + ws.WEEK_MS)
    longs, shorts = {s for s, v in w.items() if v > 0}, {s for s, v in w.items() if v < 0}
    assert longs == {"S07", "S08", "S09", "S10", "S11"} and shorts == {"S00", "S01", "S02", "S03", "S04"}
    assert sum(abs(v) for v in w.values()) == pytest.approx(1.0)
    r = ws.weights(data, {**cfg, "family": "reversal", "L": 7}, MON, MON + ws.WEEK_MS)
    assert {s for s, v in r.items() if v > 0} == {"S01", "S02", "S03", "S04", "S05"}, "разворот: лонг худшим за 7 дней (S00 прыгнул)"
    assert ws.weights({k: data[k] for k in list(data)[:9]}, cfg, MON, MON + ws.WEEK_MS) == {}, "меньше 2 × 5 монет — позиций нет"


def test_funding_family_shorts_the_payers_and_lowvol_longs_the_calm():
    data = {f"F{i}": series(funding=(i - 5) * 1e-4) for i in range(12)}
    w = ws.weights(data, {"family": "funding", "W": 7, "frac": 0.2, "universe": "all"}, MON, MON + ws.WEEK_MS)
    assert {s for s, v in w.items() if v < 0} == {"F7", "F8", "F9", "F10", "F11"}
    calm = {f"V{i}": series(path=lambda k, i=i: 100.0 * (1 + 0.001 * (i + 1) * (-1) ** k)) for i in range(12)}
    for s in calm.values():
        s.open[MON] = s.open[MON + ws.WEEK_MS] = 100.0
    w = ws.weights(calm, {"family": "lowvol", "W": 30, "frac": 0.2, "universe": "all"}, MON, MON + ws.WEEK_MS)
    assert {s for s, v in w.items() if v > 0} == {"V0", "V1", "V2", "V3", "V4"}


def test_breakout_needs_a_new_high_and_volume():
    quiet = series(drift=0.005, jump_last_day=0.05)
    loud = series(drift=0.005, jump_last_day=0.05)
    for k in range(1, 8):
        loud.turnover[D0 - k] = 5e6 * 3
    down = series(drift=-0.005, jump_last_day=-0.05)
    for k in range(1, 8):
        down.turnover[D0 - k] = 5e6 * 3
    cfg = {"family": "breakout", "N": 20, "universe": "all"}
    w = ws.weights({"quiet": quiet, "loud": loud, "down": down}, cfg, MON, MON + ws.WEEK_MS)
    assert w == {"loud": 0.5, "down": -0.5}
    assert ws.weights({"quiet": quiet, "loud": loud}, cfg, MON, MON + ws.WEEK_MS) == {"loud": 1.0}


def week(t, r):
    return mx.Week(t, t + ws.WEEK_MS, r, r, 0.0, 0.0, (), ())


def test_walk_forward_picks_the_variant_by_prior_years_only():
    y21, y23, y24 = ws.year_start_ms(2021), ws.year_start_ms(2023), ws.year_start_ms(2024)
    weeks_t = mx.mondays(y21, y24 + 10 * ws.WEEK_MS)
    noise = [0.001 * (-1) ** k for k in range(len(weeks_t))]  # разброс нужен: выбор идёт по средней к разбросу
    good_then_bad = [week(t, (0.01 if t < y23 else -0.02) + e) for t, e in zip(weeks_t, noise)]
    bad_then_good = [week(t, (-0.01 if t < y23 else 0.03) + e) for t, e in zip(weeks_t, noise)]
    series_, chosen = ws.walk_forward({0: good_then_bad, 1: bad_then_good}, [0, 1], 2024)
    assert chosen[2023] == 0, "в 2023 торгуется вариант, лучший по 2021–2022, хотя в 2023 он проигрывает"
    assert chosen[2024] == 1, "к 2024 по всей истории лучше стал второй"
    year23 = [w.r for w in series_ if y23 <= w.entry_t < y24]
    assert len(year23) == sum(1 for t in weeks_t if y23 <= t < y24) and all(r < 0 for r in year23)
    assert ws.walk_forward({0: good_then_bad[:10]}, [0], 2023)[1] == {}, "меньше 52 недель — выбора нет"


def test_criteria_and_holdout_rule():
    t0 = ws.year_start_ms(2023)
    steady = [week(t0 + k * ws.WEEK_MS, 0.003 + 0.002 * (-1) ** k) for k in range(160)]
    st = ws.evaluate(steady)
    assert st["passed"] and st["checks"] == {"weeks": True, "ci": True, "mean": True, "segments": True, "drawdown": True}
    assert not ws.evaluate(steady[:100])["checks"]["weeks"]
    h = ws.holdout_check(0.003, [week(t0, 0.001), week(t0 + ws.WEEK_MS, -0.001), week(t0 + 2 * ws.WEEK_MS, 0.0)])
    assert not h["passed"], "средняя 0 при пороге 0,3 % − 2 SE ≈ 0,18 % — противоречие"
    assert ws.holdout_check(0.003, [week(t0, 0.01), week(t0 + ws.WEEK_MS, -0.01), week(t0 + 2 * ws.WEEK_MS, 0.02)])["passed"]


def test_daily_rows_are_built_from_4h_bars(tmp_path):
    conn = history.connect(tmp_path / "wide.db")
    rows = []
    for k in range(3 * 6):  # трое суток по шесть 4h-баров, начиная с понедельника 00:00
        ts = MON + k * mx.H4_MS
        rows.append(("BTCUSDT", "4h", ts, 100.0 + k, 101.0, 99.0, 100.5 + k, 1.0, 1e6))
        rows.append(("XAUUSDT", "4h", ts, 1.0, 1.0, 1.0, 1.0, 1.0, 1e6))
    conn.executemany("INSERT INTO candles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.execute("INSERT INTO funding VALUES ('BTCUSDT', ?, 0.0001)", (MON + 8 * mx.HOUR_MS,))
    conn.commit()
    data = ws.load(conn, MON, MON + 3 * ws.DAY_MS)
    assert list(data) == ["BTCUSDT"], "золото не крипта"
    s = data["BTCUSDT"]
    assert s.first_day == D0 and s.close[D0] == 100.5 + 5 and s.close[D0 + 1] == 100.5 + 11
    assert s.turnover[D0] == pytest.approx(6e6) and s.open[MON] == 100.0 and s.open[MON + ws.DAY_MS] == 106.0
    assert s.fund_ts == [MON + 8 * mx.HOUR_MS]
