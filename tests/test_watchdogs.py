"""
Сторожа процесса в control_plane/watchdogs.py (пункт 5 плана отложенного,
docs/DEFERRED_PLAN.md, шаг 9).

Поведение ThreadWatchdog и FatalReaper проверяет tests/test_invariants.py —
он по-прежнему импортирует их из runner. Здесь — связка после переноса:
runner держит те же объекты, метки сердцебиения пишутся в его SystemState,
а копий в runner не осталось.
"""
import pathlib
from datetime import UTC, datetime, timedelta

import pytest

import runner
from control_plane import watchdogs
from system_state_machine import SystemStateMachine

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_runner_keeps_the_very_same_objects():
    for name in ("ThreadWatchdog", "ThreadWatchdogState", "FatalReaper", "get_thread_watchdog",
                 "update_heartbeat_thread_safe", "_runtime_state"):
        assert getattr(runner, name) is getattr(watchdogs, name), name
    assert runner.FATAL_EXIT_CODE == watchdogs.FATAL_EXIT_CODE == 10
    assert runner.THREAD_WATCHDOG_HEARTBEAT_TIMEOUT == watchdogs.THREAD_WATCHDOG_HEARTBEAT_TIMEOUT


def test_heartbeat_marks_go_to_the_process_state(monkeypatch):
    assert watchdogs.system_state is runner.system_state
    health = runner.system_state.system_health
    monkeypatch.setattr(health, "last_heartbeat", health.last_heartbeat)  # вернуть после теста
    when = datetime.now(UTC) - timedelta(seconds=42)
    runner.update_heartbeat_thread_safe(when)
    assert health.last_heartbeat == when
    assert watchdogs.get_last_heartbeat_timestamp() == pytest.approx(when.timestamp())


def test_watchdogs_exit_through_the_module_hard_exit_by_default():
    machine = SystemStateMachine(exit_fn=lambda code: None)
    assert watchdogs.ThreadWatchdog(machine, heartbeat_timeout=1.0)._exit_fn is watchdogs._hard_exit
    assert watchdogs.FatalReaper(machine)._exit_fn is watchdogs._hard_exit


def test_configure_takes_exactly_the_process_state():
    with pytest.raises(TypeError):
        watchdogs.configure()
    with pytest.raises(TypeError):
        watchdogs.configure(system_state=runner.system_state, extra=1)


def test_no_second_copy_is_left_in_runner():
    text = (ROOT / "runner.py").read_text(encoding="utf-8")
    for definition in ("class ThreadWatchdog", "class FatalReaper", "class RuntimeState", "def _hard_exit",
                       "def update_heartbeat_thread_safe", "def get_last_heartbeat_timestamp"):
        assert definition not in text, f"{definition} остался и в runner.py"
    assert "watchdogs.configure(system_state=system_state)" in text
