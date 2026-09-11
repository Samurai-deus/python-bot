"""
Флаги защитного режима следуют за автоматом состояний (11.09.2026).

До этого system_health.safe_mode и trading_paused менял только
sync_to_system_state, а его звали ручная пауза и возобновление да ветка
возобновления в цикле анализа. Переход в SAFE_MODE (сердцебиение, сторож цикла,
ошибки анализа) флаги не трогал. Торговлю при этом держал предохранитель — он
смотрит сам автомат, — но Decision Core, отчёты и восстановление в цикле видели
«всё в порядке», и из SAFE_MODE выводил только TTL → FATAL → перезапуск.

Сквозной тест восстановления — в tests/test_analysis_cycle.py.
"""
import asyncio
import inspect
from types import SimpleNamespace

import runner
from system_state_machine import SystemState as MachineState, SystemStateMachine


def machine_with_listener():
    calls = []
    machine = SystemStateMachine(exit_fn=lambda code: None)
    machine.set_transition_listener(lambda: calls.append(machine.state))
    return machine, calls


def test_every_transition_notifies_the_listener():
    machine, calls = machine_with_listener()

    async def go():
        await machine.transition_to(MachineState.SAFE_MODE, "test", "test")
        await machine.transition_to(MachineState.RECOVERING, "test", "test")
        await machine.transition_to(MachineState.RUNNING, "test", "test")

    asyncio.run(go())
    assert calls == [MachineState.SAFE_MODE, MachineState.RECOVERING, MachineState.RUNNING]


def test_a_denied_transition_does_not_notify():
    machine, calls = machine_with_listener()
    assert asyncio.run(machine.transition_to(MachineState.RECOVERING, "test", "test")) is False
    assert calls == []


def test_automatic_transitions_notify_too():
    """record_error переводит автомат сам, под своей блокировкой: RUNNING → DEGRADED → SAFE_MODE."""
    machine, calls = machine_with_listener()

    async def go():
        for _ in range(5):
            await machine.record_error("boom")

    asyncio.run(go())
    assert calls == [MachineState.DEGRADED, MachineState.SAFE_MODE]


def test_a_failing_listener_does_not_cancel_the_transition():
    machine = SystemStateMachine(exit_fn=lambda code: None)

    def broken():
        raise RuntimeError("listener down")

    machine.set_transition_listener(broken)
    assert asyncio.run(machine.transition_to(MachineState.SAFE_MODE, "test", "test")) is True
    assert machine.state == MachineState.SAFE_MODE


def test_runner_sync_takes_the_machine_state_and_the_manual_pause(monkeypatch):
    machine = SystemStateMachine(exit_fn=lambda code: None)
    state = SimpleNamespace(system_health=SimpleNamespace(safe_mode=False, trading_paused=False))
    monkeypatch.setattr(runner, "get_state_machine", lambda: machine)
    monkeypatch.setattr(runner, "system_state", state)
    monkeypatch.setitem(runner._control_plane_state, "manual_pause_active", True)

    runner._sync_flags_from_machine()
    assert state.system_health.safe_mode is False and state.system_health.trading_paused is True

    asyncio.run(machine.transition_to(MachineState.SAFE_MODE, "test", "test"))
    runner._sync_flags_from_machine()
    assert state.system_health.safe_mode is True and state.system_health.trading_paused is True


def test_main_registers_the_listener_on_the_process_state_machine():
    src = inspect.getsource(runner.main)
    created = src.index("state_machine = get_state_machine(safe_mode_ttl=SAFE_MODE_TTL)")
    assert created < src.index("state_machine.set_transition_listener(_sync_flags_from_machine)")
