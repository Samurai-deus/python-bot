"""
Восстановление перевзводит настоящий ThreadWatchdog (10.09.2026).

Функция восстановления читала имя _thread_watchdog, которого в модуле нет:
сторож хранится в _runtime_state. Лишнее объявление global глушило flake8,
а на деле каждое успешное восстановление падало с NameError после перехода в
RUNNING, и сторож потоков оставался в состоянии TRIGGERED — следующее
зависание он бы уже не поймал.
"""
import ast
import asyncio
import inspect
import pathlib
import threading
from types import SimpleNamespace

import runner


def _recovery_function():
    src = pathlib.Path(runner.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.AsyncFunctionDef):
            if "ThreadWatchdog re-armed after recovery" in (ast.get_source_segment(src, node) or ""):
                return getattr(runner, node.name)
    raise AssertionError("не нашёл функцию восстановления")


def test_successful_recovery_rearms_the_thread_watchdog(monkeypatch):
    class StateMachine:
        async def transition_to(self, *args, **kwargs):
            return True

    watchdog = SimpleNamespace(
        lifecycle_lock=threading.Lock(),
        lifecycle_state=runner.ThreadWatchdogState.TRIGGERED,
        triggered=True,
    )
    monkeypatch.setattr(runner, "get_state_machine", lambda: StateMachine())
    monkeypatch.setattr(runner, "get_thread_watchdog", lambda: watchdog)

    fn = _recovery_function()
    kwargs = {
        name: "test"
        for name, param in inspect.signature(fn).parameters.items()
        if param.default is inspect.Parameter.empty
    }
    assert asyncio.run(fn(**kwargs)) is True
    assert watchdog.lifecycle_state == runner.ThreadWatchdogState.ARMED
    assert watchdog.triggered is False
