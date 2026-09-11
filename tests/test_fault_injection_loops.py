"""
Инструменты внедрения сбоев, вынесенные из runner.py в loops/fault_injection.py
(пункт 5 плана отложенного, шаг 4). Гейткипер и автомат состояний — подменные,
интервалы укорочены параметрами.
"""
import asyncio
import pathlib
from types import SimpleNamespace

import pytest

from loops import fault_injection

ROOT = pathlib.Path(__file__).resolve().parent.parent


def run(coro, timeout=5.0):
    return asyncio.run(asyncio.wait_for(coro, timeout))


def make_state(consecutive_errors=0):
    errors = []
    state = SimpleNamespace(system_health=SimpleNamespace(is_running=True, consecutive_errors=consecutive_errors),
                            record_error=errors.append)
    return state, errors


class FakeGatekeeper:
    meta_decision_brain = None
    position_sizer = None

    def __init__(self, should_i_trade):
        self.decision_core = SimpleNamespace(should_i_trade=should_i_trade)

    def _check_portfolio(self, snapshot):
        return None


class FakeMachine:
    def __init__(self):
        self.is_safe_mode = False
        self.transitions = []

    async def transition_to(self, target, **kwargs):
        self.transitions.append(target)
        self.is_safe_mode = True


def test_disabled_tools_return_at_once():
    state, _ = make_state()
    run(fault_injection.synthetic_decision_tick_loop(lambda: state, asyncio.Event(), enabled=False,
                                                     interval=10.0, max_consecutive_errors=5))
    run(fault_injection.loop_stall_injection_task(lambda: state, asyncio.Event(), enabled=False, stall_seconds=120.0))


def test_synthetic_tick_runs_the_decision_path(monkeypatch):
    import execution.gatekeeper as gatekeeper_module

    async def scenario():
        shutdown = asyncio.Event()
        asked = []

        def should_i_trade(symbol, system_state):
            asked.append(symbol)
            shutdown.set()
            return SimpleNamespace(can_trade=True, reason="ok")

        monkeypatch.setattr(gatekeeper_module, "get_gatekeeper", lambda: FakeGatekeeper(should_i_trade))
        state, errors = make_state()
        await fault_injection.synthetic_decision_tick_loop(lambda: state, shutdown, enabled=True,
                                                           interval=0.01, max_consecutive_errors=5)
        return asked, errors

    asked, errors = run(scenario())
    assert asked == ["BTCUSDT"] and errors == []


def test_injected_decision_fault_is_recorded_and_trips_safe_mode(monkeypatch):
    import execution.gatekeeper as gatekeeper_module
    machine = FakeMachine()
    monkeypatch.setattr(fault_injection, "get_state_machine", lambda: machine)

    async def scenario():
        shutdown = asyncio.Event()

        def should_i_trade(symbol, system_state):
            shutdown.set()
            raise RuntimeError("FAULT_INJECTION: decision_exception - test")

        monkeypatch.setattr(gatekeeper_module, "get_gatekeeper", lambda: FakeGatekeeper(should_i_trade))
        state, errors = make_state(consecutive_errors=5)
        await fault_injection.synthetic_decision_tick_loop(lambda: state, shutdown, enabled=True,
                                                           interval=0.01, max_consecutive_errors=5)
        return errors

    errors = run(scenario())
    assert errors == ["FAULT_INJECTION: decision_exception (synthetic tick)"]
    assert machine.transitions == [fault_injection.SystemStateEnum.SAFE_MODE]


def test_loop_stall_injection_finishes():
    state, _ = make_state()
    run(fault_injection.loop_stall_injection_task(lambda: state, asyncio.Event(), enabled=True,
                                                  stall_seconds=0.3, startup_delay=0.01))


def test_runner_registers_the_tools_with_its_state():
    text = (ROOT / "runner.py").read_text(encoding="utf-8")
    assert ("fault_injection.synthetic_decision_tick_loop(_state, get_shutdown_event(), "
            "ENABLE_SYNTHETIC_DECISION_TICK, SYNTHETIC_DECISION_TICK_INTERVAL, MAX_CONSECUTIVE_ERRORS)") in text
    assert ("fault_injection.loop_stall_injection_task(_state, get_shutdown_event(), "
            "FAULT_INJECT_LOOP_STALL, LOOP_STALL_DURATION)") in text
    for name in ("synthetic_decision_tick_loop", "loop_stall_injection_task"):
        assert f"async def {name}(" not in text, f"{name} остался и в runner.py — две копии"
