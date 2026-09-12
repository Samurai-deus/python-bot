"""
И1 (docs/TRADER_PLAN.md): отскок после сильно отрицательного фандинга — порог и приведение
ставки к 8 ч, вход и выход по времени, издержки, знак фандинга, одна позиция на символ,
защита от рынка, отложенный конец и критерии.
"""
import pytest

from backtest import funding_events as fe
from backtest import history
from backtest.portfolio import SLIPPAGE, TAKER_FEE

H = fe.HOUR_MS
T = 1_760_000_400_000 - 1_760_000_400_000 % (8 * H)  # выплата на границе 8 ч


def fund_of(rows):
    return {"ts": [r[0] for r in rows], "rate": [r[1] for r in rows], "rows8": fe.rates_8h(rows)}


def flat_bars(start, end, price=100.0):
    return {t: price for t in range(start, end + fe.FIVE_MS, fe.FIVE_MS)}


def test_the_rate_is_brought_to_8_hours():
    rows8 = fe.rates_8h([(T, -0.0001), (T + 8 * H, -0.0002)])
    assert [r[2] for r in rows8] == pytest.approx([-0.0001, -0.0002])
    rows4 = fe.rates_8h([(T, -0.00015), (T + 4 * H, -0.0001)])
    assert [r[2] for r in rows4] == pytest.approx([-0.0003, -0.0002])
    gap = fe.rates_8h([(T, -0.0001), (T + 40 * H, -0.0001)])
    assert gap[1][2] == pytest.approx(-0.0001), "дыра считается 8 ч, а не 40"


def test_only_rates_at_or_below_the_threshold_are_events():
    fund = {"X": fund_of([(T, -0.0003), (T + 8 * H, -0.00029), (T + 16 * H, 0.0001)])}
    bars = {"X": flat_bars(T, T + 40 * H)}
    events, _ = fe.run_variant(["X"], fund, bars, T, T + 24 * H, 8, hedged=False)
    assert [e.t_ms for e in events] == [T]


def test_entry_and_exit_by_time_with_costs():
    entry_t, exit_t = T + fe.FIVE_MS, T + fe.FIVE_MS + 24 * H
    bars = {entry_t: 100.0, exit_t: 110.0}
    r, fees, funding = fe.leg(bars, {"ts": [], "rate": []}, entry_t, exit_t, "LONG")
    fill_in, fill_out = 100 * (1 + SLIPPAGE), 110 * (1 - SLIPPAGE)
    assert fees == pytest.approx(TAKER_FEE * (1 + fill_out / fill_in))
    assert r == pytest.approx(fill_out / fill_in - 1 - fees)
    assert funding == 0.0
    short, _, _ = fe.leg(bars, {"ts": [], "rate": []}, entry_t, exit_t, "SHORT")
    assert short == pytest.approx(-(110 * (1 + SLIPPAGE)) / (100 * (1 - SLIPPAGE)) + 1
                                  - TAKER_FEE * (1 + 110 * (1 + SLIPPAGE) / (100 * (1 - SLIPPAGE))))


def test_a_long_receives_negative_funding_only_for_payments_it_holds_through():
    entry_t, exit_t = T + fe.FIVE_MS, T + fe.FIVE_MS + 8 * H
    bars = flat_bars(T, exit_t)
    fund = {"ts": [T, T + 8 * H, T + 16 * H], "rate": [-0.01, -0.001, -0.02]}
    r, fees, funding = fe.leg(bars, fund, entry_t, exit_t, "LONG")
    # выплата T — до входа, T+16 ч — после выхода; в счёт идёт только T+8 ч
    assert funding == pytest.approx(-0.001 * 100 / (100 * (1 + SLIPPAGE)))
    assert r > -fees - 2 * SLIPPAGE, "получая фандинг, лонг на плоской цене теряет меньше издержек"


def test_one_position_per_symbol():
    fund = {"X": fund_of([(T, -0.001), (T + 8 * H, -0.001), (T + 16 * H, -0.001)])}
    bars = {"X": flat_bars(T, T + 120 * H)}
    long_hold, skipped = fe.run_variant(["X"], fund, bars, T, T + 24 * H, 24, hedged=False)
    assert [e.t_ms for e in long_hold] == [T] and skipped["overlap"] == 2
    short_hold, skipped = fe.run_variant(["X"], fund, bars, T, T + 24 * H, 8, hedged=False)
    assert [e.t_ms for e in short_hold] == [T, T + 8 * H, T + 16 * H], "выход и вход в один момент — можно"


def test_the_hedge_is_a_short_btc_and_eth_for_btc():
    entry_t, exit_t = T + fe.FIVE_MS, T + fe.FIVE_MS + 8 * H
    bars = {"X": {entry_t: 100.0, exit_t: 100.0}, "BTCUSDT": {entry_t: 100.0, exit_t: 90.0},
            "ETHUSDT": {entry_t: 100.0, exit_t: 120.0}}
    fund = {s: fund_of([(T, -0.001)]) for s in ("X", "BTCUSDT")}
    fund["ETHUSDT"] = fund_of([])
    events, _ = fe.run_variant(["X", "BTCUSDT"], fund, bars, T, T + H, 8, hedged=True)
    by = {e.symbol: e for e in events}
    assert by["X"].hedge_leg > 0.09, "X хеджирован шортом BTC, который упал"
    assert by["BTCUSDT"].hedge_leg < -0.19, "BTC хеджирован шортом ETH, который вырос"
    assert by["X"].r == pytest.approx(by["X"].long_leg + by["X"].hedge_leg)


def ev(r, t):
    return fe.Event("X", t, -0.001, t, t + H, r, r, 0.0, False, 0.0, 0.0)


def test_the_holdout_is_hidden_and_the_criteria_are_the_plan():
    start, end = T, T + 300 * 86_400_000
    day = 86_400_000
    good = [ev(0.01 if i % 5 else 0.004, start + i * day // 2) for i in range(400)]  # 200 суток видимых
    hidden = [ev(-0.5, end - 10 * day)]
    stats = fe.evaluate(good + hidden, start, end, bootstrap=300)
    assert stats["events"] == 400 and stats["passed"], stats["checks"]
    assert fe.evaluate(good + hidden, start, end, holdout=True, bootstrap=300)["events"] == 401
    few = fe.evaluate(good[:100], start, end, bootstrap=300)
    assert not few["checks"]["events"]
    thin = fe.evaluate([ev(0.002, e.t_ms) for e in good], start, end, bootstrap=300)
    assert thin["checks"]["ci"] and not thin["checks"]["mean"], "плюс меньше +0,3 % не принимается"
    deep = fe.evaluate(good[:200] + [ev(-2.0, start + 150 * day)] + good[300:], start, end, bootstrap=300)
    assert not deep["checks"]["drawdown"]


def test_only_a_hedged_pass_accepts_the_hypothesis():
    ok, bad = {"passed": True}, {"passed": False}
    assert not fe.verdict({(8, False): ok, (8, True): bad})
    assert fe.verdict({(8, False): bad, (24, True): ok})


def test_load_reads_funding_and_bars_from_the_history_cache(tmp_path):
    conn = history.connect(tmp_path / "history.db")
    conn.executemany("INSERT INTO funding VALUES (?, ?, ?)", [("X", T, -0.00015), ("X", T + 4 * H, -0.0001)])
    conn.executemany("INSERT INTO candles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     [("X", "5m", t, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0) for t in range(T, T + H, fe.FIVE_MS)])
    conn.commit()
    fund, bars = fe.load(conn, ["X"], T, T + H)
    conn.close()
    assert fund["X"]["rows8"][0][2] == pytest.approx(-0.0003)
    assert len(bars["X"]) == 12 and "BTCUSDT" in fund and "ETHUSDT" in bars
