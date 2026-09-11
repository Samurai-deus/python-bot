"""
ИИ-трейдер на всех сигналах (шаг 5 плана обучения, 11.09.2026) и починка обрыва ответа.

До этого ИИ видел только взятые сигналы, а все его мнения 11.09 записались как
bad_format: ответ упирался в max_tokens=700 и JSON обрывался. Теперь на оценку идут и
заблокированные, и отсеянные сигналы — пока хватает резерва бюджета, — а недельный
отчёт показывает, как отработали сигналы, которые ИИ одобрил бы и отклонил бы.
"""
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

import database
import journal
from ai_trader import client, prompts, worker
from analytics import weekly_report as wr
from tests.test_ai_trader import db, job, key, offline_market, opinion_json, openrouter  # noqa: F401


@pytest.fixture(autouse=True)
def empty_queue(monkeypatch):
    monkeypatch.setattr(worker, "_ensure_thread", lambda: None)
    yield
    while not worker._queue.empty():
        worker._queue.get_nowait()


def cut_off(content='{"decision": "reduce", "size_multiplier": 0.5, "reasons": ["Стоп'):
    return httpx.MockTransport(lambda request: httpx.Response(200, json={
        "model": "anthropic/claude-sonnet-5",
        "choices": [{"message": {"content": content}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 2200, "completion_tokens": 700, "cost": 0.01}}))


def opinion_error():
    conn = database.get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT error FROM ai_opinions")
        return [row["error"] for row in cursor.fetchall()]
    finally:
        conn.close()


def test_the_review_asks_for_enough_tokens(db, key, offline_market):
    calls = []
    worker.process(job(), transport=openrouter(opinion_json(), calls=calls), notify=lambda text: None)
    assert calls[0]["max_tokens"] == worker.REVIEW_MAX_TOKENS >= 1500


def test_a_cut_off_answer_is_recorded_as_truncated(db, key, offline_market):
    assert worker.process(job(), transport=cut_off(), notify=lambda text: None) is None
    assert opinion_error() == ["truncated"]


def test_a_signal_the_system_did_not_take_is_reviewed_while_the_budget_allows(db, key):
    assert worker.submit("DOTUSDT", {"side": "SHORT"}, None, fate="BLOCKED", signal_ts="2026-09-11T14:00:00+00:00")
    database.record_ai_usage(client.utc_day(), "review", "m", 0, 0, 0.6)
    assert not worker.submit("ARBUSDT", {"side": "SHORT"}, None, fate="SKIPPED",
                             signal_ts="2026-09-11T14:05:00+00:00"), "резерв — половина суточного бюджета"
    assert worker.submit("SOLUSDT", {"side": "LONG"}, job()["snapshot"]), "взятый сигнал — и без резерва"


def test_signals_the_system_did_not_take_fill_at_most_half_the_queue(db, key):
    for _ in range(worker.QUEUE_SIZE // 2):
        worker._queue.put_nowait({})
    assert not worker.submit("DOTUSDT", {}, None, fate="BLOCKED", signal_ts="2026-09-11T14:00:00+00:00")
    assert worker.submit("SOLUSDT", {"side": "LONG"}, job()["snapshot"])


def test_no_timestamp_and_no_snapshot_is_nothing_to_review(db, key):
    assert not worker.submit("DOTUSDT", {}, None, fate="BLOCKED")


def test_no_disagreement_message_for_a_signal_the_system_did_not_take(db, key, offline_market):
    sent = []
    blocked = dict(job(), fate="BLOCKED", snapshot=None)
    opinion = worker.process(blocked, transport=openrouter(opinion_json("reject")), notify=sent.append)
    assert opinion.decision == "reject" and sent == []


def test_the_prompt_does_not_tell_the_systems_decision():
    assert "пропустила его через свои правила" not in prompts.REVIEW_SYSTEM
    assert "Решение самой системы тебе не сообщается" in prompts.REVIEW_SYSTEM
    assert "не длиннее 15 слов" in prompts.REVIEW_SYSTEM


def test_a_journaled_skip_goes_to_the_ai_with_the_journal_timestamp(db, monkeypatch):
    import signal_generator
    submitted = []
    monkeypatch.setattr(worker, "submit", lambda *args, **kwargs: submitted.append((args, kwargs)))
    journal._recent.clear()
    signal_generator._journal(journal.SKIPPED, "no_room", "портфель заполнен", mode="TRADE", symbol="ADAUSDT",
                              side="LONG", entry=100.0, stop=98.0, target=104.0, states={}, risk="LOW",
                              score=70, strategy="breakout", collapse_repeats=True)
    (args, kwargs), = submitted
    assert args[0] == "ADAUSDT" and args[2] is None and kwargs["fate"] == "SKIPPED"
    assert args[1]["rr_ratio"] == pytest.approx(2.0) and args[1]["strategy_name"] == "breakout"
    assert kwargs["signal_ts"] == database.get_signals_from_db("1970-01-01")[0]["timestamp"]
    journal._recent.clear()


def test_a_blocked_signal_goes_to_the_ai():
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "signal_generator.py").read_text(encoding="utf-8")
    blocked = source.index("status=journal.BLOCKED, reason_code=code")
    assert blocked < source.index("ai_submit(symbol, signal_data, snapshot, fate=journal.BLOCKED)", blocked)


def test_the_weekly_report_shows_whether_the_ai_is_right():
    verdicts = [{"decision": "approve", "rr_ratio": 2.0, "outcome": "WIN"},
                {"decision": "approve", "rr_ratio": 2.0, "outcome": "LOSS"},
                {"decision": "reject", "rr_ratio": 2.0, "outcome": "LOSS"},
                {"decision": None, "error": "truncated", "rr_ratio": 2.0, "outcome": None}]
    lines = wr.ai_section(verdicts)
    assert lines[0].startswith("ИИ (тень): мнений 3, без мнения 1.")
    assert "• одобрил бы: 2 — цель 1, стоп 1, без касаний 0, ожидание +0,50 R" in lines
    assert "• отклонил бы: 1 — цель 0, стоп 1, без касаний 0, ожидание -1,00 R" in lines


def test_ai_verdicts_join_the_journal_and_the_outcome(db):
    moment = datetime.now(UTC) - timedelta(hours=30)
    journal._recent.clear()
    journal.record_signal(symbol="DOTUSDT", side="SHORT", entry=100.0, stop=102.0, target=96.0,
                          status=journal.BLOCKED, reason_code="RiskCore", timestamp=moment)
    ts = moment.isoformat()
    database.save_ai_opinion(signal_ts=ts, symbol="DOTUSDT", side="SHORT", stage="0", model="m", decision="approve",
                             size_multiplier=1.0, confidence=0.7, reasons=[], key_risk="", cost_usd=0.01,
                             latency_ms=1, error=None)
    database.save_signal_outcome({"signal_ts": ts, "symbol": "DOTUSDT", "direction": "SHORT", "entry": 100.0,
                                  "tp": 96.0, "sl": 102.0, "checked_at": ts, "outcome": "WIN"})
    verdict, = database.get_ai_verdicts((moment - timedelta(minutes=1)).isoformat())
    assert (verdict["decision"], verdict["status"], verdict["outcome"]) == ("approve", "BLOCKED", "WIN")
    assert verdict["rr_ratio"] == pytest.approx(2.0)
    assert json.dumps(verdict)  # строка отчёта собирается из простых значений
