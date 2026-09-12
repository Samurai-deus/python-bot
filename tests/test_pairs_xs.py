"""
И6 (docs/TRADER_PLAN.md): арбитраж пар — отбор пар по корреляции без общих монет, β по МНК,
вход/выход/стоп по z на закрытых барах с исполнением на следующем open, закрытие в конце недели,
издержки и фандинг ног, критерии и чтение из кэша.
"""
import math

import pytest

from backtest import history
from backtest import pairs_xs as px
from backtest.portfolio import SLIPPAGE, TAKER_FEE

MON = px.mx.day_ms("2023-01-02")  # понедельник
H4, WEEK, DAY = px.H4_MS, px.WEEK_MS, px.DAY_MS


def test_ols_slope_and_corr():
    x = [1.0, 2.0, 3.0, 4.0]
    assert px.ols_slope(x, [2 * v + 1 for v in x]) == pytest.approx(2.0)
    assert px.corr(x, [3 * v for v in x]) == pytest.approx(1.0)
    assert px.corr(x, [-v for v in x]) == pytest.approx(-1.0)


def walk(seed, n, step=0.01):
    """Детерминированное блуждание лог-цены (без случайных модулей)."""
    out, v, s = [], 0.0, seed
    for _ in range(n):
        s = (s * 1103515245 + 12345) % 2 ** 31
        v += step * ((s / 2 ** 31) - 0.5)
        out.append(v)
    return out


def market(names_to_logs, t_end):
    """4h-закрытия и open по лог-путям, заканчивающимся барами, закрытыми к t_end."""
    data = {}
    for name, logs in names_to_logs.items():
        n = len(logs)
        c4 = {t_end - (n - k) * H4: 100.0 * math.exp(v) for k, v in enumerate(logs)}
        o4 = {tau + H4: c for tau, c in c4.items()}
        data[name] = {"c4": c4, "o4": o4, "fund": {"ts": [], "rate": []}}
    return data


def test_formation_takes_the_most_correlated_pairs_without_shared_coins():
    base, other = walk(1, px.BARS), walk(7, px.BARS)
    noise = walk(3, px.BARS, step=0.002)
    logs = {"A": base, "B": [u + e for u, e in zip(base, noise)], "C": [u + 2 * e for u, e in zip(base, noise)],
            "D": other, "E": [u + e for u, e in zip(other, walk(5, px.BARS, step=0.002))]}
    pairs = px.formation(market(logs, MON), list(logs), MON)
    names = [{p.a, p.b} for p in pairs]
    assert len(pairs) == 2 and {"D", "E"} in names, "две независимые группы — по паре из каждой"
    used = [s for p in pairs for s in (p.a, p.b)]
    assert len(used) == len(set(used)), "монета не входит в две пары"
    for p in pairs:
        assert p.corr >= px.MIN_CORR and 0 < p.beta <= px.MAX_BETA and p.sd > 0


def test_uncorrelated_coins_give_no_pairs():
    logs = {"A": walk(1, px.BARS), "B": walk(2, px.BARS), "C": walk(4, px.BARS)}
    assert px.formation(market(logs, MON), list(logs), MON) == []


def pair_data(z_path, beta=1.0, sd=0.01, mean=0.0):
    """Две монеты: B — ровная цена 100, A так, что z на закрытии бара k равен z_path[k]; open = предыдущее закрытие."""
    c_a, c_b = {}, {}
    for k, z in enumerate(z_path):
        tau = MON + k * H4
        c_b[tau] = 100.0
        c_a[tau] = math.exp(mean + z * sd + beta * math.log(100.0))
    o_a = {tau + H4: c for tau, c in c_a.items()}
    o_b = {tau + H4: c for tau, c in c_b.items()}
    o_a[MON], o_b[MON] = c_a[MON], c_b[MON]
    data = {"A": {"c4": c_a, "o4": o_a, "fund": {"ts": [], "rate": []}},
            "B": {"c4": c_b, "o4": o_b, "fund": {"ts": [], "rate": []}}}
    return data, px.Pair("A", "B", 0.9, beta, mean, sd)


def test_enter_on_two_sigmas_exit_on_reversion_at_the_next_open():
    z = [0.0, 2.5, 1.0, 0.3] + [0.0] * 38
    data, pair = pair_data(z)
    trades = px.trade_pair(data, pair, MON, MON + WEEK)
    assert len(trades) == 1
    t_in, t_out, r, costs, _ = trades[0]
    assert (t_in, t_out) == (MON + 2 * H4, MON + 4 * H4), "решение на закрытии бара, исполнение по open следующего"
    a_in, a_out = data["A"]["o4"][t_in], data["A"]["o4"][t_out]
    fill_in, fill_out = a_in * (1 - SLIPPAGE), a_out * (1 + SLIPPAGE)       # шорт A
    b_fill_in, b_fill_out = 100.0 * (1 + SLIPPAGE), 100.0 * (1 - SLIPPAGE)  # лонг B
    expected = (px.LEG * -(fill_out / fill_in - 1) - px.LEG * TAKER_FEE * (1 + fill_out / fill_in)
                + px.LEG * (b_fill_out / b_fill_in - 1) - px.LEG * TAKER_FEE * (1 + b_fill_out / b_fill_in))
    assert r == pytest.approx(expected) and r > 0, "спред вернулся — сделка в плюсе"


def test_a_stop_at_four_sigmas_and_a_forced_close_at_the_end_of_the_week():
    data, pair = pair_data([0.0, -2.2, -3.0, -4.5] + [-4.6] * 38)
    trades = px.trade_pair(data, pair, MON, MON + WEEK)
    assert len(trades) == 1 and trades[0][1] == MON + 4 * H4 and trades[0][2] < 0, "стоп по |z| ≥ 4"
    data, pair = pair_data([0.0] * 40 + [2.5, 2.4])
    trades = px.trade_pair(data, pair, MON, MON + WEEK)
    assert len(trades) == 1 and trades[0][0] == MON + 41 * H4 and trades[0][1] == MON + WEEK, "закрытие в понедельник"


def test_no_reentry_after_a_stop_even_when_the_spread_comes_back_into_the_entry_zone():
    data, pair = pair_data([0.0, -2.2, -4.5, -3.0, -2.5] + [0.0] * 37)
    trades = px.trade_pair(data, pair, MON, MON + WEEK)
    assert len(trades) == 1 and trades[0][1] == MON + 3 * H4, "после стопа пара до конца недели не торгуется"


def test_no_entry_beyond_the_stop():
    data, pair = pair_data([0.0, 4.5, 4.4] + [0.0] * 39)
    assert px.trade_pair(data, pair, MON, MON + WEEK) == [], "за границей стопа не входим"


def test_the_last_bar_of_the_week_does_not_open_a_trade():
    data, pair = pair_data([0.0] * 41 + [3.0])
    assert px.trade_pair(data, pair, MON, MON + WEEK) == []


def test_leg_b_is_sized_by_beta_and_funding_is_charged_by_side():
    data, pair = pair_data([0.0, 2.5, 1.0, 0.3] + [0.0] * 38, beta=1.5)
    stamp = MON + 3 * H4
    data["B"]["fund"] = {"ts": [stamp], "rate": [0.001]}
    t_in, t_out, r, costs, funding = px.trade_pair(data, pair, MON, MON + WEEK)[0]
    assert funding == pytest.approx(px.LEG * 1.5 * 0.001), "лонг B на 15 % платит положительную ставку"
    assert costs > px.LEG * TAKER_FEE * (2 + 2 * 1.5) * 0.95, "издержки обеих ног, нога B в 1,5 раза больше"


def wk(r, i):
    return px.mx.Week(MON + i * WEEK, MON + (i + 1) * WEEK, r, r, 0.0, 0.0, (), ("0",))


def test_the_criteria_are_the_plan():
    good = [wk(0.004 if i % 4 else -0.002, i) for i in range(160)]
    assert px.evaluate(good, bootstrap=300)["passed"]
    assert not px.evaluate(good[:120], bootstrap=300)["checks"]["weeks"]
    assert not px.evaluate([wk(0.001, i) for i in range(160)], bootstrap=300)["checks"]["mean"]
    assert not px.evaluate(good[:50] + [wk(-0.2, 50)] + good[51:], bootstrap=300)["checks"]["drawdown"]
    assert px.ALPHA == pytest.approx(0.05)


def test_load_reads_4h_and_funding(tmp_path):
    conn = history.connect(tmp_path / "history.db")
    conn.execute("INSERT INTO candles VALUES ('X', '4h', ?, 1.0, 1.0, 1.0, 2.0, 1.0, 1.0)", (MON,))
    conn.execute("INSERT INTO funding VALUES ('X', ?, 0.0001)", (MON + 3_600_000,))
    conn.commit()
    data = px.load(conn, ["X"], MON, MON + DAY)
    conn.close()
    assert data["X"]["c4"][MON] == 2.0 and data["X"]["o4"][MON] == 1.0 and data["X"]["fund"]["ts"] == [MON + 3_600_000]
