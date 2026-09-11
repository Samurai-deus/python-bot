"""
Контролёры процесса, вынесенные из runner.py в loops/monitors.py (пункт 5 плана
отложенного, шаг 2): сторож блокировки цикла, TTL защитного режима, heartbeat
в Telegram. Интервалы укорочены параметрами; автомат состояний и отправка —
подменные.
"""
import asyncio
import pathlib
from datetime import datetime, timedelta, UTC
from types import SimpleNamespace

import pytest

from loops import monitors

ROOT = pathlib.Path(__file__).resolve().parent.parent


def run(coro, timeout=5.0):
    return asyncio.run(asyncio.wait_for(coro, timeout))


def make_state(running=True, last_heartbeat=None):
    errors, beats = [], []
    state = SimpleNamespace(
        system_health=SimpleNamespace(is_running=running, last_heartbeat=last_heartbeat),
        record_error=errors.append,
        update_heartbeat=lambda: beats.append(1),
    )
    return state, errors, beats


class FakeMachine:
    def __init__(self, on_call=None):
        self.is_safe_mode = False
        self.transitions = []
        self.ttl_checks = 0
        self._on_call = on_call

    async def transition_to(self, target, **kwargs):
        self.transitions.append(target)
        self.is_safe_mode = True
        if self._on_call:
            self._on_call()

    async def check_safe_mode_ttl(self):
        self.ttl_checks += 1
        if self._on_call:
            self._on_call()
        return False


def test_loop_guard_enters_safe_mode_after_a_blocked_loop(monkeypatch):
    async def scenario():
        shutdown = asyncio.Event()
        machine = FakeMachine(on_call=shutdown.set)
        monkeypatch.setattr(monitors, "get_state_machine", lambda: machine)
        state, errors, _ = make_state(last_heartbeat=datetime.now(UTC) - timedelta(seconds=400))
        await monitors.loop_guard_watchdog(lambda: state, shutdown, timeout_seconds=300, check_every=0.01)
        return machine, errors

    machine, errors = run(scenario())
    assert machine.transitions == [monitors.SystemStateEnum.SAFE_MODE]
    assert len(errors) == 1 and errors[0].startswith("LOOP_GUARD_TIMEOUT: loop-guard-")


def test_loop_guard_stays_quiet_while_heartbeats_are_fresh(monkeypatch):
    async def scenario():
        shutdown = asyncio.Event()
        machine = FakeMachine()
        monkeypatch.setattr(monitors, "get_state_machine", lambda: machine)
        state, errors, _ = make_state(last_heartbeat=datetime.now(UTC))
        task = asyncio.create_task(monitors.loop_guard_watchdog(lambda: state, shutdown, 300, check_every=0.01))
        await asyncio.sleep(0.05)
        shutdown.set()
        await task
        return machine, errors

    machine, errors = run(scenario())
    assert machine.transitions == [] and errors == []


def test_safe_mode_ttl_is_checked_through_the_state_machine(monkeypatch):
    async def scenario():
        shutdown = asyncio.Event()
        machine = FakeMachine(on_call=shutdown.set)
        monkeypatch.setattr(monitors, "get_state_machine", lambda: machine)
        state, _, _ = make_state()
        await monitors.safe_mode_ttl_monitor(lambda: state, shutdown, check_every=0.01)
        return machine

    assert run(scenario()).ttl_checks == 1


def test_heartbeat_is_sent_and_marks_the_thread_watchdog():
    async def scenario():
        shutdown = asyncio.Event()
        sent, marks = [], []

        async def send():
            sent.append(1)
            shutdown.set()

        state, _, beats = make_state()
        await monitors.heartbeat_loop(lambda: state, shutdown, lambda: marks.append(1), interval=0.01, send=send)
        return sent, beats, marks

    assert run(scenario()) == ([1], [1], [1])


def test_monitors_do_nothing_when_the_bot_is_stopping(monkeypatch):
    machine = FakeMachine()
    monkeypatch.setattr(monitors, "get_state_machine", lambda: machine)
    state, errors, beats = make_state(running=False)

    async def never():
        pytest.fail("остановленный бот heartbeat не шлёт")

    run(monitors.loop_guard_watchdog(lambda: state, asyncio.Event(), 300))
    run(monitors.safe_mode_ttl_monitor(lambda: state, asyncio.Event()))
    run(monitors.heartbeat_loop(lambda: state, asyncio.Event(), lambda: None, send=never))
    assert machine.transitions == [] and machine.ttl_checks == 0 and errors == [] and beats == []


def test_runner_registers_the_monitors_with_its_state():
    text = (ROOT / "runner.py").read_text(encoding="utf-8")
    assert ('monitors.loop_guard_watchdog(_state, get_shutdown_event(), LOOP_GUARD_TIMEOUT), '
            'name="LoopGuardWatchdog"') in text
    assert 'monitors.safe_mode_ttl_monitor(_state, get_shutdown_event()), name="SafeModeTTLMonitor"' in text
    assert ('monitors.heartbeat_loop(_state, get_shutdown_event(), update_heartbeat_thread_safe), '
            'name="TelegramHeartbeat"') in text
    for name in ("loop_guard_watchdog", "safe_mode_ttl_monitor", "heartbeat_loop"):
        assert f"async def {name}(" not in text, f"{name} остался и в runner.py — две копии"
