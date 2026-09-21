"""
И19 (backtest/xfunding.py): ставка «последняя выплаченная к t» в сутки, издержки круга, фандинг по выплатам,
выход при схлопывании, фильтры (тот же актив, оборот, предел позиций), загрузчик с докачкой. Без сети.
"""
import pytest

from backtest import xfunding as xf

H, D = xf.HOUR_MS, xf.DAY_MS
T0 = 1_789_344_000_000          # 14.09.2026 00:00 UTC


def series(rates, interval_h=8, start=T0 - 2 * D, price=100.0, vol=1e6, hours=24 * 12):
    """rates: функция времени выплаты → ставка за интервал; свечи — ровная цена и оборот vol в час."""
    s = xf.Series()
    t = start
    while t < start + hours * H:
        s.funding.append((t, rates(t)))
        t += interval_h * H
    for i in range(hours):
        ts = start + i * H
        s.close[ts] = price if not callable(price) else price(ts)
        s.vol[ts] = vol
    return s


def test_rate_is_the_last_settled_one_per_day():
    s = series(lambda t: 0.001 if t >= T0 else 0.0, interval_h=8)
    assert xf.rate_day(s, T0 - H) == 0.0, "выплата в T0 ещё не известна за час до неё"
    assert xf.rate_day(s, T0) == pytest.approx(0.003), "0,1 % за 8 ч = 0,3 % в сутки"
    s4 = series(lambda t: 0.001, interval_h=4)
    assert xf.rate_day(s4, T0) == pytest.approx(0.006), "интервал 4 ч — вдвое больше в сутки"
    assert xf.rate_day(xf.Series(funding=[(T0, 0.1)]), T0) is None, "одной выплаты мало для интервала"


def two_exchange_data(high=lambda t: 0.002, low=lambda t: 0.0, vol=1e6, price_b=100.0):
    return {"XUSDT": {"bybit": series(high, vol=vol), "okx": series(low, vol=vol, price=price_b)}}


def test_round_trip_earns_settlements_minus_four_fills():
    """Bybit платит 0,2 % за 8 ч (0,6 %/сут), OKX 0: шорт Bybit / лонг OKX; с T0+2д ставка Bybit 0 → выход."""
    data = two_exchange_data(high=lambda t: 0.002 if t < T0 + 2 * D else 0.0)
    res = xf.simulate(data, T0, T0 + 5 * D, xf.Params())
    assert len(res["trades"]) == 1
    tr = res["trades"][0]
    assert (tr["short"], tr["long"], tr["t0"]) == ("bybit", "okx", T0)
    n = xf.LEG_FRACTION
    settled = [t for t, _ in data["XUSDT"]["bybit"].funding if T0 < t < T0 + 2 * D]
    assert tr["funding"] == pytest.approx(len(settled) * 0.002 * n), "выплаты строго после входа и до выхода"
    fills = 2 * n * (xf.TAKER_FEE["bybit"] + xf.SLIPPAGE) + 2 * n * (xf.TAKER_FEE["okx"] + xf.SLIPPAGE)
    assert tr["costs"] == pytest.approx(fills) and tr["price"] == pytest.approx(0.0)
    assert tr["t1"] == T0 + 2 * D and tr["reason"] == "spread", "выход в час, когда стала известна нулевая ставка"
    assert tr["net"] == pytest.approx(tr["funding"] - fills)


def test_price_legs_count_against_us_when_prices_diverge():
    """Лонг OKX дешевеет на 1 %, шорт Bybit на месте — результат ног −1 % номинала."""
    drop = lambda ts: 100.0 if ts < T0 + D else 99.0
    data = {"XUSDT": {"bybit": series(lambda t: 0.002 if t < T0 + 2 * D else 0.0),
                      "okx": series(lambda t: 0.0, price=drop)}}
    tr = xf.simulate(data, T0, T0 + 5 * D)["trades"][0]
    assert tr["price"] == pytest.approx(-0.01 * xf.LEG_FRACTION)


def test_filters_same_asset_liquidity_and_threshold():
    assert xf.simulate(two_exchange_data(price_b=0.1), T0, T0 + 3 * D)["trades"] == [], "отношение цен 1000 — другой актив"
    assert xf.simulate(two_exchange_data(vol=1e4), T0, T0 + 3 * D)["trades"] == [], "оборот 0,24 млн за сутки < 2 млн"
    assert xf.simulate(two_exchange_data(high=lambda t: 0.0009), T0, T0 + 3 * D)["trades"] == [], "0,27 %/сут < порога 0,30"
    _, excluded = xf.pairs_for(two_exchange_data(price_b=0.1))
    assert excluded and excluded[0].startswith("XUSDT bybit/okx")


def test_position_cap_takes_the_largest_spreads():
    data = {f"K{i}USDT": {"bybit": series(lambda t, i=i: 0.002 + 0.0001 * i), "bitget": series(lambda t: 0.0)} for i in range(12)}
    res = xf.simulate(data, T0, T0 + D, xf.Params(max_positions=10))
    keys = {x["key"] for x in res["trades"]}
    assert len(keys) == 10 and "K0USDT" not in keys and "K1USDT" not in keys, "в пределе 10 — наибольшие разницы"


def test_summary_and_verdict():
    data = two_exchange_data(high=lambda t: 0.002 if t < T0 + 2 * D else 0.0)
    s = xf.summarize(xf.simulate(data, T0, T0 + 5 * D), n_boot=50)
    assert s["trades"] == 1 and s["days"] == 5 and s["total"] == pytest.approx(s["mean_week"] / 7 * 5)
    v = xf.verdict(s, s)
    assert v["passed"] is False and v["checks"]["сделок ≥ 50"] is False


class FakeEx:
    def __init__(self, name, keys):
        self.name, self.keys, self.pause, self.calls = name, keys, 0, 0

    def instruments(self):
        return {k: f"{k}-{self.name}" for k in self.keys}

    def funding(self, sym, start, end):
        self.calls += 1
        return [(start + i * 8 * H, 0.0001) for i in range(3)]

    def candles(self, sym, start, end):
        self.calls += 1
        return [(start + i * H, 100.0, 1e6) for i in range(5)]


def test_download_takes_only_keys_on_two_exchanges_and_resumes(tmp_path):
    ex = {"bybit": FakeEx("bybit", ["BTC", "ONLY"]), "bitget": FakeEx("bitget", ["BTC"]), "okx": FakeEx("okx", ["BTC", "ETH"])}
    conn = xf.connect(tmp_path / "x.db")
    st1 = xf.download(conn, T0, days=10, ex_map=ex)
    assert st1["keys"] == 1 and st1["series"] == 6, "только BTC есть на двух биржах: 3 биржи × ставки и свечи"
    assert conn.execute("SELECT COUNT(*) FROM funding").fetchone()[0] == 9
    calls = sum(e.calls for e in ex.values())
    assert xf.download(conn, T0, days=10, ex_map=ex)["series"] == 0 and sum(e.calls for e in ex.values()) == calls, "докачка не повторяет готовое"
    data = xf.load(conn)
    assert set(data["BTC"]) == {"bybit", "bitget", "okx"} and len(data["BTC"]["okx"].close) == 5


def test_bybit_candles_page_backwards_from_the_newest(monkeypatch):
    """Bybit отдаёт последние 1000 свечей диапазона — без листания назад старые терялись."""
    monkeypatch.setattr(xf.time, "sleep", lambda s: None)
    start, end = T0, T0 + 1500 * H
    seen = []

    def get(url, params):
        seen.append(params["end"])
        hi = min(params["end"], end)
        ts = [t for t in range(start, hi + 1, H)][-1000:][::-1]
        return {"result": {"list": [[str(t), "1", "1", "1", "1", "1", "5"] for t in ts]}}
    rows = xf.Bybit(get).candles("X", start, end)
    assert len({t for t, _, _ in rows}) == 1501 and min(t for t, _, _ in rows) == start and len(seen) == 2
