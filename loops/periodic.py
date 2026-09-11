"""
Периодические фоновые задачи бота: группы корреляции, суточный отчёт, трекер
исходов сигналов.

Вынесены из runner.py без изменения логики (пункт 5 плана отложенного,
docs/DEFERRED_PLAN.md). Состояние процесса передаётся параметрами — функция
«бот ещё работает» и событие остановки, — а не берётся из runner: иначе этот
модуль и runner импортировали бы друг друга.
"""
import asyncio
import logging
from datetime import datetime, UTC, timedelta
from typing import Callable

logger = logging.getLogger(__name__)

IsRunning = Callable[[], bool]


async def correlation_groups_loop(is_running: IsRunning, shutdown_evt: asyncio.Event):
    """
    Группы коррелирующих символов для Risk Core (market_data.correlation_groups).
    Раз в час проверяет, не пора ли пересчитать (групп нет или они старше суток),
    и считает в потоке. Сбой не критичен: остаются прежние группы.
    """
    from market_data import correlation_groups as cg
    logger.info("Correlation groups loop started")
    while is_running() and not shutdown_evt.is_set():
        try:
            if await asyncio.to_thread(cg.needs_refresh):
                await asyncio.wait_for(asyncio.to_thread(cg.refresh), timeout=300.0)
        except asyncio.CancelledError:
            logger.info("Correlation groups loop cancelled")
            break
        except Exception as e:
            logger.warning("Correlation groups refresh failed (non-critical): %s: %s", type(e).__name__, e)
        remaining = 3600.0
        while remaining > 0 and is_running() and not shutdown_evt.is_set():
            try:
                await asyncio.sleep(min(60.0, remaining))
                remaining -= 60.0
            except asyncio.CancelledError:
                logger.info("Correlation groups loop cancelled")
                return
    logger.info("Correlation groups loop stopped")


async def daily_report_loop(is_running: IsRunning, shutdown_evt: asyncio.Event):
    """
    Отправляет ежедневный отчёт в 00:00 UTC.

    AsyncIO safety:
    - Длинные sleep с проверкой shutdown
    - Graceful cancellation support
    """
    from daily_report import generate_daily_report
    logger.info("Daily report loop started")

    while is_running() and not shutdown_evt.is_set():
        try:
            # Вычисляем время до следующего отчета (00:00 UTC)
            now = datetime.now(UTC)
            next_report = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            sleep_seconds = (next_report - now).total_seconds()

            logger.info("Next daily report in %.1f hours", sleep_seconds / 3600)

            # Sleep с проверкой shutdown (разбиваем на чанки для responsiveness)
            sleep_chunk = min(3600.0, sleep_seconds)  # Максимум 1 час за раз
            remaining = sleep_seconds

            while remaining > 0 and is_running() and not shutdown_evt.is_set():
                try:
                    chunk = min(sleep_chunk, remaining)
                    await asyncio.sleep(chunk)
                    remaining -= chunk
                except asyncio.CancelledError:
                    break

            if shutdown_evt.is_set() or not is_running():
                break

            # Отправляем отчет
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(generate_daily_report),
                    timeout=60.0
                )
                logger.info("Daily report sent")
            except Exception as e:
                logger.warning("Failed to send daily report (non-critical): %s: %s", type(e).__name__, e)

        except asyncio.CancelledError:
            logger.info("Daily report loop cancelled")
            break
        except Exception as e:
            logger.error("Error in daily report loop: %s: %s", type(e).__name__, e)
            # Пауза 1 час перед повтором (с проверкой shutdown каждую секунду)
            try:
                remaining = 3600
                while remaining > 0:
                    if shutdown_evt.is_set() or not is_running():
                        break
                    await asyncio.sleep(min(1.0, remaining))
                    remaining -= 1.0
            except asyncio.CancelledError:
                break

    logger.info("Daily report loop stopped")


async def outcome_tracker_loop(is_running: IsRunning, shutdown_evt: asyncio.Event):
    """
    Периодически маркирует сигналы результатами (WIN/LOSS/NEUTRAL).

    Запускается через 5 минут после старта (дать боту время),
    затем повторяется каждые 30 минут.
    """
    logger.info("[OutcomeTracker] Loop started")

    # Initial delay: 5 minutes
    try:
        await asyncio.wait_for(shutdown_evt.wait(), timeout=300.0)
        logger.info("[OutcomeTracker] Loop stopped (shutdown during initial delay)")
        return
    except asyncio.TimeoutError:
        pass  # Expected: initial delay elapsed, proceed to outcome check loop

    while is_running() and not shutdown_evt.is_set():
        try:
            from brains.outcome_tracker import run_outcome_check
            count = await asyncio.to_thread(run_outcome_check)
            if count > 0:
                logger.info("[OutcomeTracker] Newly marked outcomes: %d", count)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("[OutcomeTracker] Error: %s", e, exc_info=True)

        try:
            await asyncio.wait_for(shutdown_evt.wait(), timeout=1800.0)  # 30 min
            break
        except asyncio.TimeoutError:
            pass  # Expected: sleep interval elapsed, proceed to next outcome check

    logger.info("[OutcomeTracker] Loop stopped")
