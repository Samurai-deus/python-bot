"""
Кэш широкой вселенной (backtest/history_wide.py): отбор контрактов (USDT-perp, старше N дней, крипта),
таблица instruments, свечи и фандинг от листинга, 1h для top-K с историей ≥ 2 лет; фандинг — только хвост при повторе.
"""
from backtest import history, history_wide
from tests.test_backtest_history import T0, FakeBybit, Response, api_over, ok

NOW = T0 + 400 * history_wide.DAY_MS


class FakeWide(FakeBybit):
    """Инструменты, тикеры и свечи/фандинг как у FakeBybit (5m-ряд отдаётся на любой интервал)."""

    def __init__(self):
        super().__init__(count=50)
        self.items = [
            {"symbol": "BTCUSDT", "quoteCoin": "USDT", "contractType": "LinearPerpetual", "status": "Trading", "launchTime": str(T0),
             "lotSizeFilter": {"minOrderQty": "0.001"}, "priceFilter": {"tickSize": "0.1"}},
            {"symbol": "NEWUSDT", "quoteCoin": "USDT", "contractType": "LinearPerpetual", "status": "Trading", "launchTime": str(NOW - 10 * history_wide.DAY_MS),
             "lotSizeFilter": {"minOrderQty": "1"}, "priceFilter": {"tickSize": "0.001"}},
            {"symbol": "TSLAUSDT", "quoteCoin": "USDT", "contractType": "LinearPerpetual", "status": "Trading", "launchTime": str(T0),
             "lotSizeFilter": {"minOrderQty": "1"}, "priceFilter": {"tickSize": "0.01"}},
            {"symbol": "ETHUSDC", "quoteCoin": "USDC", "contractType": "LinearPerpetual", "status": "Trading", "launchTime": str(T0),
             "lotSizeFilter": {"minOrderQty": "1"}, "priceFilter": {"tickSize": "0.01"}},
            {"symbol": "OLDUSDT", "quoteCoin": "USDT", "contractType": "LinearPerpetual", "status": "Trading", "launchTime": str(T0 - 800 * history_wide.DAY_MS),
             "lotSizeFilter": {"minOrderQty": "1"}, "priceFilter": {"tickSize": "0.01"}},
            {"symbol": "SPCXUSDT", "quoteCoin": "USDT", "contractType": "LinearPerpetual", "status": "Trading", "launchTime": str(T0),
             "lotSizeFilter": {"minOrderQty": "1"}, "priceFilter": {"tickSize": "0.01"}, "symbolType": "stock", "marketRegion": "US"},
            {"symbol": "CAPUSDT", "quoteCoin": "USDT", "contractType": "LinearPerpetual", "status": "Trading", "launchTime": str(T0),
             "lotSizeFilter": {"minOrderQty": "1"}, "priceFilter": {"tickSize": "0.01"}, "symbolType": "innovation", "marketRegion": ""},
        ]

    def get(self, url, params, timeout):
        path = url.replace(history.BASE_URL, "")
        if path == "/v5/market/instruments-info":
            self.calls.append((path, dict(params)))
            return Response(ok({"list": self.items, "nextPageCursor": ""}))
        if path == "/v5/market/tickers":
            return Response(ok({"list": [{"symbol": "BTCUSDT", "turnover24h": "9e9"}, {"symbol": "OLDUSDT", "turnover24h": "1e6"}]}))
        return super().get(url, params, timeout)


def test_universe_keeps_only_old_crypto_usdt_perps_and_fills_instruments(tmp_path):
    fake = FakeWide()
    conn = history_wide.connect(tmp_path / "wide.db")
    totals = history_wide.sync_universe(conn, api_over(fake), min_age_days=365, tail_days=3, now_ms=NOW)
    symbols = [r[0] for r in conn.execute("SELECT symbol FROM instruments ORDER BY symbol")]
    assert symbols == ["BTCUSDT", "CAPUSDT", "OLDUSDT"], ("NEW — моложе года, TSLA — не крипта по имени, SPCX — акция по "
                                                        "признаку биржи, ETHUSDC — не USDT; CAP (innovation) — крипта")
    assert totals["instruments"] == 3 and totals["candles"] > 0
    assert conn.execute("SELECT turnover24h FROM instruments WHERE symbol = 'BTCUSDT'").fetchone()[0] == 9e9
    assert conn.execute("SELECT COUNT(DISTINCT symbol) FROM candles WHERE interval = '4h'").fetchone()[0] == 3


def test_hourly_symbols_are_top_by_turnover_with_two_years_of_history(tmp_path):
    fake = FakeWide()
    conn = history_wide.connect(tmp_path / "wide.db")
    history_wide.sync_universe(conn, api_over(fake), min_age_days=365, tail_days=3, now_ms=NOW)
    assert history_wide.hourly_symbols(conn, top=40, now_ms=NOW) == ["OLDUSDT"], "BTC в подделке моложе двух лет"
    assert history_wide.sync_hourly(conn, api_over(fake), top=40, now_ms=NOW)["symbols"] == 1


def test_exchange_type_marks_non_crypto_and_names_stay_as_second_net():
    from backtest.wide_search import is_crypto_instrument
    assert is_crypto_instrument({"symbol": "BTCUSDT", "symbolType": "", "marketRegion": ""})
    assert is_crypto_instrument({"symbol": "CAPUSDT", "symbolType": "innovation"})
    assert is_crypto_instrument({"symbol": "1000PEPEUSDT"}), "поля не пришли — решает список имён"
    for item in ({"symbol": "SKHYUSDT", "symbolType": "stock", "marketRegion": "KR"},
                 {"symbol": "SNXXUSDT", "symbolType": "ETF", "marketRegion": "US"},
                 {"symbol": "BZUSDT", "symbolType": "commodity"},
                 {"symbol": "EURUSDUSDT", "symbolType": "forex"},
                 {"symbol": "NEWKINDUSDT", "symbolType": "bond"},
                 {"symbol": "STOCKNOREGIONUSDT", "symbolType": "stock"},
                 {"symbol": "REGIONONLYUSDT", "symbolType": "", "marketRegion": "US"},
                 {"symbol": "TSLAUSDT"}):
        assert not is_crypto_instrument(item), item


def test_funding_tail_mode_only_asks_for_the_recent_window(tmp_path):
    fake = FakeBybit(count=10)
    conn = history.connect(tmp_path / "h.db")
    api = api_over(fake)
    end = T0 + 85 * history_wide.DAY_MS          # 255 выплат по 8 ч — больше страницы (200): полный перечит = 2 страницы
    history.sync_funding(conn, api, "BTCUSDT", T0, end)
    full = conn.execute("SELECT COUNT(*) FROM funding").fetchone()[0]
    assert full == 256 and len([p for path, p in fake.calls if path == "/v5/market/funding/history"]) == 2
    fake.calls.clear()
    added = history.sync_funding(conn, api, "BTCUSDT", T0, end + 2 * history_wide.DAY_MS, tail_days=3)
    asked = [p for path, p in fake.calls if path == "/v5/market/funding/history"]
    assert len(asked) == 1, "хвост за 3 дня — одна страница, а не листание до начала периода"
    assert added == 6 and conn.execute("SELECT COUNT(*) FROM funding").fetchone()[0] == full + 6
