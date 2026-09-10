"""
Снимок состояния и множитель просадки (10.09.2026).

До исправления снимок не сохранялся ни разу: в кэше сигналов лежат MarketState,
и json.dumps падал на каждом сохранении. После починки сериализации снимок
обязан вернуться в тот же вид — иначе кэш сигналов перестанет узнавать
состояния, а журнал действий Risk Core — считать время.
"""
import ast
import json
import pathlib
from datetime import datetime, timedelta, UTC

import pytest

import database
from core.market_state import MarketState
from system_state import SystemState

ROOT = pathlib.Path(__file__).resolve().parent.parent


def filled_state():
    state = SystemState()
    state.signal_cache["SOLUSDT"] = MarketState.A
    state.add_signal({"symbol": "SOLUSDT", "side": "LONG", "timestamp": datetime.now(UTC) - timedelta(minutes=3)})
    return state


def assert_revived(restored):
    assert restored.signal_cache["SOLUSDT"] is MarketState.A
    assert isinstance(restored.recent_signals[0]["timestamp"], datetime)
    assert restored.is_new_signal("SOLUSDT", MarketState.A) is False, "то же состояние после рестарта — не новый сигнал"


def test_snapshot_survives_a_json_round_trip():
    text = json.dumps(filled_state().create_snapshot(), default=database._snapshot_json_default)
    restored = SystemState()
    restored.restore_from_snapshot(json.loads(text))
    assert_revived(restored)


def test_unknown_types_still_fail_loudly():
    with pytest.raises(TypeError):
        json.dumps({"x": object()}, default=database._snapshot_json_default)


@pytest.fixture
def db(tmp_path, monkeypatch):
    if getattr(database, "_PG_MODE", False):
        pytest.skip("тест для SQLite")
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(database._thread_local, "conn", None, raising=False)
    yield database
    conn = getattr(database._thread_local, "conn", None)
    if conn is not None:
        conn.close()
    database._thread_local.conn = None


def test_snapshot_survives_the_database(db):
    db.save_system_state_snapshot(filled_state().create_snapshot())
    loaded = db.get_latest_system_state_snapshot()
    restored = SystemState()
    restored.restore_from_snapshot(loaded)
    assert_revived(restored)


# ---------------------------------------------------------------------------
# Проводка
# ---------------------------------------------------------------------------

def _function(path, name):
    text = (ROOT / path).read_text(encoding="utf-8")
    node = next(n for n in ast.walk(ast.parse(text))
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    return ast.get_source_segment(text, node)


def test_drawdown_multiplier_is_applied_between_decision_and_final_size():
    src = _function("execution/gatekeeper.py", "send_signal")
    apply = 'signal_data["position_size"] = original_size * dd_multiplier'
    assert apply in src
    assert src.index("decision = self.decision_core.should_i_trade(") < src.index(apply) < src.index("finalize_position_size(")


def test_analysis_cycle_keeps_database_calls_off_the_event_loop():
    src = _function("runner.py", "run_market_analysis")
    assert "await asyncio.to_thread(decision_core.should_i_trade" in src
    assert "await asyncio.to_thread(SystemStateSnapshotStore.save, snapshot)" in src
    assert "await asyncio.to_thread(cleanup_old_snapshots" in src
