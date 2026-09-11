"""
Контролёры процесса бота: сторож блокировки цикла событий, TTL защитного режима
и heartbeat в Telegram.

Вынесены из runner.py без изменения логики (пункт 5 плана отложенного,
docs/DEFERRED_PLAN.md, шаг 2). Состояние процесса приходит функцией get_state —
она отдаёт текущий system_state runner'а, так что подхватывается и его замена;
событие остановки и колбэк метки для ThreadWatchdog — параметрами. Таймаут и
интервалы проверок — параметры со значениями по умолчанию, равными прежним.
"""
import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from health_monitor import HEARTBEAT_INTERVAL, send_heartbeat_async
from system_state_machine import SystemState as SystemStateEnum, get_state_machine

logger = logging.getLogger(__name__)

StateGetter = Callable[[], Any]


async def loop_guard_watchdog(get_state: StateGetter, shutdown_evt: asyncio.Event, timeout_seconds: float,
                              check_every: float = 10.0):
    """
    REQUIREMENT 3: LOOP_GUARD_TIMEOUT

    Watchdog для event loop - обнаруживает длительные блокировки.
    После timeout:
    - снимает дамп задач (asyncio.all_tasks)
    - записывает structured task dump в лог
    - инициирует SAFE_MODE
    """
    logger.info("🛡️ Loop guard watchdog started")

    last_heartbeat_check = time.time()

    while get_state().system_health.is_running and not shutdown_evt.is_set():
        try:
            await asyncio.sleep(check_every)

            if shutdown_evt.is_set() or not get_state().system_health.is_running:
                break

            # Проверяем время с последнего heartbeat
            current_time = time.time()
            time_since_last_heartbeat = current_time - last_heartbeat_check

            last_heartbeat = get_state().system_health.last_heartbeat
            if last_heartbeat:
                time_since_heartbeat = current_time - last_heartbeat.timestamp()
            else:
                time_since_heartbeat = time_since_last_heartbeat

            # Если прошло больше timeout_seconds - event loop заблокирован
            if time_since_heartbeat > timeout_seconds:
                import uuid
                incident_id = f"loop-guard-{uuid.uuid4().hex[:8]}"

                logger.critical(
                    "LOOP_GUARD_TIMEOUT: Event loop blocked for %.1fs (threshold=%ss) incident_id=%s",
                    time_since_heartbeat, timeout_seconds, incident_id
                )

                # ========== TASK DUMP ==========
                try:
                    all_tasks = asyncio.all_tasks()
                    task_dump = []
                    for task in all_tasks:
                        task_info = {
                            "name": task.get_name(),
                            "done": task.done(),
                            "cancelled": task.cancelled(),
                        }
                        if task.done():
                            try:
                                task_info["exception"] = str(task.exception())
                            except Exception:
                                logger.debug("Failed to get task exception in loop guard dump", exc_info=True)
                        task_dump.append(task_info)

                    logger.critical(
                        "LOOP_GUARD_TASK_DUMP incident_id=%s total_tasks=%s tasks=%s",
                        incident_id, len(task_dump), task_dump
                    )
                except Exception as e:
                    logger.error("LOOP_GUARD: Failed to dump tasks: %s: %s", type(e).__name__, e)

                # HARDENING: SAFE_MODE ACTIVATION через state machine
                state_machine = get_state_machine()
                if not state_machine.is_safe_mode:
                    await state_machine.transition_to(
                        SystemStateEnum.SAFE_MODE,
                        reason=f"LOOP_GUARD_TIMEOUT: Event loop blocked for {time_since_heartbeat:.1f}s",
                        owner="loop_guard_watchdog",
                        metadata={"time_since_heartbeat": time_since_heartbeat, "incident_id": incident_id}
                    )
                    logger.critical(
                        "LOOP_GUARD_ENFORCEMENT: SAFE_MODE activated - incident_id=%s",
                        incident_id
                    )

                    get_state().record_error(f"LOOP_GUARD_TIMEOUT: {incident_id}")

            last_heartbeat_check = current_time

        except asyncio.CancelledError:
            logger.info("⏹ Loop guard watchdog cancelled")
            break
        except Exception as e:
            logger.error("Error in loop guard watchdog: %s: %s", type(e).__name__, e)

    logger.info("🛡️ Loop guard watchdog stopped")


async def safe_mode_ttl_monitor(get_state: StateGetter, shutdown_evt: asyncio.Event, check_every: float = 30.0):
    """
    HARDENING: Мониторит SAFE_MODE TTL через state machine.

    SINGLE-WRITER: Вся логика TTL находится в state machine.
    Этот монитор только вызывает check_safe_mode_ttl() и обрабатывает FATAL.

    REQUIREMENT 4: SAFE_MODE TTL
    - По истечении TTL: SAFE_MODE → FATAL (через state machine)
    - FATAL обрабатывается централизованным exit handler
    """
    logger.info("⏱️ Safe mode TTL monitor started")

    state_machine = get_state_machine()

    while get_state().system_health.is_running and not shutdown_evt.is_set():
        try:
            await asyncio.sleep(check_every)

            if shutdown_evt.is_set() or not get_state().system_health.is_running:
                break

            # State machine сам выполнит переход SAFE_MODE → FATAL, если TTL истёк
            ttl_expired = await state_machine.check_safe_mode_ttl()

            if ttl_expired:
                # TTL истёк, state machine перешёл в FATAL; os._exit выполнит
                # централизованный exit handler в main() при проверке состояния
                logger.critical("SAFE_MODE_TTL_EXPIRED: State machine transitioned to FATAL")

        except asyncio.CancelledError:
            logger.info("⏹ Safe mode TTL monitor cancelled")
            break
        except Exception as e:
            logger.error("Error in safe mode TTL monitor: %s: %s", type(e).__name__, e)

    logger.info("⏱️ Safe mode TTL monitor stopped")


async def heartbeat_loop(get_state: StateGetter, shutdown_evt: asyncio.Event, on_heartbeat: Callable[[], None],
                         interval: float = HEARTBEAT_INTERVAL,
                         send: Callable[[], Awaitable[Any]] = send_heartbeat_async):
    """
    Отправляет периодические heartbeat сообщения в Telegram.
    Отдельно от runtime heartbeat для доказательства liveness.
    on_heartbeat — отметка для ThreadWatchdog (update_heartbeat_thread_safe в runner).
    """
    logger.info("💓 Telegram heartbeat monitoring started")

    while get_state().system_health.is_running and not shutdown_evt.is_set():
        try:
            # Sleep с проверкой shutdown каждую секунду для быстрого отклика на SIGTERM
            remaining = interval
            while remaining > 0 and not shutdown_evt.is_set() and get_state().system_health.is_running:
                try:
                    await asyncio.sleep(min(1.0, remaining))
                except asyncio.CancelledError:
                    raise  # Пробрасываем для правильного shutdown
                remaining -= 1.0

            # Проверяем shutdown после sleep
            if shutdown_evt.is_set() or not get_state().system_health.is_running:
                break

            try:
                await send()
                get_state().update_heartbeat()
                on_heartbeat()  # Обновляем для ThreadWatchdog
                logger.debug("Telegram heartbeat sent")
            except asyncio.TimeoutError:
                # Timeout при network blackhole - не критично, просто пропускаем heartbeat
                logger.debug("Telegram heartbeat timeout (non-critical) - network may be unreachable")
            except Exception as e:
                # Telegram ошибки не должны останавливать heartbeat
                logger.warning("Telegram heartbeat failed (non-critical): %s: %s", type(e).__name__, e)
        except asyncio.CancelledError:
            logger.info("⏹ Telegram heartbeat cancelled")
            break
        except Exception as e:
            logger.error("Error in Telegram heartbeat loop: %s: %s", type(e).__name__, e)
            # Пауза перед повтором с проверкой shutdown каждую секунду
            remaining = 300
            while remaining > 0 and not shutdown_evt.is_set() and get_state().system_health.is_running:
                try:
                    await asyncio.sleep(min(1.0, remaining))
                except asyncio.CancelledError:
                    raise  # Пробрасываем для правильного shutdown
                remaining -= 1.0
            if shutdown_evt.is_set() or not get_state().system_health.is_running:
                break

    logger.info("💓 Telegram heartbeat stopped")
