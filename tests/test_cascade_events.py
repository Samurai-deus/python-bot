"""
И2 (docs/TRADER_PLAN.md): каскад по следу — признак без подглядывания вперёд, сторона против
движения, вход и выход по времени, проскальзывание 0,1 %, одна позиция на символ, свой ДИ.
"""
import pytest

from backtest import cascade_events as ce
from backtest import funding_events as fe
from backtest import history
from backtest.portfolio import TAKER_FEE

F = ce.FIVE_MS
T = 1_760_000_400_000 - 1_760_000_400_000 % (8 * ce.HOUR_MS)


def series(value, start, end):
    return {t: value for t in range(start, end, F)}


def with_cascade(oi_drop, move, at=T):
    """OI и close ровные, а к моменту `at` — падение OI и сдвиг цены за 15 минут (по данным до at − 5 мин)."""
    oi = series(1000.0, at - 12 * F, at + 12 * F)
    closes = series(100.0, at - 12 * F, at + 12 * F)
    for t in range(at - F, at + 12 * F, F):
        oi[t] = 1000.0 * (1 + oi_drop)
        closes[t] = 100.0 * (1 + move)
    return oi, closes


def test_a_cascade_is_an_oi_drop_with_a_big_move_and_the_trade_goes_against_it():
    oi, closes = with_cascade(-0.021, -0.025)
    moments = ce.detect(oi, closes, T - 2 * F, T + 2 * F)
    assert [(t, side) for t, side, *_ in moments][:1] == [(T, "LONG")]
    oi, closes = with_cascade(-0.021, +0.02)
    assert ce.detect(oi, closes, T - 2 * F, T + F)[0][1] == "SHORT"


@pytest.mark.parametrize("oi_drop, move", [(-0.019, -0.05), (-0.05, -0.019)])
def test_below_either_threshold_there_is_no_cascade(oi_drop, move):
    oi, closes = with_cascade(oi_drop, move)
    assert ce.detect(oi, closes, T - 2 * F, T + 2 * F) == []


@pytest.mark.parametrize("late", ["oi", "price"])
def test_the_decision_uses_only_data_known_by_the_moment(late):
    # одно условие каскада видно к T − 5 мин, второе появляется только в T (снимок OI на T и бар,
    # открывшийся в T, в момент T ещё не известны) — события в T быть не должно
    oi, closes = with_cascade(0.0 if late == "oi" else -0.03, -0.03 if late == "oi" else 0.0)
    if late == "oi":
        oi[T] = 900.0
    else:
        closes[T] = 90.0
    assert ce.detect(oi, closes, T, T + F) == []


def test_entry_at_the_moment_exit_by_time_with_doubled_slippage():
    exit_t = T + ce.HOUR_MS
    data = {"X": {"opens": {T: 100.0, exit_t: 102.0}, "fund": {"ts": [], "rate": []},
                  "moments": [(T, "LONG", -0.03, -0.03)]}}
    events, _ = ce.run_variant(["X"], data, 1)
    fill_in, fill_out = 100 * (1 + ce.SLIPPAGE), 102 * (1 - ce.SLIPPAGE)
    assert ce.SLIPPAGE == 0.001
    assert events[0].r == pytest.approx(fill_out / fill_in - 1 - TAKER_FEE * (1 + fill_out / fill_in))
    data["X"]["moments"] = [(T, "SHORT", -0.03, 0.03)]
    assert ce.run_variant(["X"], data, 1)[0][0].r < -0.02, "шорт на росте цены теряет"


def test_one_position_per_symbol():
    opens = series(100.0, T, T + 30 * ce.HOUR_MS)
    data = {"X": {"opens": opens, "fund": {"ts": [], "rate": []},
                  "moments": [(T, "LONG", -0.03, -0.03), (T + F, "LONG", -0.03, -0.03), (T + 2 * ce.HOUR_MS, "LONG", -0.03, -0.03)]}}
    events, skipped = ce.run_variant(["X"], data, 1)
    assert [e.t_ms for e in events] == [T, T + 2 * ce.HOUR_MS] and skipped["overlap"] == 1


def test_three_variants_give_a_98_3_percent_interval():
    assert ce.ALPHA == pytest.approx(0.05 / 3)
    day = 86_400_000
    evs = [fe.Event("X", T + i * day // 2, 0.0, T + i * day // 2, T + i * day // 2 + ce.HOUR_MS,
                    0.01 * (1 if i % 3 else -1), 0.0, 0.0, False, 0.0, 0.0) for i in range(400)]
    wide = fe.evaluate(evs, T, T + 300 * day, bootstrap=400, alpha=fe.ALPHA)["ci"]
    narrow = fe.evaluate(evs, T, T + 300 * day, bootstrap=400, alpha=ce.ALPHA)["ci"]
    assert narrow[0] >= wide[0] and narrow[1] <= wide[1]


def test_load_finds_cascades_in_the_history_cache(tmp_path):
    conn = history.connect(tmp_path / "history.db")
    oi, closes = with_cascade(-0.03, -0.03)
    conn.executemany("INSERT INTO open_interest VALUES (?, '5min', ?, ?)", [("X", t, v) for t, v in oi.items()])
    conn.executemany("INSERT INTO candles VALUES (?, '5m', ?, ?, ?, ?, ?, ?, ?)",
                     [("X", t, c, c, c, c, 1.0, 1.0) for t, c in closes.items()])
    conn.commit()
    data = ce.load(conn, ["X"], T - 2 * F, T + 2 * F)
    conn.close()
    assert data["X"]["moments"][0][:2] == (T, "LONG")
