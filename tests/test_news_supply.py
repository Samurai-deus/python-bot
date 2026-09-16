"""
Еженедельная запись предложения монет (news/supply.py): страницы CoinGecko по капитализации, покрытие
контрактов Bybit (множители 1000/10000, тёзки, не-крипта), одна запись на неделю, повтор после сбоя не
раньше чем через час. Сеть подменена httpx.MockTransport, база — временная SQLite (как в test_news).
"""
import httpx
import pytest

import database
from news import supply
from tests.test_news import db  # noqa: F401 — фикстура базы

MONDAY = 1_789_344_000_000            # 14.09.2026 00:00 UTC (понедельник)
NOW = MONDAY + 2 * 86_400_000 + 3_600_000


def coin(cg_id, symbol, cap, circ=1.0, total=2.0, max_supply=None, rank=None):
    return {"id": cg_id, "symbol": symbol.lower(), "name": cg_id, "market_cap_rank": rank, "current_price": 1.5,
            "market_cap": cap, "circulating_supply": circ, "total_supply": total, "max_supply": max_supply}


PAGE1 = [coin("bitcoin", "btc", 1e12, 2e7, 2e7, 2.1e7, 1), coin("pepe", "pepe", 5e9, 4e14, 4e14),
         coin("pepe-clone", "pepe", 1e6, 9.0, 9.0), coin("ethereum", "eth", 4e11, 1.2e8, 1.2e8)]
BYBIT = {"retCode": 0, "result": {"nextPageCursor": "", "list": [
    {"symbol": "BTCUSDT", "quoteCoin": "USDT", "contractType": "LinearPerpetual", "status": "Trading", "symbolType": ""},
    {"symbol": "1000PEPEUSDT", "quoteCoin": "USDT", "contractType": "LinearPerpetual", "status": "Trading", "symbolType": ""},
    {"symbol": "SKHYNIXUSDT", "quoteCoin": "USDT", "contractType": "LinearPerpetual", "status": "Trading", "symbolType": "stock"},
    {"symbol": "NOPEUSDT", "quoteCoin": "USDT", "contractType": "LinearPerpetual", "status": "Trading", "symbolType": ""},
    {"symbol": "ETHUSDC", "quoteCoin": "USDC", "contractType": "LinearPerpetual", "status": "Trading", "symbolType": ""}]}}


def transport(calls, fail_markets=False):
    def handler(request):
        calls.append(str(request.url))
        if "coingecko" in str(request.url):
            if fail_markets:
                return httpx.Response(429)
            page = int(request.url.params.get("page"))
            return httpx.Response(200, json=PAGE1 if page == 1 else [])
        return httpx.Response(200, json=BYBIT)
    return httpx.MockTransport(handler)


def rows(sql, *params):
    conn = database.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(supply, "PAGE_PAUSE_S", 0)
    monkeypatch.setattr(supply, "_next_try_ms", 0)


def test_week_starts_on_monday_utc():
    assert supply.week_start_ms(NOW) == MONDAY
    assert supply.week_start_ms(MONDAY) == MONDAY
    assert supply.week_start_ms(MONDAY - 1) == MONDAY - supply.WEEK_MS


def test_week_is_recorded_once_with_bybit_coverage(db):  # noqa: F811
    calls = []
    with httpx.Client(transport=transport(calls)) as http:
        done = supply.maybe_record(http, NOW)
        assert done == {"week_ms": MONDAY, "coins": 4, "saved": 4, "bases": 3, "matched": 2}, \
            "базы: BTC, PEPE (без множителя 1000), NOPE; SKHYNIX — акция, ETHUSDC — не USDT; покрыты BTC и PEPE"
        saved = {r["cg_id"]: r for r in rows("SELECT * FROM coin_supply WHERE week_ms = ?", MONDAY)}
        assert saved["pepe"]["bybit_base"] == "PEPE" and saved["pepe-clone"]["bybit_base"] is None, "тёзка ниже по капитализации"
        assert saved["bitcoin"]["circulating"] == 2e7 and saved["bitcoin"]["max_supply"] == 2.1e7 and saved["bitcoin"]["rank"] == 1
        assert saved["pepe"]["rank"] == 2, "без market_cap_rank — по месту на странице"
        assert saved["ethereum"]["bybit_base"] is None and saved["bitcoin"]["fetched_ms"] == NOW
        markets = [c for c in calls if "coingecko" in c]
        assert len(markets) == 1, "первая страница короче 250 — конец списка, вторая не запрашивается"
        n = len(calls)
        assert supply.maybe_record(http, NOW + 3_600_000) is None and len(calls) == n, "та же неделя — без запросов"
        assert supply.maybe_record(http, NOW + supply.WEEK_MS)["week_ms"] == MONDAY + supply.WEEK_MS


def test_failure_backs_off_for_an_hour_and_keeps_news_loop_alive(db):  # noqa: F811
    calls = []
    with httpx.Client(transport=transport(calls, fail_markets=True)) as http:
        assert supply.maybe_record(http, NOW) is None
        n = len(calls)
        assert supply.maybe_record(http, NOW + supply.RETRY_MS - 1) is None and len(calls) == n, "до часа — не повторяет"
        assert supply.maybe_record(http, NOW + supply.RETRY_MS) is None and len(calls) > n, "через час — снова пробует"
    assert rows("SELECT COUNT(*) AS n FROM coin_supply")[0]["n"] == 0
