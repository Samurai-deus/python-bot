"""
ИИ-трейдер, этап 0 — тень (docs/AI_TRADER_PLAN.md).

OpenRouter подменён httpx.MockTransport, база — временная SQLite, рыночные
данные для контекста отключены. Главное свойство: при любом сбое ИИ (нет
ключа, бюджет, таймаут, мусор в ответе) торговля идёт как без него, а расход
не превышает суточного бюджета.
"""
import json
from datetime import datetime, UTC
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import database
from ai_trader import client, review, worker
from ai_trader import telegram as ai_telegram

ROOT = Path(__file__).resolve().parent.parent
SIGNAL_TS = "2026-09-11T08:00:00+00:00"
SIGNAL = {"side": "LONG", "entry": 100.0, "stop": 97.0, "target": 106.0, "atr": 2.0,
          "rr_ratio": 2.0, "position_size": 10.0}


@pytest.fixture
def db(tmp_path, monkeypatch):
    if getattr(database, "_PG_MODE", False):
        pytest.skip("тест для SQLite")
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ai.db"))
    monkeypatch.setattr(database._thread_local, "conn", None, raising=False)
    yield database
    conn = getattr(database._thread_local, "conn", None)
    if conn is not None:
        conn.close()
    database._thread_local.conn = None


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("AI_TRADER_STAGE", raising=False)
    monkeypatch.setenv("AI_DAILY_BUDGET_USD", "1.0")


@pytest.fixture
def offline_market(monkeypatch):
    monkeypatch.setattr(review, "_candles_summary", lambda symbol: {})
    monkeypatch.setattr(review, "_crowd_positioning", lambda symbol: {})


def openrouter(content, cost=0.01, calls=None):
    def handler(request):
        if calls is not None:
            calls.append(json.loads(request.content))
        usage = {"prompt_tokens": 3000, "completion_tokens": 300}
        if cost is not None:
            usage["cost"] = cost
        return httpx.Response(200, json={"model": "anthropic/claude-sonnet-5",
                                         "choices": [{"message": {"content": content}}], "usage": usage})
    return httpx.MockTransport(handler)


def opinion_json(decision="approve", **overrides):
    data = {"decision": decision, "size_multiplier": 1.0, "confidence": 0.7,
            "reasons": ["причина"], "key_risk": "риск"}
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def job():
    snapshot = SimpleNamespace(timestamp=datetime(2026, 9, 11, 8, tzinfo=UTC), states={}, directions={},
                               confidence=0.6, entropy=0.4, market_regime=None)
    return {"symbol": "SOLUSDT", "signal_ts": SIGNAL_TS, "signal_data": dict(SIGNAL), "snapshot": snapshot}


# ---------------------------------------------------------------------------
# Клиент и бюджет
# ---------------------------------------------------------------------------

def test_no_key_means_no_request(db, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    calls = []
    assert client.complete("review", "s", "u", "m", transport=openrouter("{}", calls=calls)) is None
    assert calls == []


def test_cost_is_recorded_and_the_daily_budget_stops_calls(db, key, monkeypatch):
    monkeypatch.setenv("AI_DAILY_BUDGET_USD", "0.05")
    calls = []
    transport = openrouter("ok", cost=0.03, calls=calls)
    assert client.complete("review", "s", "u", "m", transport=transport).cost_usd == pytest.approx(0.03)
    assert client.complete("review", "s", "u", "m", transport=transport) is not None, "0,03 < 0,05"
    assert client.complete("review", "s", "u", "m", transport=transport) is None, "0,06 ≥ 0,05"
    assert len(calls) == 2
    assert database.get_ai_spend(client.utc_day()) == pytest.approx(0.06)


def test_request_uses_prompt_cache_and_asks_for_cost(db, key):
    calls = []
    client.complete("review", "SYSTEM", "USER", "anthropic/claude-sonnet-5", transport=openrouter("ok", calls=calls))
    [body] = calls
    assert body["usage"] == {"include": True}
    assert body["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["messages"][1] == {"role": "user", "content": "USER"}


def test_missing_cost_is_estimated_from_the_model_price(db, key):
    completion = client.complete("review", "s", "u", "anthropic/claude-sonnet-5",
                                 transport=openrouter("ok", cost=None))
    assert completion.cost_usd == pytest.approx((3000 * 2.0 + 300 * 10.0) / 1_000_000)


@pytest.mark.parametrize("failure", ["timeout", "http500", "not_json"])
def test_failure_is_no_answer_and_costs_nothing(db, key, failure):
    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        if failure == "http500":
            return httpx.Response(500, text="boom")
        return httpx.Response(200, text="not json")
    assert client.complete("review", "s", "u", "m", transport=httpx.MockTransport(handler)) is None
    assert database.get_ai_spend(client.utc_day()) == 0


# ---------------------------------------------------------------------------
# Разбор ответа
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("```json\n" + opinion_json("approve", size_multiplier=0.3) + "\n```", ("approve", 1.0)),
    (opinion_json("reduce", size_multiplier=0.5), ("reduce", 0.5)),
    (opinion_json("reject", size_multiplier=0.9), ("reject", 0.0)),
])
def test_opinion_is_parsed_and_normalised(text, expected):
    opinion = review.parse_opinion(text)
    assert (opinion.decision, opinion.size_multiplier) == expected


@pytest.mark.parametrize("text", [
    "", "не json", "[1, 2]", opinion_json("buy"), opinion_json("approve", confidence=1.5),
    opinion_json("reduce", size_multiplier=1.2), opinion_json("approve", confidence="высокая"),
])
def test_malformed_answer_is_no_opinion(text):
    assert review.parse_opinion(text) is None


# ---------------------------------------------------------------------------
# Этап и очередь
# ---------------------------------------------------------------------------

def test_stage(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert worker.stage() == "off"
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.delenv("AI_TRADER_STAGE", raising=False)
    assert worker.stage() == "0"
    monkeypatch.setenv("AI_TRADER_STAGE", "off")
    assert worker.stage() == "off"
    monkeypatch.setenv("AI_TRADER_STAGE", "3")
    assert worker.stage() == "0", "нереализованный этап работает как тень"


def test_submit_does_nothing_when_ai_is_off(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert worker.submit("SOLUSDT", dict(SIGNAL), SimpleNamespace(timestamp=datetime.now(UTC))) is False


# ---------------------------------------------------------------------------
# Мнение: запись, сообщение, связь с исходом
# ---------------------------------------------------------------------------

def test_disagreement_is_stored_and_reported(db, key, offline_market):
    sent = []
    opinion = worker.process(job(), transport=openrouter(opinion_json("reject", confidence=0.8,
                                                                      reasons=["против тренда 4h"])),
                             notify=sent.append)
    assert opinion.decision == "reject"
    [row] = database.get_recent_ai_opinions(5)
    assert row["symbol"] == "SOLUSDT" and row["decision"] == "reject"
    assert len(sent) == 1 and "отклонил бы" in sent[0] and "против тренда 4h" in sent[0]


def test_agreement_is_stored_silently(db, key, offline_market):
    sent = []
    worker.process(job(), transport=openrouter(opinion_json("approve")), notify=sent.append)
    assert sent == []
    assert database.get_ai_opinion_stats(7)["approve"]["total"] == 1


def test_bad_answer_is_recorded_as_no_opinion(db, key, offline_market):
    sent = []
    assert worker.process(job(), transport=openrouter("не знаю"), notify=sent.append) is None
    assert sent == []
    assert database.get_ai_opinion_stats(7)["нет мнения"]["total"] == 1


def test_opinion_is_matched_with_the_signal_outcome(db, key, offline_market):
    worker.process(job(), transport=openrouter(opinion_json("reject")), notify=lambda text: None)
    database.save_signal_outcome({
        "signal_ts": SIGNAL_TS, "symbol": "SOLUSDT", "direction": "LONG", "entry": 100.0, "tp": 106.0,
        "sl": 97.0, "confidence": 0.6, "state_15m": None, "checked_at": datetime.now(UTC).isoformat(),
        "outcome": "LOSS", "candles_checked": 5, "max_favorable_pct": 0.5, "max_adverse_pct": 3.0,
    })
    assert database.get_ai_opinion_stats(7)["reject"]["outcomes"] == {"LOSS": 1}


# ---------------------------------------------------------------------------
# Чат и статистика
# ---------------------------------------------------------------------------

def test_chat_answers_and_explains_when_off(db, key, monkeypatch):
    assert ai_telegram.ask("что с рынком?", transport=openrouter("Рынок в диапазоне.")) == "Рынок в диапазоне."
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert "не подключён" in ai_telegram.ask("что с рынком?")


def test_stats_show_spend_and_opinions(db, key, offline_market):
    worker.process(job(), transport=openrouter(opinion_json("reject"), cost=0.02), notify=lambda text: None)
    text = ai_telegram.stats_text()
    assert "0.020 $" in text and "reject: 1" in text


# ---------------------------------------------------------------------------
# Подключение
# ---------------------------------------------------------------------------

def test_gatekeeper_submits_after_approval_and_before_execution():
    text = (ROOT / "execution" / "gatekeeper.py").read_text(encoding="utf-8")
    submit_at = text.index("ai_submit(symbol, signal_data, snapshot)")
    assert text.index("system_state.add_signal(") < submit_at
    assert submit_at < text.index("self._execute_order(symbol, signal_data, sizing_result)")


def test_bot_registers_ai_commands_and_the_old_assistant_is_gone():
    text = (ROOT / "telegram_commands.py").read_text(encoding="utf-8")
    assert 'CommandHandler("ai", cmd_ai)' in text
    assert 'CommandHandler("ai_stats", cmd_ai_stats)' in text
    assert not (ROOT / "claude_assistant.py").exists()
