"""
Периодические задачи, вынесенные из runner.py в loops/periodic.py (пункт 5 плана
отложенного). Проверяется то, что изменилось при переносе: состояние процесса
приходит параметрами, и задачи по-прежнему останавливаются по событию и по
флагу «бот работает», не делая лишнего.
"""
import asyncio
import pathlib

import pytest

from loops import periodic

ROOT = pathlib.Path(__file__).resolve().parent.parent


def run(coro, timeout=5.0):
    return asyncio.run(asyncio.wait_for(coro, timeout))


def test_correlation_groups_refresh_once_then_stop_on_shutdown(monkeypatch):
    from market_data import correlation_groups as cg

    async def scenario():
        shutdown = asyncio.Event()
        calls = []

        def refresh():
            calls.append("refresh")
            shutdown.set()

        monkeypatch.setattr(cg, "needs_refresh", lambda: True)
        monkeypatch.setattr(cg, "refresh", refresh)
        await periodic.correlation_groups_loop(lambda: True, shutdown)
        return calls

    assert run(scenario()) == ["refresh"]


def test_correlation_groups_do_nothing_when_the_bot_is_stopping(monkeypatch):
    from market_data import correlation_groups as cg
    monkeypatch.setattr(cg, "needs_refresh", lambda: pytest.fail("бот остановлен — пересчёта быть не должно"))
    run(periodic.correlation_groups_loop(lambda: False, asyncio.Event()))


def test_daily_report_is_not_sent_when_shutdown_is_already_set(monkeypatch):
    import daily_report
    monkeypatch.setattr(daily_report, "generate_daily_report", lambda: pytest.fail("отчёт при остановке"))

    async def scenario():
        shutdown = asyncio.Event()
        shutdown.set()
        await periodic.daily_report_loop(lambda: True, shutdown)

    run(scenario())


def test_outcome_tracker_stops_during_the_initial_delay(monkeypatch):
    import brains.outcome_tracker as tracker
    monkeypatch.setattr(tracker, "run_outcome_check", lambda: pytest.fail("проверка исходов при остановке"))

    async def scenario():
        shutdown = asyncio.Event()
        shutdown.set()
        await periodic.outcome_tracker_loop(lambda: True, shutdown)

    run(scenario())


def test_runner_registers_the_moved_tasks_with_its_state():
    text = (ROOT / "runner.py").read_text(encoding="utf-8")
    for loop, name in (("daily_report_loop", "DailyReport"), ("correlation_groups_loop", "CorrelationGroups"),
                       ("outcome_tracker_loop", "OutcomeTracker")):
        assert f'periodic.{loop}(_is_running, get_shutdown_event()), name="{name}"' in text.replace("\n", "").replace(
            "            ", ""), f"{name} не зарегистрирован через loops.periodic"
        assert f"async def {loop}(" not in text, f"{loop} остался и в runner.py — две копии"
