"""
И19 вперёд (news/xfunding_live.py): снимок трёх бирж раз в час, только общие контракты, интервалы и оборот
в USDT приведены, сбой одной биржи не мешает остальным. Сеть подменена httpx.MockTransport.
"""
import httpx

from news import xfunding_live as xl

NOW = 1_789_974_000_000 + 5 * 60_000


def payloads(fail=None):
    def handler(request):
        url = str(request.url)
        if fail and fail in url:
            return httpx.Response(500)
        if "instruments-info" in url:
            return httpx.Response(200, json={"result": {"nextPageCursor": "", "list": [
                {"symbol": "BTCUSDT", "quoteCoin": "USDT", "contractType": "LinearPerpetual", "status": "Trading", "fundingInterval": 480},
                {"symbol": "CVCUSDT", "quoteCoin": "USDT", "contractType": "LinearPerpetual", "status": "Trading", "fundingInterval": 60},
                {"symbol": "SOLOUSDT", "quoteCoin": "USDT", "contractType": "LinearPerpetual", "status": "Trading", "fundingInterval": 480}]}})
        if "bybit.com/v5/market/tickers" in url:
            return httpx.Response(200, json={"result": {"list": [
                {"symbol": "BTCUSDT", "fundingRate": "0.0001", "nextFundingTime": "1789977600000", "bid1Price": "81500", "ask1Price": "81500.1", "turnover24h": "9e9"},
                {"symbol": "CVCUSDT", "fundingRate": "0.0007", "nextFundingTime": "1789977600000", "bid1Price": "0.1", "ask1Price": "0.1001", "turnover24h": "5e5"},
                {"symbol": "SOLOUSDT", "fundingRate": "0.0001", "nextFundingTime": "1789977600000", "bid1Price": "1", "ask1Price": "1", "turnover24h": "1"}]}})
        if "current-fund-rate" in url:
            return httpx.Response(200, json={"data": [{"symbol": "BTCUSDT", "fundingRate": "0.00006", "fundingRateInterval": "8", "nextUpdate": "1789977600000"},
                                                      {"symbol": "CVCUSDT", "fundingRate": "-0.0003", "fundingRateInterval": "4", "nextUpdate": "1789977600000"}]})
        if "bitget.com/api/v2/mix/market/tickers" in url:
            return httpx.Response(200, json={"data": [{"symbol": "BTCUSDT", "bidPr": "81499", "askPr": "81501", "usdtVolume": "1.8e9"},
                                                      {"symbol": "CVCUSDT", "bidPr": "0.1", "askPr": "0.1002", "usdtVolume": "3e5"}]})
        if "funding-rate" in url:
            return httpx.Response(200, json={"data": [{"instId": "BTC-USDT-SWAP", "fundingRate": "0.0001", "fundingTime": "1790006400000",
                                                       "prevFundingTime": "1789977600000"}]})
        if "okx.com/api/v5/market/tickers" in url:
            return httpx.Response(200, json={"data": [{"instId": "BTC-USDT-SWAP", "bidPx": "81498", "askPx": "81502", "volCcy24h": "1000", "last": "81500"},
                                                      {"instId": "BTC-USD-SWAP", "bidPx": "1", "askPx": "1", "volCcy24h": "1", "last": "1"}]})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_hourly_snapshot_keeps_only_contracts_on_two_exchanges(tmp_path):
    conn = xl.connect(tmp_path / "x.db")
    with httpx.Client(transport=payloads()) as http:
        done = xl.maybe_record(conn, http, NOW)
        assert done == {"hour_ms": NOW // xl.HOUR_MS * xl.HOUR_MS, "rows": 5, "keys": 2, "exchanges": 3}, "SOLO — только на Bybit"
        assert xl.maybe_record(conn, http, NOW + 30 * 60_000) is None, "тот же час — второй раз не пишем"
    rows = {(r[1], r[2]): r for r in conn.execute("SELECT * FROM snap")}
    assert rows[("bybit", "CVC")][5] == 1.0 and rows[("bitget", "CVC")][5] == 4.0, "интервал: Bybit в минутах → часы, Bitget — часы"
    assert rows[("okx", "BTC")][5] == 8.0 and rows[("okx", "BTC")][8] == 81_500_000.0, "OKX: интервал по двум выплатам, оборот = объём × цена"
    assert rows[("bitget", "CVC")][3] == -0.0003 and rows[("bybit", "BTC")][4] == 1_789_977_600_000


def test_one_exchange_down_does_not_stop_the_others(tmp_path):
    conn = xl.connect(tmp_path / "x.db")
    with httpx.Client(transport=payloads(fail="okx.com")) as http:
        done = xl.record(conn, http, NOW)
    assert done["exchanges"] == 2 and done["keys"] == 2 and done["rows"] == 4
