"""
И10б (docs/TRADER_PLAN.md): обезличивание заголовка, отбор заголовков для второй оценки, запись
итога, порядок в цикле. OpenRouter подменён httpx.MockTransport, база — временная SQLite.
"""
import json

import pytest

import database
from news import blind, scorer
from news.__main__ import STALE_MS
from tests.test_news import NOW, db, item, label, openrouter, rows  # noqa: F401 — фикстура и помощники


@pytest.mark.parametrize("title, expected", [
    ("SEC approves spot Solana ETF", "SEC approves spot [COIN] ETF"),
    ("Bitcoin and Ethereum slide as $BTC drops below $60k", "[COIN] slide as [COIN] drops below $60k"),
    ("Binance will list Worldcoin (WLD) and delist BTC/USDT pairs", "Binance will list [COIN] ([COIN]) and delist [COIN] pairs"),
    ("New listing: RENDERUSDT perpetual", "New listing: [COIN] perpetual"),
    ("Fed holds rates steady", "Fed holds rates steady"),
    ("Optimism about the economy grows", "[COIN] about the economy grows"),
    ("Polygon's MATIC becomes POL", "[COIN]'s [COIN] becomes [COIN]"),
])
def test_coin_names_and_tickers_are_hidden(title, expected):
    assert blind.anonymize(title) == expected


def test_exchange_name_and_lowercase_words_survive():
    out = blind.anonymize("Binance CEO says the sun will shine on Ether holders and eth fans")
    assert out == "Binance CEO says the sun will shine on [COIN] holders and eth fans", "названия — без регистра, тикеры — заглавными"
    assert blind.anonymize("Op-ed: the settlement is final") == "Op-ed: the settlement is final", "тикер — только заглавными"
    assert blind.anonymize("U.S. SEC delays decision") == "U.S. SEC delays decision"


def test_only_items_scored_with_symbols_are_blinded(db):
    database.save_news_items([item("a", title="SOL ETF approved"), item("b", title="Market recap"),
                              item("c", title="pending")], NOW, STALE_MS)
    database.save_news_score("a", NOW, "ok", "m", scorer.PROMPT_VERSION, scorer._item_rows(label(1)))
    database.save_news_score("b", NOW, "ok", "m", scorer.PROMPT_VERSION, [])
    assert [r["uid"] for r in database.get_unblinded_news(10)] == ["a"], "без монет и неоценённые — не тратят бюджет"
    calls = []
    answer = json.dumps({"items": [{"id": 1, "direction": "down", "magnitude": 3, "horizon": "hours",
                                    "confidence": 0.8, "novelty": True}]})
    assert blind.score_pending(transport=openrouter(answer, calls=calls), clock=lambda: NOW / 1000 + 9) == 1
    assert "1. [test] [COIN] ETF approved" in calls[0]["messages"][1]["content"]
    assert "[COIN]" in json.dumps(calls[0]["messages"][0]["content"]), "модели сказано, что названия скрыты"
    row = rows("SELECT * FROM news_blind")[0]
    assert row["uid"] == "a" and row["blind_title"] == "[COIN] ETF approved" and row["status"] == "ok"
    assert row["scored_ms"] == NOW + 9000 and row["prompt_version"] == blind.PROMPT_VERSION
    assert (row["direction"], row["magnitude"], row["confidence"], row["novelty"]) == ("down", 3, 0.8, 1)
    assert database.get_unblinded_news(10) == [] and blind.score_pending(transport=openrouter(answer, calls=calls)) == 0
    assert len(calls) == 1, "оценённый второй раз не оценивается снова"


def test_off_schema_blind_answer_is_recorded_and_not_retried(db):
    database.save_news_items([item("a")], NOW, STALE_MS)
    database.save_news_score("a", NOW, "ok", "m", scorer.PROMPT_VERSION, scorer._item_rows(label(1)))
    assert blind.score_pending(transport=openrouter("не знаю"), clock=lambda: NOW / 1000 + 60) == 1
    row = rows("SELECT status, direction FROM news_blind")[0]
    assert row["status"] == "bad_format" and row["direction"] is None
    assert database.get_unblinded_news(10) == []


def test_second_evaluation_only_within_30_minutes_of_the_first(db):
    database.save_news_items([item("fresh", title="SOL up"), item("late", title="SOL down")], NOW, STALE_MS)
    database.save_news_score("fresh", NOW, "ok", "m", scorer.PROMPT_VERSION, scorer._item_rows(label(1)))
    database.save_news_score("late", NOW - blind.MAX_LAG_MS - 1, "ok", "m", scorer.PROMPT_VERSION, scorer._item_rows(label(1)))
    calls = []
    answer = json.dumps({"items": [label(1)]})
    assert blind.score_pending(transport=openrouter(answer, calls=calls), clock=lambda: NOW / 1000) == 1
    assert len(calls) == 1 and "SOL down" not in json.dumps(calls[0]) and "[COIN] up" in json.dumps(calls[0])
    status = {r["uid"]: r["status"] for r in rows("SELECT uid, status FROM news_blind")}
    assert status == {"fresh": "ok", "late": blind.STALE}, "просроченный помечен без запроса и не ждёт следующего цикла"
    assert rows("SELECT direction FROM news_blind WHERE uid = 'late'")[0]["direction"] is None


def test_late_blind_rows_already_stored_are_reclassified(db):
    database.save_news_items([item("a")], NOW, STALE_MS)
    database.save_news_score("a", NOW - 2 * blind.MAX_LAG_MS, "ok", "m", scorer.PROMPT_VERSION, scorer._item_rows(label(1)))
    database.save_news_blind("a", "x", NOW, "ok", "m", blind.PROMPT_VERSION, {"direction": "up", "magnitude": 2,
                                                                              "horizon": "hours", "confidence": 0.7, "novelty": 1})
    assert blind.score_pending(transport=openrouter("unused")) == 0
    row = rows("SELECT status, direction FROM news_blind")[0]
    assert row["status"] == blind.STALE and row["direction"] == "up", "оценка сохраняется, но в проверку не входит"


def test_blind_labels_reject_off_schema_and_ignore_symbols():
    good = {"id": 1, "direction": "up", "magnitude": 2, "horizon": "days", "confidence": 0.7, "novelty": False,
            "symbols": ["BTC"]}
    parsed = blind.parse_labels(json.dumps({"items": [good, {**good, "id": 2, "magnitude": 7}]}), 2)
    assert parsed == {1: {"direction": "up", "magnitude": 2, "horizon": "days", "confidence": 0.7, "novelty": 0}}


def test_blind_scoring_runs_after_named_scoring_in_the_cycle():
    import inspect
    from news import __main__ as m
    src = inspect.getsource(m.main)
    assert src.index("scorer.score_pending()") < src.index("blind.score_pending()")
