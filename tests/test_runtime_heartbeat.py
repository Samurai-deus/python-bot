"""
Runtime heartbeat (loops/runtime_heartbeat.py), вынесенный из runner.py
(пункт 5 плана отложенного, docs/DEFERRED_PLAN.md, шаг 7).

До переноса у цикла был один тест — текстовый, что он ставит метку liveness.
Здесь поведение: удары и метки, пропуск ударов → защитный режим, короткий
пропуск только считается, инъекция остановки цикла, живучесть при ошибке,
остановка. Интервал укорочен параметром, время подменено, автомат состояний —
подменный.
"""
import ast
import asyncio
import inspect
import logging
import pathlib
from types import SimpleNamespace

import pytest

from control_plane import state as cp_state
from loops import runtime_heartbeat

ROOT = pathlib.Path(__file__).resolve().parent.parent
INTERVAL = 0.01


def make_state(running=True, consecutive_errors=0, fail_first_update=False):
    errors, updates = [], []

    def update_heartbeat():
        updates.append(1)
        if fail_first_update and len(updates) == 1:
            raise RuntimeError("state store hiccup")

    state = SimpleNamespace(
        system_health=SimpleNamespace(is_running=running, consecutive_errors=consecutive_errors),
        record_error=errors.append,
        update_heartbeat=update_heartbeat,
    )
    return state, errors, updates


class FakeMachine:
    def __init__(self, safe=False):
        self.is_safe_mode = safe
        self.transitions = []

    async def transition_to(self, target, **kwargs):
        self.transitions.append((target, kwargs.get("owner"), kwargs.get("metadata")))
        self.is_safe_mode = True


class JumpingClock:
    """Подмена модуля time: старт, дальше каждый удар «через» step секунд."""

    def __init__(self, step):
        self.now = 1000.0
        self.step = step
        self.started = False

    def time(self):
        if self.started:
            self.now += self.step
        self.started = True
        return self.now


@pytest.fixture
def beat(monkeypatch):
    machine = FakeMachine()
    marks = []
    metrics = {"scheduler_stalls_total": 0}
    monkeypatch.setattr(runtime_heartbeat, "get_state_machine", lambda: machine)
    monkeypatch.setattr(runtime_heartbeat, "liveness", SimpleNamespace(mark=marks.append))
    monkeypatch.setattr(cp_state, "prometheus_metrics", metrics)
    monkeypatch.setitem(cp_state.chaos, "was_active", False)

    def run(state, beats, step, **kwargs):
        """Гоняет цикл, пока on_heartbeat не отметит `beats` ударов; время скачет на step."""
        monkeypatch.setattr(runtime_heartbeat, "time", JumpingClock(step))
        ticks = []

        async def scenario():
            shutdown = asyncio.Event()

            def on_heartbeat():
                ticks.append(1)
                if len(ticks) >= beats:
                    shutdown.set()

            await runtime_heartbeat.runtime_heartbeat_loop(lambda: state, shutdown, on_heartbeat,
                                                           interval=INTERVAL, **kwargs)

        asyncio.run(asyncio.wait_for(scenario(), 5.0))
        return ticks

    return SimpleNamespace(machine=machine, marks=marks, metrics=metrics, run=run)


def test_each_beat_marks_the_state_the_watchdog_and_the_healthcheck(beat):
    state, errors, updates = make_state()
    ticks = beat.run(state, beats=3, step=INTERVAL)
    assert len(ticks) == 3 and len(updates) == 3
    assert beat.marks == ["heartbeat"] * 3
    assert beat.machine.transitions == [] and errors == []
    assert beat.metrics["scheduler_stalls_total"] == 0


def test_missed_beats_force_safe_mode(beat):
    state, errors, _ = make_state()
    beat.run(state, beats=1, step=1.0)  # удар пришёл через 100 интервалов — цикл событий стоял
    assert len(beat.machine.transitions) == 1
    target, owner, metadata = beat.machine.transitions[0]
    assert target == runtime_heartbeat.SystemStateEnum.SAFE_MODE and owner == "runtime_heartbeat_loop"
    assert metadata["missed_heartbeats"] >= 2
    assert beat.metrics["scheduler_stalls_total"] == 1
    assert beat.metrics["heartbeat_enforcement_total"] == 1
    assert len(errors) == 1 and errors[0].startswith("HEARTBEAT_MISS_ENFORCEMENT: heartbeat-miss-")


def test_a_short_miss_is_counted_but_not_enforced(beat):
    state, errors, _ = make_state()
    beat.run(state, beats=1, step=2.5 * INTERVAL)  # один пропущенный удар — ниже порога
    assert beat.metrics["scheduler_stalls_total"] == 1
    assert beat.machine.transitions == [] and errors == []


def test_no_second_transition_when_already_in_safe_mode(beat):
    beat.machine.is_safe_mode = True
    state, errors, _ = make_state()
    beat.run(state, beats=1, step=1.0)
    assert beat.metrics["scheduler_stalls_total"] == 1
    assert beat.machine.transitions == [] and errors == []


def test_safe_mode_after_chaos_is_logged_as_the_invariant(beat, caplog):
    cp_state.chaos["was_active"] = True
    caplog.set_level(logging.INFO)
    state, _, _ = make_state()
    beat.run(state, beats=1, step=1.0)
    assert "CHAOS_INVARIANT_SATISFIED" in caplog.text


def test_loop_stall_injection_enters_safe_mode_after_too_many_errors(beat):
    state, errors, _ = make_state(consecutive_errors=3)
    beat.run(state, beats=1, step=2.5 * INTERVAL, fault_inject_loop_stall=True, max_consecutive_errors=3)
    assert errors == ["FAULT_INJECTION: loop_stall_detected"]
    assert beat.machine.transitions == [(runtime_heartbeat.SystemStateEnum.SAFE_MODE, "runtime_heartbeat_loop",
                                         {"consecutive_errors": 3})]


def test_an_error_in_one_beat_does_not_stop_the_heartbeat(beat):
    state, _, updates = make_state(fail_first_update=True)
    ticks = beat.run(state, beats=2, step=INTERVAL)
    assert len(updates) == 3, "первый удар упал, цикл пошёл дальше"
    assert len(ticks) == 2


def test_the_heartbeat_does_nothing_when_the_bot_is_stopping(beat):
    state, _, updates = make_state(running=False)
    assert beat.run(state, beats=1, step=INTERVAL) == []
    assert updates == [] and beat.marks == []


def test_runner_registers_the_heartbeat_with_its_constants():
    text = (ROOT / "runner.py").read_text(encoding="utf-8")
    assert "async def runtime_heartbeat_loop(" not in text, "цикл остался и в runner.py — две копии"
    calls = [n for n in ast.walk(ast.parse(text)) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "runtime_heartbeat_loop"
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "runtime_heartbeat"]
    assert len(calls) == 1
    call = calls[0]
    assert [ast.unparse(a) for a in call.args] == ["_state", "get_shutdown_event()", "update_heartbeat_thread_safe"]
    assert {k.arg: ast.unparse(k.value) for k in call.keywords} == {
        "interval": "RUNTIME_HEARTBEAT_INTERVAL",
        "miss_threshold": "HEARTBEAT_MISS_THRESHOLD",
        "enforcement_threshold": "HEARTBEAT_MISS_ENFORCEMENT_THRESHOLD",
        "fault_inject_loop_stall": "FAULT_INJECT_LOOP_STALL",
        "max_consecutive_errors": "MAX_CONSECUTIVE_ERRORS",
    }


def test_defaults_match_the_runner_constants():
    import runner
    params = inspect.signature(runtime_heartbeat.runtime_heartbeat_loop).parameters
    assert params["interval"].default == runner.RUNTIME_HEARTBEAT_INTERVAL
    assert params["miss_threshold"].default == runner.HEARTBEAT_MISS_THRESHOLD
    assert params["enforcement_threshold"].default == runner.HEARTBEAT_MISS_ENFORCEMENT_THRESHOLD
