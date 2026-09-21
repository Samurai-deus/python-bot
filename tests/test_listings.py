"""
И21 (backtest/listings.py): вход — первый 4h-бар после суток с листинга, фильтр оборота первых суток, результат
«шорт нового против лонга BTC» с издержками и фандингом, усечение на конце данных, портфель ≤ 20, критерии. Без сети.
"""
import pytest

from backtest import listings as ls

H4, D = ls.H4, ls.DAY_MS
L = ls.day("2024-03-01") + 2 * 3_600_000          # листинг в 02:00 UTC


def ser(start, days, price, turn=1e6, fund_every=8 * 3_600_000, rate=0.0):
    s = ls.Series()
    t = start - start % H4
    while t < start + days * D:
        s.open[t] = price(t) if callable(price) else price
        s.turnover[t] = turn
        t += H4
    f = start - start % fund_every
    while f < start + days * D:
        s.fund_ts.append(f)
        s.fund_rate.append(rate)
        f += fund_every
    return s


def test_trade_enters_after_a_day_and_nets_both_legs():
    fall = lambda t: 10.0 if t < L + 10 * D else 8.0          # новый контракт падает на 20 %
    data = {"NEWUSDT": ser(L, 40, fall, turn=1e6, rate=-0.001), ls.BTC: ser(L - 5 * D, 50, 100.0, rate=0.0001)}
    tr = ls.trade("NEWUSDT", L, data)
    assert tr["t0"] == ls.day("2024-03-02") + 4 * 3_600_000, "первый 4h-бар после 02:00 следующего дня — 04:00"
    assert tr["t1"] == tr["t0"] + 28 * D and not tr["truncated"]
    assert tr["short_leg"] == pytest.approx(0.2) and tr["long_leg"] == pytest.approx(0.0)
    n_new = sum(1 for t in data["NEWUSDT"].fund_ts if tr["t0"] < t <= tr["t1"])
    assert tr["funding"] == pytest.approx(-0.001 * n_new - 0.0001 * n_new), "шорт платит при отрицательной ставке, лонг BTC платит свою"
    costs = (ls.FEE + ls.SLIP_NEW) * (1 + 0.8) + (ls.FEE + ls.SLIP_BTC) * 2
    assert tr["costs"] == pytest.approx(costs)
    assert tr["r"] == pytest.approx(0.2 - costs + tr["funding"])


def test_first_day_turnover_filter_and_truncation():
    data = {"THINUSDT": ser(L, 40, 1.0, turn=1e4), ls.BTC: ser(L - 5 * D, 50, 100.0)}
    assert ls.trade("THINUSDT", L, data) is None, "6 баров × 10 тыс. < 2 млн за первые сутки"
    data = {"NEWUSDT": ser(L, 10, 1.0), ls.BTC: ser(L - 5 * D, 50, 100.0)}
    tr = ls.trade("NEWUSDT", L, data)
    assert tr["truncated"] and tr["t1"] < tr["t0"] + 28 * D


def test_trades_respect_window_and_non_crypto():
    launches = {"NEWUSDT": L, "OLDUSDT": ls.day("2021-06-01"), "TSLAUSDT": L, ls.BTC: ls.day("2020-03-25")}
    data = {s: ser(max(t, L - 5 * D), 60, 1.0) for s, t in launches.items()}
    got = [x["symbol"] for x in ls.trades(launches, data, ls.day(ls.FROM), ls.day(ls.HOLDOUT_FROM))]
    assert got == ["NEWUSDT"], "до 2022 и акции — вне правила"
    assert ls.trades(launches, data, ls.day(ls.FROM), L) == [], "граница отложенного конца: листинг в день границы — не в видимой части"


def test_portfolio_caps_open_trades_at_twenty():
    t4 = L - L % H4 + H4
    trs = [{"symbol": f"K{i}USDT", "t0": t4 + i * H4, "t1": t4 + i * H4 + 28 * D, "launch": L, "r": 0.0} for i in range(25)]
    data = {x["symbol"]: ser(L - D, 60, 1.0) for x in trs}
    data[ls.BTC] = ser(L - D, 60, 100.0)
    weeks = ls.mondays(L - 7 * D, L + 60 * D)
    port = ls.portfolio_weeks(trs, data, weeks)
    per_trade = ls.LEG * ((ls.FEE + ls.SLIP_NEW) + (ls.FEE + ls.SLIP_BTC))
    assert sum(port.values()) == pytest.approx(-20 * 2 * per_trade), "ровные цены: вход и выход 20 сделок, 5 пропущены — предел"


def test_summary_and_verdict():
    trs = [{"symbol": f"K{i}", "launch": ls.day(f"{2022 + i % 5}-0{1 + i % 9}-01"), "r": 0.02 + 0.001 * (i % 7),
            "short_leg": 0.03, "long_leg": 0.0, "costs": 0.004, "funding": -0.006} for i in range(120)]
    s = ls.summarize(trs)
    assert s["trades"] == 120 and s["ci95"][0] > 0 and len(s["by_year"]) == 5
    assert ls.verdict(s, 0.3)["passed"] and not ls.verdict(s, 0.8)["passed"], "та же ставка, что И18, — не отдельная нога"
    assert not ls.verdict(s, None)["passed"]
