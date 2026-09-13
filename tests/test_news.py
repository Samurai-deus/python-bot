"""
Сборщик новостей (news/, docs/TRADER_PLAN.md И10): разбор источников, запись заголовков без
повторов с пометкой backlog, оценка моделью с отдельным бюджетом, проверка здоровья.
OpenRouter и источники подменены httpx.MockTransport, база — временная SQLite.
"""
import json

import httpx
import pytest

import database
from ai_trader import client
from news import health, scorer, sources
from news.__main__ import STALE_MS, poll_once

NOW = 1_789_300_000_000  # 13.09.2026, мс

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>feed</title>
<item><title><![CDATA[SEC approves  spot SOL ETF]]></title><link>https://x.test/a</link>
<guid isPermaLink="false">g-1</guid><pubDate>Sun, 13 Sep 2026 06:00:00 +0000</pubDate></item>
<item><title>No date here</title><link>https://x.test/b</link></item>
</channel></rss>"""
ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Fed statement</title>
<link href="https://fed.test/1"/><id>tag:fed,1</id><updated>2026-09-13T06:00:00Z</updated></entry></feed>"""
BYBIT = {"retCode": 0, "result": {"list": [{"title": "New listing: FOOUSDT", "url": "https://bybit.test/1",
                                             "dateTimestamp": NOW - 60_000}]}}
BINANCE = {"data": {"catalogs": [{"articles": [{"code": "abc", "title": "Binance Will List Foo (FOO)",
                                                "releaseDate": NOW - 60_000}]}]}}


@pytest.fixture
def db(tmp_path, monkeypatch):
    if getattr(database, "_PG_MODE", False):
        pytest.skip("тест для SQLite")
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "news.db"))
    monkeypatch.setattr(database._thread_local, "conn", None, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("AI_DAILY_BUDGET_USD", "1.0")
    monkeypatch.setenv("AI_NEWS_DAILY_BUDGET_USD", "0.3")
    yield database
    conn = getattr(database._thread_local, "conn", None)
    if conn is not None:
        conn.close()
    database._thread_local.conn = None


def item(uid, published_ms=NOW - 60_000, title="headline"):
    return {"uid": uid, "source": "test", "title": title, "url": None, "published_ms": published_ms}


def openrouter(content, cost=0.01, calls=None):
    def handler(request):
        if calls is not None:
            calls.append(json.loads(request.content))
        return httpx.Response(200, json={"model": "anthropic/claude-sonnet-5",
                                         "choices": [{"message": {"content": content}}],
                                         "usage": {"prompt_tokens": 900, "completion_tokens": 400, "cost": cost}})
    return httpx.MockTransport(handler)


def label(i, **kw):
    obj = {"id": i, "symbols": ["SOL"], "direction": "up", "magnitude": 2, "horizon": "hours",
           "confidence": 0.7, "novelty": True}
    obj.update(kw)
    return obj


def rows(sql, *params):
    conn = database.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Источники
# ---------------------------------------------------------------------------

def test_rss_and_atom_items_are_parsed():
    rss = sources.parse_feed("coindesk", RSS)
    assert rss[0] == {"uid": "coindesk:g-1", "source": "coindesk", "title": "SEC approves spot SOL ETF",
                      "url": "https://x.test/a", "published_ms": 1_789_279_200_000}
    assert rss[1]["published_ms"] is None and rss[1]["uid"] == "coindesk:https://x.test/b"
    atom = sources.parse_feed("fed", ATOM)
    assert atom == [{"uid": "fed:tag:fed,1", "source": "fed", "title": "Fed statement",
                     "url": "https://fed.test/1", "published_ms": 1_789_279_200_000}]


def test_exchange_announcements_are_parsed():
    assert sources.parse_bybit("bybit", BYBIT)[0]["uid"] == "bybit:https://bybit.test/1"
    b = sources.parse_binance("binance_listing", BINANCE)[0]
    assert b["uid"] == "binance:abc" and b["published_ms"] == NOW - 60_000
    assert sources.parse_binance("binance_listing", {"data": {"catalogs": []}}) == []


def test_failed_source_does_not_stop_the_others(db, tmp_path, monkeypatch):
    monkeypatch.setenv("NEWS_HEARTBEAT", str(tmp_path / "hb"))

    def handler(request):
        url = str(request.url)
        if "coindesk" in url:
            return httpx.Response(500)
        if "bybit" in url:
            return httpx.Response(200, json=BYBIT)
        if "binance" in url:
            return httpx.Response(200, json=BINANCE)
        return httpx.Response(200, content=RSS)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        added, ok = poll_once(http, NOW)
    assert ok == len(sources.SOURCES) - 1
    assert added >= 3
    assert health.check(tmp_path / "hb", NOW / 1000)


# ---------------------------------------------------------------------------
# Запись заголовков
# ---------------------------------------------------------------------------

def test_items_are_stored_once(db):
    assert database.save_news_items([item("a"), item("b")], NOW, STALE_MS) == 2
    assert database.save_news_items([item("a"), item("c")], NOW + 120_000, STALE_MS) == 1
    first = rows("SELECT seen_ms FROM news_items WHERE uid = ?", "a")[0]
    assert first["seen_ms"] == NOW, "момент первого взгляда не перезаписывается"


def test_stale_or_undated_items_are_backlog_and_not_scored(db):
    database.save_news_items([item("fresh"), item("old", published_ms=NOW - STALE_MS - 1),
                              item("undated", published_ms=None)], NOW, STALE_MS)
    backlog = {r["uid"]: r["backlog"] for r in rows("SELECT uid, backlog FROM news_items")}
    assert backlog == {"fresh": 0, "old": 1, "undated": 1}
    assert [r["uid"] for r in database.get_unscored_news(10)] == ["fresh"]


# ---------------------------------------------------------------------------
# Разбор ответа модели
# ---------------------------------------------------------------------------

def test_labels_are_parsed_and_normalised():
    text = "```json\n" + json.dumps({"items": [label(1, symbols=["solusdt", "SOL", "eth", "BTC", "XRP"]),
                                                label(2, symbols=[], direction="none", magnitude=0)]}) + "\n```"
    parsed = scorer.parse_scores(text, 2)
    assert [r["symbol"] for r in parsed[1]] == ["SOL", "ETH"], "не больше 3 из списка, без повторов и USDT"
    assert parsed[1][0] == {"symbol": "SOL", "direction": "up", "magnitude": 2, "horizon": "hours",
                            "confidence": 0.7, "novelty": 1}
    assert parsed[2] == []


@pytest.mark.parametrize("bad", [
    {"direction": "sideways"}, {"horizon": "weeks"}, {"magnitude": 4}, {"magnitude": True},
    {"magnitude": 1.5}, {"confidence": 1.2}, {"confidence": "high"}, {"novelty": "yes"},
    {"symbols": "SOL"}, {"symbols": ["S O L"]},
])
def test_off_schema_label_is_rejected(bad):
    assert scorer.parse_scores(json.dumps({"items": [label(1, **bad)]}), 1) == {}


def test_unknown_ids_and_garbage_are_ignored():
    assert scorer.parse_scores(json.dumps({"items": [label(5)]}), 2) == {}
    assert scorer.parse_scores("не знаю", 2) == {}


# ---------------------------------------------------------------------------
# Оценка и бюджет
# ---------------------------------------------------------------------------

def test_pending_items_are_scored_in_one_request(db):
    database.save_news_items([item("a", title="SOL ETF approved"), item("b", title="Market recap")], NOW, STALE_MS)
    calls = []
    answer = json.dumps({"items": [label(1), label(2, symbols=[], direction="none", magnitude=0, novelty=False)]})
    assert scorer.score_pending(transport=openrouter(answer, calls=calls), clock=lambda: NOW / 1000 + 5) == 2
    assert len(calls) == 1 and "1. [test] SOL ETF approved" in calls[0]["messages"][1]["content"]
    items = {r["uid"]: r for r in rows("SELECT uid, scored_ms, score_status, prompt_version FROM news_items")}
    assert items["a"]["scored_ms"] == NOW + 5000 and items["a"]["score_status"] == "ok"
    assert items["a"]["prompt_version"] == scorer.PROMPT_VERSION
    assert rows("SELECT uid, symbol, direction FROM news_scores") == [{"uid": "a", "symbol": "SOL", "direction": "up"}]
    assert database.get_unscored_news(10) == []
    assert database.get_ai_spend(client.utc_day(), purpose=client.NEWS_PURPOSE) == pytest.approx(0.01)


def test_off_schema_answer_marks_items_and_is_not_retried(db):
    database.save_news_items([item("a")], NOW, STALE_MS)
    assert scorer.score_pending(transport=openrouter("не знаю")) == 1
    assert rows("SELECT score_status FROM news_items")[0]["score_status"] == "bad_format"
    assert database.get_unscored_news(10) == []


def test_news_has_its_own_budget(db):
    answer = json.dumps({"items": [label(1)]})
    database.record_ai_usage(client.utc_day(), "review", "m", 1, 1, 5.0)
    assert client.budget_left_usd(purpose=client.NEWS_PURPOSE) == pytest.approx(0.3)
    database.save_news_items([item("a")], NOW, STALE_MS)
    calls = []
    assert scorer.score_pending(transport=openrouter(answer, calls=calls)) == 1, "сигналы не съедают бюджет новостей"
    database.record_ai_usage(client.utc_day(), client.NEWS_PURPOSE, "m", 1, 1, 0.3)
    database.save_news_items([item("b")], NOW, STALE_MS)
    assert scorer.score_pending(transport=openrouter(answer, calls=calls)) == 0
    assert len(calls) == 1 and len(database.get_unscored_news(10)) == 1, "исчерпан — ждёт следующих суток"


def test_news_spend_does_not_eat_the_signal_budget(db):
    database.record_ai_usage(client.utc_day(), client.NEWS_PURPOSE, "m", 1, 1, 0.3)
    assert client.budget_left_usd() == pytest.approx(1.0)
    assert client.complete("review", "s", "u", "m", transport=openrouter("ok")) is not None


# ---------------------------------------------------------------------------
# Здоровье
# ---------------------------------------------------------------------------

def test_health_by_heartbeat_age(tmp_path):
    hb = tmp_path / "hb"
    assert not health.check(hb, 1000.0), "пульса нет — нездоров"
    hb.write_text("1000\n", encoding="utf-8")
    assert health.check(hb, 1000.0 + health.MAX_AGE)
    assert not health.check(hb, 1000.0 + health.MAX_AGE + 1)
