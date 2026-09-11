"""
Прогон портфеля по истории (Ф1 плана трейдера, шаг 3, фаза B) и метрики (шаг 4):
исполнение консервативно (гэп — по open, стоп раньше цели), порядок проверок — как у
живого бота, издержки учтены, интервал ожидания — бутстрепом по дням.
"""
from dataclasses import dataclass
from typing import Optional

import pytest

from backtest import portfolio, report
from backtest.portfolio import Limits, Position, simulate

FIVE = portfolio.FIVE_MS
T0 = 1_725_000_000_000 - 1_725_000_000_000 % FIVE
BYBIT_TAKER = 0.00055  # комиссия taker Bybit — литералом, а не константой модуля


@dataclass
class S:
    t_ms: int
    symbol: str
    side: str = "LONG"
    entry: float = 100.0
    stop: float = 98.0
    target: float = 104.0
    strategy: str = "trend_following"
    state_15m: Optional[str] = "A"
    block_long: bool = False
    block_short: bool = False


def flat_bars(bars=200, price=100.0, start=T0):
    """Бары без касаний стопа 98 и цели 104 (ход ±0,5)."""
    return [[start + i * FIVE, price, price + 0.5, price - 0.5, price, 1000] for i in range(bars)]


def with_bar(rows, i, o, h, low, c=None):
    rows = [list(r) for r in rows]
    rows[i] = [rows[i][0], o, h, low, c if c is not None else o, 1000]
    return rows


def pos(side="LONG", entry=100.0, stop=98.0, target=104.0):
    return Position("X", "s", side, 0, 0, entry, stop, target, qty=1.0)


@pytest.mark.parametrize("side, bar, expected", [
    ("LONG", (97.0, 99.0, 96.0), (97.0, "SL_GAP")),     # открылся ниже стопа — по open
    ("LONG", (100.0, 105.0, 97.0), (98.0, "SL")),       # стоп и цель в одном баре — стоп
    ("LONG", (100.0, 105.0, 99.0), (104.0, "TP")),
    ("LONG", (100.0, 101.0, 99.0), None),
    ("SHORT", (103.0, 104.0, 101.0), (103.0, "SL_GAP")),
    ("SHORT", (100.0, 103.0, 95.0), (102.0, "SL")),
    ("SHORT", (100.0, 101.0, 95.0), (96.0, "TP")),
])
def test_exits_are_conservative(side, bar, expected):
    p = pos(side, stop=98.0 if side == "LONG" else 102.0, target=104.0 if side == "LONG" else 96.0)
    assert portfolio.exit_in_bar(p, *bar) == expected


def test_a_trade_enters_at_the_next_open_and_pays_costs_both_ways():
    bars = with_bar(flat_bars(), 10, 100.3, 100.6, 99.9)   # бар входа открылся выше цены сигнала
    bars = with_bar(bars, 12, 100.0, 105.0, 99.5)          # бар 12 задевает цель
    res = simulate([S(T0 + 10 * FIVE, "X")], {"X": bars}, {}, ["X"], equity=1000.0)
    (trade,) = res.trades
    assert trade.entry_t == T0 + 10 * FIVE, "вход — открытие бара, начинающегося в момент сигнала"
    assert trade.entry == pytest.approx(100.3 * (1 + portfolio.SLIPPAGE)), "по open следующего бара, не по сигналу"
    assert trade.exit == pytest.approx(104.0 * (1 - portfolio.SLIPPAGE)) and trade.reason == "TP"
    assert trade.qty == pytest.approx(1000 * 0.01 / 0.02 / 100.0), "1 % капитала / 2 % стопа = 500 $"
    assert trade.fees == pytest.approx(trade.qty * (trade.entry + trade.exit) * BYBIT_TAKER)
    gross = trade.qty * (trade.exit - trade.entry)
    assert trade.pnl == pytest.approx(gross - trade.fees)
    risk = trade.qty * (trade.entry - 98.0)
    # цель 104 от входа ~100,35 при стопе 98: чистыми ≈ 1,48 R (вход выше сигнала, издержки)
    assert trade.r == pytest.approx(trade.pnl / risk) and 1.3 < trade.r < 1.6


def test_at_most_n_new_positions_per_turn():
    setups = [S(T0 + 10 * FIVE, f"C{i}") for i in range(5)]
    bars = {f"C{i}": flat_bars() for i in range(5)}
    res = simulate(setups, bars, {}, [f"C{i}" for i in range(5)])
    assert res.refused.get("turn_limit") == 2


def test_the_open_risk_limit_caps_the_portfolio():
    setups = [S(T0 + (10 + i) * FIVE, f"C{i}") for i in range(7)]
    bars = {f"C{i}": flat_bars() for i in range(7)}
    res = simulate(setups, bars, {}, [f"C{i}" for i in range(7)])
    assert res.refused.get("no_room") == 1, "седьмая позиция по 1 % риска не помещается в 6 %"


def test_the_position_count_limit_is_its_own():
    setups = [S(T0 + (10 + i) * FIVE, f"C{i}") for i in range(3)]
    bars = {f"C{i}": flat_bars() for i in range(3)}
    res = simulate(setups, bars, {}, [f"C{i}" for i in range(3)], limits=Limits(max_positions=2))
    assert res.refused.get("no_room") == 1, "третья — сверх предела позиций при свободном риске"


def test_novelty_is_consumed_like_the_live_bot():
    bars = {"X": flat_bars()}
    same_state = [S(T0 + 10 * FIVE, "X"), S(T0 + 11 * FIVE, "X")]
    assert simulate(same_state, bars, {}, ["X"]).refused.get("not_new") == 1
    new_state = [S(T0 + 10 * FIVE, "X"), S(T0 + 11 * FIVE, "X", state_15m="B")]
    assert simulate(new_state, bars, {}, ["X"]).refused.get("position_open") == 1


def test_a_trend_signal_without_state_repeats_after_four_hours():
    bars = {"X": with_bar(flat_bars(bars=200), 20, 100.0, 105.0, 99.5)}   # первая сделка закрыта по цели
    setups = [S(T0 + 10 * FIVE, "X", state_15m=None), S(T0 + 30 * FIVE, "X", state_15m=None),
              S(T0 + 10 * FIVE + 4 * 3_600_000, "X", state_15m=None)]
    res = simulate(setups, bars, {}, ["X"])
    assert res.refused.get("not_new") == 1 and len(res.trades) >= 1


def test_three_loss_events_pause_new_entries():
    symbols = [f"C{i}" for i in range(4)]
    bars = {s: flat_bars(bars=120) for s in symbols}
    for k, s in enumerate(symbols[:3]):                           # три убытка в разные моменты (> 15 мин)
        bars[s] = with_bar(bars[s], 11 + 5 * k, 100.0, 100.5, 97.0)
    setups = [S(T0 + 10 * FIVE, "C0"), S(T0 + 14 * FIVE, "C1"), S(T0 + 19 * FIVE, "C2"),
              S(T0 + 25 * FIVE, "C3"), S(T0 + 60 * FIVE, "C3", state_15m="B")]
    res = simulate(setups, bars, {}, symbols)
    assert [t.reason for t in res.trades[:3]] == ["SL", "SL", "SL"]
    assert res.refused.get("loss_cooldown") == 1, "через 30 мин после третьего события пауза снята"


def test_a_long_pays_positive_funding():
    bars = {"X": with_bar(flat_bars(), 30, 100.0, 105.0, 99.5)}
    fund = {"X": [(T0 + 20 * FIVE, 0.001)]}
    res = simulate([S(T0 + 10 * FIVE, "X")], bars, fund, ["X"])
    (trade,) = res.trades
    assert trade.funding == pytest.approx(trade.qty * 100.0 * 0.001)


def test_a_microstructure_block_refuses_the_side():
    res = simulate([S(T0 + 10 * FIVE, "X", block_long=True)], {"X": flat_bars()}, {}, ["X"])
    assert res.refused.get("funding") == 1 and not res.trades


def test_limits_come_from_settings():
    import config
    limits = Limits.from_config()
    assert (limits.max_positions, limits.max_open_risk_pct, limits.max_new_per_turn) == (
        config.RISK_MAX_OPEN_POSITIONS, config.RISK_MAX_OPEN_RISK_PCT, config.MAX_NEW_POSITIONS_PER_TURN)


# --- метрики --------------------------------------------------------------------

@dataclass
class T:
    r: float
    exit_t: int
    pnl: float = 0.0
    fees: float = 0.0
    funding: float = 0.0
    entry_t: int = 0
    strategy: str = "s"


def test_summary_metrics():
    trades = [T(2.0, 0), T(-1.0, report.DAY_MS), T(2.0, 2 * report.DAY_MS), T(-1.0, 3 * report.DAY_MS)]
    stats = report.summary(trades)
    assert stats["expectancy_r"] == pytest.approx(0.5) and stats["win_rate"] == 0.5
    assert stats["max_drawdown_r"] == pytest.approx(1.0)
    low, high = stats["ci95_r"]
    assert low <= 0.5 <= high


def test_the_profit_factor_is_gains_over_losses():
    trades = [T(3.0, 0), T(-1.0, report.DAY_MS), T(-2.0, 2 * report.DAY_MS)]
    assert report.summary(trades)["profit_factor"] == pytest.approx(1.0)


def test_identical_results_give_a_point_interval():
    trades = [T(1.0, d * report.DAY_MS) for d in range(10)]
    assert report.expectancy_ci(trades) == (1.0, 1.0)


def test_the_interval_respects_same_day_correlation():
    """20 дней: в каждом 10 сделок одного знака. По дням интервал широкий, по сделкам был бы узким."""
    trades = [T(1.0 if d % 2 else -1.0, d * report.DAY_MS + k) for d in range(20) for k in range(10)]
    low, high = report.expectancy_ci(trades)
    assert high - low > 0.5, "блочный бутстреп по дням; по отдельным сделкам ширина ≈ 0,28"


def test_walk_forward_splits_cover_the_range():
    parts, holdout = report.splits(0, 400 * report.DAY_MS, parts=3, holdout_days=100)
    assert parts[0][0] == 0 and parts[-1][1] == holdout[0] == 300 * report.DAY_MS and holdout[1] == 400 * report.DAY_MS
    assert all(a[1] == b[0] for a, b in zip(parts, parts[1:]))
