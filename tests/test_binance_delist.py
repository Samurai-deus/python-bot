"""
И11 (backtest/binance_delist.py): заголовки делистинга, фандинг за держание, SHORT с издержками,
пороги И11. Bybit подменён: свечи и ставки фандинга по символу; неизвестный символ — 10001.
"""
import pytest

from backtest import binance_delist as bd
from backtest import binance_launch as bl

MIN = bl.MIN_MS
T0 = 1_700_000_000_000 - 1_700_000_000_000 % MIN + 30_000


class FakeApi:
    def __init__(self, bars=None, rates=None):
        self.bars = bars or {}
        self.rates = rates or {}

    def get(self, path, params):
        sym = params["symbol"]
        if path.endswith("/funding/history"):
            rows = [{"symbol": sym, "fundingRate": str(r), "fundingRateTimestamp": str(ts)}
                    for ts, r in self.rates.get(sym, {}).items()
                    if params["startTime"] <= ts <= params["endTime"]]
            return {"list": sorted(rows, key=lambda r: -int(r["fundingRateTimestamp"]))}
        if sym not in self.bars:
            raise RuntimeError("Bybit /v5/market/kline: 10001 params error: Symbol Is Invalid")
        rows = [[str(ts), str(o), "0", "0", str(c), "0", "0"] for ts, (o, c) in self.bars[sym].items()
                if params["start"] <= ts <= params["end"]]
        return {"list": sorted(rows, key=lambda r: -int(r[0]))[:params["limit"]]}


@pytest.mark.parametrize("title, expected", [
    ("Binance Will Delist ICX, SCRT, STORJ on 2026-09-03", ["ICX", "SCRT", "STORJ"]),
    ("Binance Will Delist ACX, HFT, PIVX, PYR, VANRY, VIC on 2026-08-17", ["ACX", "HFT", "PIVX", "PYR", "VANRY", "VIC"]),
    ("Binance Will Delist USDP on 2026-09-24", ["USDP"]),
    ("Binance Will Delist FOO, TUSD on March 3, 2023", ["FOO"]),
    ("Notice of Removal of Spot Trading Pairs - 2026-09-12", []),
    ("Binance Futures Will Delist USDⓈ-M FOOUSDT Perpetual Contract", []),
])
def test_bases_from_delisting_titles(title, expected):
    assert bd.bases(title) == expected


def test_events_use_the_delisting_parser():
    pre = (T0 // MIN - bl.PRE_MIN) * MIN
    api = FakeApi({"ICXUSDT": {pre: (1.0, 1.0)}})
    cat = [{"releaseDate": T0, "title": "Binance Will Delist ICX, SCRT on 2026-09-03"}]
    assert bl.events(api, cat, T0 + bl.DAY_MS, parse=bd.bases) == [("ICXUSDT", T0)]
    assert bl.events(api, cat, T0 + bl.DAY_MS) == [], "без parse — разбор анонсов фьючерсов"


def test_funding_counts_only_payments_inside_the_holding():
    api = FakeApi(rates={"FOOUSDT": {1000: 0.01, 2000: -0.03, 3000: 0.005, 4000: 0.5}})
    assert bd.funding(api, "FOOUSDT", 1000, 3000) == pytest.approx(-0.025), "(вход; выход]"


def test_short_trade_profits_from_a_fall_and_pays_negative_funding():
    entry = bl.entry_minute(T0)
    bars = [(entry + i * MIN, 100.0 - i, 99.5 - i) for i in range(90)]
    api = FakeApi(rates={"FOOUSDT": {entry + 30 * MIN: -0.01}})
    t = bd.trade(api, "FOOUSDT", T0, bars, 1)
    assert t.entry == 100.0 and t.exit == pytest.approx(40.5)
    assert t.funding == pytest.approx(-0.01)
    assert t.r == pytest.approx(1 - 40.5 / 100.0 - bl.COST - 0.01)
    assert bd.trade(api, "FOOUSDT", T0, [(entry + MIN, 1.0, 1.0)], 1) is None


def trades_with(rs, start):
    return [bd.Trade("X", start + i * bl.DAY_MS, start + i * bl.DAY_MS, start + i * bl.DAY_MS + MIN,
                     1.0, 1.0, 0.0, r) for i, r in enumerate(rs)]


def test_h11_thresholds_and_no_holdout():
    start = 1_600_000_000_000 - 1_600_000_000_000 % bl.DAY_MS
    end = start + 60 * bl.DAY_MS
    ok = bd.evaluate(trades_with([0.03, 0.02] * 23, start), start, end)
    assert ok["events"] == 46 and ok["passed"], ok["checks"]
    assert not bd.evaluate(trades_with([0.03, 0.02] * 22, start), start, end)["checks"]["events"], "44 < 45"
    assert not bd.evaluate(trades_with([0.015, 0.012] * 23, start), start, end)["checks"]["mean"], "1,35 % < 1,5 %"
    late = trades_with([0.03, 0.02] * 23, start)
    assert bd.evaluate(late, start, start + 42 * bl.DAY_MS)["events"] == 42, "закрытые после конца не видны"
