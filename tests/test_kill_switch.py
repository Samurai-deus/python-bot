"""
Предохранитель (аудит 10.09.2026, блокер B-3): /pause обязан останавливать
открытие позиций, а не только менять флаг, который никто не читает.
"""
from types import SimpleNamespace

import pytest

from execution.kill_switch import trading_halt_reason
from system_state_machine import SystemState as MachineState


def machine(state=MachineState.RUNNING, paused=False):
    return SimpleNamespace(state=state, trading_paused=paused)


def sys_state(paused=False):
    return SimpleNamespace(system_health=SimpleNamespace(trading_paused=paused))


def risk(state="SAFE"):
    return SimpleNamespace(risk_state=SimpleNamespace(value=state))


# ---------------------------------------------------------------------------
# Логика предиката
# ---------------------------------------------------------------------------

def test_all_clear_allows():
    assert trading_halt_reason(sys_state(), machine(), risk()) is None


def test_manual_pause_blocks():
    reason = trading_halt_reason(sys_state(paused=True), machine(), risk())
    assert reason and "вручную" in reason


@pytest.mark.parametrize("state", [s for s in MachineState if s != MachineState.RUNNING])
def test_every_non_running_state_blocks(state):
    assert trading_halt_reason(sys_state(), machine(state=state), risk()) is not None


def test_machine_level_pause_blocks():
    assert trading_halt_reason(sys_state(), machine(paused=True), risk()) is not None


@pytest.mark.parametrize("state", ["LOCKED", "HALTED"])
def test_blocking_risk_states_block(state):
    reason = trading_halt_reason(sys_state(), machine(), risk(state))
    assert reason and state in reason


@pytest.mark.parametrize("state", ["SAFE", "LIMITED"])
def test_non_blocking_risk_states_allow(state):
    """LIMITED — решение Risk Core урезать размер, а не запрет."""
    assert trading_halt_reason(sys_state(), machine(), risk(state)) is None


def test_risk_core_can_be_skipped_for_pre_evaluation_check():
    """
    Проверка до оценки сигнала не должна смотреть на Risk Core: его состояние
    осталось от прошлой оценки, возможно по другому символу.
    """
    assert trading_halt_reason(sys_state(), machine(), risk("LOCKED"), include_risk_core=False) is None


def test_missing_system_state_blocks(monkeypatch):
    import system_state as ss_module
    monkeypatch.setattr(ss_module, "get_system_state", lambda: None)
    reason = trading_halt_reason(None, machine(), risk())
    assert reason and "недоступно" in reason


def test_failure_inside_check_blocks():
    """Не можем удостовериться, что торговать можно, — значит, нельзя."""
    class Broken:
        @property
        def state(self):
            raise RuntimeError("state machine is broken")

    reason = trading_halt_reason(sys_state(), Broken(), risk())
    assert reason and "RuntimeError" in reason


# ---------------------------------------------------------------------------
# Сквозная проверка: /pause действительно пишет то, что читает предохранитель
# ---------------------------------------------------------------------------

def test_pause_command_is_seen_by_kill_switch():
    """
    Суть блокера: pause_trading_manually() ставил флаг, который никто в пути
    до ордера не читал. Здесь проверяется связь целиком — настоящая функция
    паузы и настоящий глобальный объект состояния, который читает предохранитель.
    Машина состояний и Risk Core подменены, чтобы исход зависел только от паузы.
    """
    import runner

    assert trading_halt_reason(state_machine=machine(), risk_core=risk()) is None, (
        "до паузы предохранитель должен пропускать — иначе тест ничего не докажет"
    )
    runner.pause_trading_manually()
    try:
        reason = trading_halt_reason(state_machine=machine(), risk_core=risk())
        assert reason and "вручную" in reason, (
            f"после /pause предохранитель обязан блокировать, получено: {reason!r}"
        )
    finally:
        runner.resume_trading_manually()

    assert trading_halt_reason(state_machine=machine(), risk_core=risk()) is None, (
        "после /resume блокировка должна сниматься"
    )
