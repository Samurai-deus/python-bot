"""
Журнал всех сигналов-кандидатов и разметка их исходов (шаг 3 плана обучения, 11.09.2026).

До этого журнал в режиме SQLite писался в signals_log.csv внутри контейнера и пропадал
с каждым деплоем, в него попадали только отправленные сигналы, а outcome tracker читал
тот же пустой файл. Теперь каждый кандидат — отправленный, заблокированный гейткипером
и отсеянный генератором — лежит в signal_journal с причиной, и исход размечается у всех.
"""
import pathlib
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import database
import journal
from brains import outcome_tracker
from execution.gatekeeper import Gatekeeper
from system_state import SystemState

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def db(tmp_path, monkeypatch):
    if getattr(database, "_PG_MODE", False):
        pytest.skip("тест для SQLite")
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "journal.db"))
    monkeypatch.setattr(database._thread_local, "conn", None, raising=False)
    journal._recent.clear()
    yield database
    journal._recent.clear()
    conn = getattr(database._thread_local, "conn", None)
    if conn is not None:
        conn.close()
    database._thread_local.conn = None


def put(status, *, symbol="ADAUSDT", side="LONG", hours_ago=30.0, code=None, collapse=False):
    """LONG от 100: цель 104, стоп 98."""
    return journal.record_signal(
        symbol=symbol, side=side, entry=100.0, stop=98.0, target=104.0, status=status,
        reason_code=code, reason=code and f"причина {code}", strategy="breakout",
        timestamp=datetime.now(UTC) - timedelta(hours=hours_ago), collapse_repeats=collapse,
    )


def candle(high, low, open_=100.0):
    return ["0", str(open_), str(high), str(low), "100"]


def test_every_fate_is_journaled_with_its_reason(db):
    assert put(journal.SENT)
    assert put(journal.BLOCKED, symbol="DOTUSDT", code="RiskCore")
    assert put(journal.SKIPPED, symbol="ARBUSDT", code="no_room")
    rows = {r["symbol"]: r for r in journal.get_recent_signals()}
    assert (rows["DOTUSDT"]["status"], rows["DOTUSDT"]["reason_code"]) == ("BLOCKED", "RiskCore")
    assert (rows["ARBUSDT"]["status"], rows["ARBUSDT"]["reason"]) == ("SKIPPED", "причина no_room")
    assert rows["ADAUSDT"]["strategy"] == "breakout" and rows["ADAUSDT"]["direction"] == "LONG"
    assert [r["symbol"] for r in journal.get_recent_signals(statuses=[journal.SENT])] == ["ADAUSDT"]


def test_repeats_of_a_pre_filter_collapse_but_sent_signals_do_not(db):
    assert put(journal.SKIPPED, code="no_room", collapse=True)
    assert not put(journal.SKIPPED, code="no_room", collapse=True), "тот же отсев в течение часа"
    assert put(journal.SKIPPED, code="funding", collapse=True), "другая причина — отдельная запись"
    assert put(journal.SENT) and put(journal.SENT)
    assert len(journal.get_recent_signals()) == 4


def test_a_journal_write_failure_does_not_raise(db, monkeypatch):
    def broken(row):
        raise RuntimeError("диск")
    monkeypatch.setattr(database, "log_signal_to_db", broken)
    assert put(journal.SENT) is False


def test_an_old_journal_table_gets_the_new_columns(tmp_path):
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.execute("CREATE TABLE signal_journal (id INTEGER PRIMARY KEY, timestamp TEXT, symbol TEXT, decision TEXT)")
    database._ensure_signal_journal_columns(conn.cursor())
    database._ensure_signal_journal_columns(conn.cursor())  # повторный запуск — без ошибок
    columns = {row[1] for row in conn.execute("PRAGMA table_info(signal_journal)")}
    conn.close()
    assert {"status", "reason_code", "reason", "strategy", "score"} <= columns


def test_outcomes_cover_blocked_and_skipped_signals(db, monkeypatch):
    put(journal.SENT, symbol="WINUSDT")
    put(journal.BLOCKED, symbol="LOSSUSDT", code="RiskCore")
    put(journal.SKIPPED, symbol="FLATUSDT", code="no_room")
    paths = {
        "WINUSDT": [candle(101, 99), candle(105, 99.5)],
        "LOSSUSDT": [candle(101, 99), candle(100.5, 97)],
        "FLATUSDT": [candle(101, 99)] * (outcome_tracker.MAX_CANDLES + 2),
    }
    monkeypatch.setattr(outcome_tracker, "_fetch_candles_since", lambda symbol, start_ms, limit=26: paths[symbol])
    assert outcome_tracker.run_outcome_check() == 3
    outcomes = {r["symbol"]: r["outcome"] for r in database.get_outcomes_for_analysis(days=3)}
    assert outcomes == {"WINUSDT": "WIN", "LOSSUSDT": "LOSS", "FLATUSDT": "NEUTRAL"}
    assert outcome_tracker.run_outcome_check() == 0, "размеченные не проверяются снова"


def test_neutral_waits_for_the_whole_window(db, monkeypatch):
    """Шесть часов без касаний — ещё не NEUTRAL: цель или стоп могут быть задеты позже."""
    put(journal.SENT, hours_ago=6)
    monkeypatch.setattr(outcome_tracker, "_fetch_candles_since", lambda *a, **k: [candle(101, 99)] * 6)
    assert outcome_tracker.run_outcome_check() == 0
    later = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    assert len(database.get_signals_to_evaluate("1970-01-01", later)) == 1, "остаётся в очереди"


def test_too_young_signals_are_not_checked(db, monkeypatch):
    put(journal.SENT, hours_ago=outcome_tracker.MIN_AGE_HOURS / 2)
    monkeypatch.setattr(outcome_tracker, "_fetch_candles_since", lambda *a, **k: pytest.fail("рано"))
    assert outcome_tracker.run_outcome_check() == 0


def test_the_gatekeeper_names_the_refusal():
    keeper = SimpleNamespace(trace_enabled=False, decision_trace=None)
    trace = [("META", True, "ok", None), ("RiskCore", False, "PORTFOLIO_OPEN_RISK", None)]
    Gatekeeper._save_decision_trace(keeper, "ADAUSDT", None, trace, final_decision="BLOCK")
    assert keeper.last_block_reason == ("RiskCore", "PORTFOLIO_OPEN_RISK")
    Gatekeeper._save_decision_trace(keeper, "ADAUSDT", None, [], final_decision="ERROR")
    assert keeper.last_block_reason == ("error", "Final decision: ERROR")


def test_novelty_can_be_checked_without_consuming_it():
    state = SystemState()
    assert state.would_be_new_signal("ADAUSDT", "A") and state.would_be_new_signal("ADAUSDT", "A")
    assert state.is_new_signal("ADAUSDT", "A")
    assert not state.would_be_new_signal("ADAUSDT", "A")
    assert state.would_be_new_signal("ADAUSDT", "B")
    assert state.would_be_new_signal("DOTUSDT", None) and state.is_new_signal("DOTUSDT", None)
    assert not state.would_be_new_signal("DOTUSDT", None), "повтор тренд-сигнала — через 4 часа"


def test_the_turn_limit_does_not_consume_novelty():
    source = (ROOT / "signal_generator.py").read_text(encoding="utf-8")
    cap = source.index('if stats["signals_sent"] >= MAX_NEW_POSITIONS_PER_TURN:')
    assert cap < source.index("would_be_new_signal(symbol, state_15m)", cap) \
        < source.index("system_state.is_new_signal(symbol, state_15m)")


def test_every_exit_after_a_setup_reaches_the_journal():
    source = (ROOT / "signal_generator.py").read_text(encoding="utf-8")
    assert source.count("_journal(journal.SKIPPED,") == 7, \
        "high_risk, low_rr, no_room, funding ×2, turn_limit, learner"
    assert source.count("seen_by=system_state") == 6, "отсев до новизны — без повторов сетапа"
    assert source.count("status=journal.BLOCKED") == 2, "отказ гейткипера и сбой отправки"
    assert "SignalSnapshotStore.save(snapshot, strategy=strategy_name)" in source


def test_the_journal_lives_only_in_the_database():
    for path in ("journal.py", "brains/outcome_tracker.py"):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert "open(\"signals_log" not in text and "import csv" not in text
