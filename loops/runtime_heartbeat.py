"""
Runtime heartbeat процесса бота: раз в 10 с доказывает, что цикл событий жив,
ставит метки для ThreadWatchdog и healthcheck контейнера, а пропуск ударов
(цикл событий стоял) переводит в защитный режим.

Вынесен из runner.py без изменения логики (пункт 5 плана отложенного,
docs/DEFERRED_PLAN.md, шаг 7). Состояние процесса приходит функцией get_state —
она отдаёт текущий system_state runner'а; событие остановки и отметка для
ThreadWatchdog — параметрами. Интервал и пороги — параметры со значениями по
умолчанию, равными прежним константам; runner передаёт свои явно (часть из них
читается из окружения). Флаг хаоса и счётчики — control_plane.state.
"""
import asyncio
import logging
import time
from typing import Any, Callable

from control_plane import state as cp_state
from system_state_machine import SystemState as SystemStateEnum, get_state_machine
from utils import liveness

logger = logging.getLogger(__name__)

StateGetter = Callable[[], Any]


async def runtime_heartbeat_loop(get_state: StateGetter, shutdown_evt: asyncio.Event,
                                 on_heartbeat: Callable[[], None], *,
                                 interval: float = 10.0, miss_threshold: float = 2.0,
                                 enforcement_threshold: int = 2, fault_inject_loop_stall: bool = False,
                                 max_consecutive_errors: int = 5):
    """
    Runtime heartbeat - доказывает, что процесс жив и event loop не заблокирован.
    Запускается каждые 10 секунд, неблокирующий.
    
    Также обнаруживает пропущенные heartbeats (признак застопорившегося event loop).

    on_heartbeat — отметка для ThreadWatchdog (update_heartbeat_thread_safe в runner);
    интервал и пороги — параметры, runner передаёт свои константы.
    """
    logger.info("💓 Runtime heartbeat started (interval: 10s)")
    
    heartbeat_count = 0
    last_heartbeat_time = time.time()
    
    
    while get_state().system_health.is_running and not shutdown_evt.is_set():
        try:
            # Sleep с проверкой shutdown каждую секунду для быстрого отклика на SIGTERM
            remaining = interval
            while remaining > 0 and not shutdown_evt.is_set() and get_state().system_health.is_running:
                await asyncio.sleep(min(1.0, remaining))
                remaining -= 1.0
            
            # Проверяем shutdown после sleep
            if shutdown_evt.is_set() or not get_state().system_health.is_running:
                break
            
            heartbeat_count += 1
            
            # Обновляем время последнего heartbeat
            current_time = time.time()
            time_since_last = current_time - last_heartbeat_time
            last_heartbeat_time = current_time
            
            # Обновляем SystemState (thread-safe для ThreadWatchdog)
            get_state().update_heartbeat()
            on_heartbeat()  # Обновляем для ThreadWatchdog
            liveness.mark("heartbeat")  # метка для healthcheck контейнера (utils/liveness.py)
            
            # Проверяем, не пропущены ли heartbeats (признак застопорившегося loop)
            # Если прошло больше чем 2 интервала - это stall
            expected_interval = interval
            if time_since_last > expected_interval * miss_threshold:
                # Обнаружен пропуск heartbeats - возможен stall event loop
                missed_heartbeats = int((time_since_last - expected_interval) / expected_interval)
                logger.warning(
                    "HEARTBEAT_MISS detected - time_since_last=%.1fs (expected=%ss) missed_heartbeats=%d",
                    time_since_last, expected_interval, missed_heartbeats
                )
                
                # ========== PROMETHEUS METRICS (NON-BLOCKING) ==========
                # Увеличиваем счетчик scheduler stalls
                cp_state.increment_scheduler_stalls()
                
                # ========== REQUIREMENT 1: HEARTBEAT → ENFORCEMENT ==========
                # HEARTBEAT_MISS НЕ МОЖЕТ БЫТЬ ТОЛЬКО ЛОГОМ
                # После превышения порога missed_heartbeats → SAFE_MODE
                if missed_heartbeats >= enforcement_threshold:
                    # Генерируем incident_id для трейсинга
                    import uuid
                    incident_id = f"heartbeat-miss-{uuid.uuid4().hex[:8]}"
                    
                    # HARDENING: Переход в SAFE_MODE через state machine
                    state_machine = get_state_machine()
                    if not state_machine.is_safe_mode:
                        await state_machine.transition_to(
                            SystemStateEnum.SAFE_MODE,
                            reason=f"HEARTBEAT_ENFORCEMENT: missed_heartbeats={missed_heartbeats} >= threshold={enforcement_threshold}",
                            owner="runtime_heartbeat_loop",
                            metadata={"missed_heartbeats": missed_heartbeats, "incident_id": incident_id}
                        )
                        logger.critical(
                            "HEARTBEAT_ENFORCEMENT: SAFE_MODE activated - missed_heartbeats=%s >= threshold=%s incident_id=%s",
                            missed_heartbeats, enforcement_threshold, incident_id
                        )
                        
                        # Метрика для Prometheus
                        cp_state.prometheus_metrics["heartbeat_enforcement_total"] = \
                            cp_state.prometheus_metrics.get("heartbeat_enforcement_total", 0) + 1
                        
                        # Записываем ошибку для health tracking
                        get_state().record_error(f"HEARTBEAT_MISS_ENFORCEMENT: {incident_id}")
                        
                        # ========== REQUIREMENT 2: CHAOS INVARIANT ==========
                        # Если chaos был активен, фиксируем что переход через SAFE_MODE произошёл
                        if cp_state.chaos["was_active"]:
                            logger.critical(
                                "CHAOS_INVARIANT_SATISFIED: SAFE_MODE entered after chaos - incident_id=%s",
                                incident_id
                            )
                
                # Проверяем, не является ли это fault injection
                if fault_inject_loop_stall:
                    logger.error(
                        "FAULT_INJECTION: loop_stall_detected - Controlled loop stall detected via missed heartbeats. time_since_last=%.1fs missed_heartbeats=%s",
                        time_since_last, missed_heartbeats
                    )
                    # Записываем ошибку для health tracking
                    get_state().record_error("FAULT_INJECTION: loop_stall_detected")
                    
                    # HARDENING: Проверяем safe-mode активацию через state machine
                    state_machine = get_state_machine()
                    if get_state().system_health.consecutive_errors >= max_consecutive_errors:
                        if not state_machine.is_safe_mode:
                            await state_machine.transition_to(
                                SystemStateEnum.SAFE_MODE,
                                reason=f"Loop stall detection: consecutive_errors >= MAX_CONSECUTIVE_ERRORS",
                                owner="runtime_heartbeat_loop",
                                metadata={"consecutive_errors": get_state().system_health.consecutive_errors}
                            )
                            logger.warning(
                                "SAFE-MODE activated after loop stall detection: consecutive_errors=%s >= MAX_CONSECUTIVE_ERRORS=%s",
                                get_state().system_health.consecutive_errors, max_consecutive_errors
                            )
            
            # Логируем heartbeat с метриками
            # Используем asyncio.all_tasks() без get_event_loop() для безопасности
            try:
                pending_tasks = len([t for t in asyncio.all_tasks() if not t.done()])
                loop_running = True  # Если мы здесь, loop точно работает
            except RuntimeError:
                # Если нет активного loop, это не критично
                pending_tasks = 0
                loop_running = False
            
            logger.debug(
                "heartbeat_alive=true count=%d pending_tasks=%d loop_running=%s",
                heartbeat_count, pending_tasks, loop_running
            )
            
        except asyncio.CancelledError:
            logger.info("⏹ Runtime heartbeat cancelled")
            break
        except Exception as e:
            logger.error("Error in runtime heartbeat: %s: %s", type(e).__name__, e)
            # Не падаем - продолжаем heartbeat даже при ошибках
    
    logger.info("💓 Runtime heartbeat stopped (total: %d)", heartbeat_count)
