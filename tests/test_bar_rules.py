"""
И16 (docs/TRADER_PLAN.md): свечные паттерны по определениям плана, пробой и пересечения,
RSI/Боллинджер, выходы (стоп первым при обоих в баре, тейк, по времени), одна позиция на
контракт, дневные бары из 4h, выбор варианта только по прошлым годам, критерии.
"""
import pytest

from backtest import bar_rules as br
from backtest import momentum_xs as mx

T0 = mx.day_ms("2024-09-23")
C = 2 * br.COST_SIDE


def bars(ohlc, step=br.H4_MS):
    n = len(ohlc)
    return br.Bars([T0 + k * step for k in range(n)], [x[0] for x in ohlc], [x[1] for x in ohlc], [x[2] for x in ohlc],
                   [x[3] for x in ohlc], [], [], T0 // br.DAY_MS)


def flat(n, px=100.0):
    return [(px, px + 1, px - 1, px)] * n


def test_candle_patterns_match_the_plan_definitions():
    decline = [(110 - k, 111 - k, 108 - k, 109 - k) for k in range(6)]      # падение: закрытия 109 → 104
    hammer = decline + [(104.0, 104.12, 100.0, 104.1)]                        # тело 0,1; нижняя тень 4; верхняя 0,02
    b = bars(hammer)
    assert br.candle_signal("hammer", b, 6) == 1 and br.candle_signal("shooting_star", b, 6) == 0
    b = bars(decline + [(104.0, 104.2, 100.0, 104.1)])
    assert br.candle_signal("hammer", b, 6) == 0, "верхняя тень 0,1 больше половины тела — не молот"
    b = bars(decline + [(104.0, 104.02, 103.85, 104.1)])
    assert br.candle_signal("hammer", b, 6) == 0, "нижняя тень 0,15 меньше двух тел — не молот"
    engulf = flat(2) + [(101.0, 101.5, 99.0, 99.5), (99.0, 103.0, 98.5, 102.0)]  # медвежий бар, затем бычий шире
    assert br.candle_signal("bull_engulf", bars(engulf), 3) == 1
    assert br.candle_signal("bear_engulf", bars(engulf), 3) == 0
    doji = decline + [(104.0, 106.0, 102.0, 104.1)]                            # тело 0,1 при диапазоне 4
    assert br.candle_signal("doji_bull", bars(doji), 6) == 1 and br.candle_signal("doji_bear", bars(doji), 6) == 0
    soldiers = flat(2) + [(100, 103, 99.5, 102.5), (102, 105, 101.5, 104.5), (104, 107, 103.5, 106.5)]
    assert br.candle_signal("soldiers", bars(soldiers), 4) == 1 and br.candle_signal("crows", bars(soldiers), 4) == 0
    star = flat(2) + [(110, 110.5, 100, 101), (101, 101.5, 100, 100.8), (101, 108, 100.5, 107)]
    assert br.candle_signal("morning_star", bars(star), 4) == 1
    assert br.candle_signal("morning_star", bars(flat(2) + [(110, 110.5, 100, 101), (101, 101.5, 100, 100.8), (101, 104, 100.5, 103)]), 4) == 0, \
        "третий бар не дошёл до середины первого"


def test_breakout_cross_and_reversion_signals():
    ohlc = flat(60)
    ohlc[59] = (100.0, 103.0, 99.0, 102.5)
    b = bars(ohlc)
    ind = br.Indicators(b)
    assert br.signal("donchian20", b, ind, 59) == 1 and br.signal("donchian55", b, ind, 59) == 1
    assert br.signal("donchian20", b, ind, 58) == 0
    ramp = flat(110) + [(100 + k, 101 + k, 99 + k, 100 + k) for k in range(1, 30)]
    b = bars(ramp)
    ind = br.Indicators(b)
    crosses = [i for i in range(len(ramp)) if br.signal("macross_10_50", b, ind, i) == 1]
    assert len(crosses) == 1 and crosses[0] >= 110, "быстрая средняя пересекает медленную один раз, после начала роста"
    assert br.signal("rsi_30_70", b, ind, crosses[0]) in (0, -1)
    drop = flat(30) + [(100 - k, 101 - k, 99 - k, 100 - k) for k in range(1, 20)]
    b = bars(drop)
    ind = br.Indicators(b)
    longs = [i for i in range(len(drop)) if br.signal("rsi_30_70", b, ind, i) == 1]
    assert len(longs) == 1, "RSI пересекает 30 сверху вниз один раз на ровном падении"
    assert br.signal("bollinger_20_2", b, ind, len(drop) - 1) == 0, "ровное падение остаётся внутри полосы 2σ"
    crash = drop + [(81.0, 81.5, 60.0, 62.0)]
    b = bars(crash)
    assert br.signal("bollinger_20_2", b, br.Indicators(b), len(crash) - 1) == 1, "обвал за нижнюю полосу — лонг"


def test_exits_stop_first_take_and_time():
    ohlc = flat(40)
    ohlc[10] = (100.0, 100.5, 99.5, 100.0)
    ohlc[11] = (100.0, 100.5, 99.5, 100.0)   # бар входа: open 100
    ohlc[12] = (100.0, 103.0, 96.0, 101.0)   # задеты и стоп (−2 ATR), и тейк (+2 ATR) при ATR = 2 → стоп
    b = bars(ohlc)
    res = br.outcome(b, 10, +1, 2.0)
    assert res[(6, 1, 1)] == (12, 98.0, "stop") and res[(6, 2, 2)] == (12, 96.0, "stop")
    assert res[(1, 0, 0)] == (12, 100.0, "time"), "удержание 1 бар — выход по open бара 12"
    assert res[(6, 0, 0)] == (17, 100.0, "time") and res[(24, 0, 0)][2] == "time"
    ohlc[12] = (100.0, 102.5, 99.8, 101.0)   # только тейк 1 ATR
    res = br.outcome(bars(ohlc), 10, +1, 2.0)
    assert res[(6, 0, 1)] == (12, 102.0, "take") and res[(6, 0, 2)][2] == "time"
    res = br.outcome(bars(ohlc), 10, -1, 2.0)
    assert res[(6, 1, 0)] == (12, 102.0, "stop"), "для шорта стоп выше входа"


def test_one_position_per_contract_and_costs():
    ohlc = flat(25) + [(100.0, 103.0, 99.0, 102.5)] * 3 + flat(25) + [(100.0, 103.0, 99.0, 102.5)] + flat(30)
    b = bars(ohlc)
    ind = br.Indicators(b)
    trades = br.trades_for_rule("X", b, ind, "donchian20")
    t6 = trades[(6, 0, 0)]
    assert len(t6) == 2, "пробои на барах 25 и 53; бары 26–27 канал (high 103) не пробивают"
    assert t6[0].entry_t == b.ts[26] and t6[1].entry_t == b.ts[54]
    assert t6[0].r == pytest.approx(-C), "вход по open следующего бара (100), выход по open через 6 баров (100), издержки на обе стороны"
    assert br.trades_for_rule("X", b, ind, "donchian20", allowed=[53])[(6, 0, 0)][0].entry_t == b.ts[54], "вне вселенной — сигналов нет"
    ohlc2 = flat(25) + [(100.0, 103.0, 99.0, 102.5), (102.5, 106.0, 102.0, 105.5)] + flat(40, 105.5)
    b2 = bars(ohlc2)
    t2 = br.trades_for_rule("X", b2, br.Indicators(b2), "donchian20")[(6, 0, 0)]
    assert len(t2) == 1 and t2[0].entry_t == b2.ts[26], "второй пробой подряд (бар 26) при открытой позиции пропущен"


def test_daily_bars_from_4h_and_incomplete_days_dropped():
    rows = [[T0 + k * br.H4_MS, 100 + k, 101 + k, 99 + k, 100.5 + k] for k in range(6)]
    rows += [[T0 + br.DAY_MS + k * br.H4_MS, 200, 201, 199, 200] for k in range(5)]
    d = br.daily_from_4h(rows)
    assert d == [[T0, 100, 106, 99, 105.5]], "второй день неполный"


def trade(t, r):
    return br.Trade("X", t, t + br.H4_MS, r, 1, "time")


def test_walk_forward_selects_by_prior_years_only():
    y = {yr: br.year_of(mx.day_ms(f"{yr}-01-01")) for yr in (2021, 2022, 2023)}
    assert y[2021] == 2021
    def stats(good_first):
        out = {}
        for yr, mean in ((2021, 0.01), (2022, 0.01), (2023, -0.03)):  # 2023 перевешивает: с подглядыванием выбор сменился бы
            m = mean if good_first else -mean
            out[yr] = [200, 200 * m, 200 * (m * m + 0.01 ** 2)]
        return out
    chosen = br.walk_forward({"A": stats(True), "B": stats(False)}, 2023)
    assert chosen[2023] == "A", "в 2023 берётся A — лучший по 2021–2022, хотя в 2023 он в минусе"
    assert br.prior_score({2022: [100, 1.0, 0.1]}, 2023) is None, "меньше 300 сделок — выбора нет"


def test_criteria_and_holdout_rule():
    t0 = mx.day_ms("2023-01-02")
    good = [trade(t0 + k * br.HOUR_MS * 6, 0.004 + 0.003 * (-1) ** k) for k in range(400)]
    st = br.evaluate(good)
    assert st["passed"] and st["trades"] == 400
    assert not br.evaluate(good[:200])["checks"]["trades"]
    bad = [trade(t0 + k * br.HOUR_MS * 6, -0.05) for k in range(400)]
    assert not br.evaluate(bad)["checks"]["drawdown"], "400 × −5 % × 10 % номинала = просадка 200 %"
    assert not br.holdout_check(0.004, [trade(t0, -0.01), trade(t0 + 1, -0.01), trade(t0 + 2, -0.012)])["passed"]


def test_concurrency_cap_skips_trades_when_the_book_is_full():
    t0 = mx.day_ms("2023-01-02")
    h = br.HOUR_MS
    trades = [br.Trade("A", t0, t0 + 10 * h, 0.01, 1, "time"), br.Trade("B", t0 + h, t0 + 10 * h, 0.01, 1, "time"),
              br.Trade("C", t0 + 2 * h, t0 + 3 * h, -0.05, 1, "time"), br.Trade("D", t0 + 11 * h, t0 + 12 * h, 0.02, 1, "time")]
    kept = br.cap_concurrency(trades, 2)
    assert [t.symbol for t in kept] == ["A", "B", "D"], "C пропущена — две позиции открыты; D взята после их закрытия"
    assert br.cap_concurrency(trades, None) == trades
    full = br.evaluate(trades * 100, alpha=0.05)
    capped = br.evaluate(trades * 100, alpha=0.05, max_open=2)
    assert capped["trades"] < full["trades"] and capped["drawdown"] <= full["drawdown"]
