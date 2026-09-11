"""
Сторожа процесса бота: ThreadWatchdog (поток вне asyncio: зависание цикла
событий, TTL защитного режима), FatalReaper (FATAL ⇒ процесс обязан выйти),
метки сердцебиения для них и RuntimeState — где лежат запущенные сторожа.

Вынесены из runner.py без изменения логики (пункт 5 плана отложенного,
docs/DEFERRED_PLAN.md, шаг 9). Из runner им нужен только system_state (метки
сердцебиения) — runner передаёт его один раз через configure(). runner держит
прежние имена: ими пользуются main(), восстановление из SAFE_MODE и тесты.
"""
import logging
import os
import threading
import time
import uuid
from datetime import datetime, UTC
from enum import Enum
from system_state_machine import SystemState as SystemStateEnum
from typing import Optional

logger = logging.getLogger(__name__)

FATAL_EXIT_CODE = 10  # Exit code для FATAL состояния (systemd restart)


def _hard_exit(code: int) -> None:
    """
    Немедленное завершение процесса, минуя cleanup и atexit.

    Единственная точка, где вызывается os._exit из сторожевых потоков, и точка
    подмены для тестов: инвариант «FATAL ⇒ процесс обязан выйти» нужно уметь
    проверить, не убивая при этом pytest (см. FatalReaper).
    """
    os._exit(code)


# ========== THREAD WATCHDOG CONSTANTS ==========
THREAD_WATCHDOG_INTERVAL = 5.0  # Проверка каждые 5 секунд
THREAD_WATCHDOG_HEARTBEAT_TIMEOUT = 30.0  # 30 секунд без heartbeat → LOOP_STALL

# ========== THREAD-SAFE HEARTBEAT ACCESS ==========
_heartbeat_lock = threading.Lock()  # Lock для thread-safe доступа к last_heartbeat


# Передаёт runner через configure(): метки сердцебиения пишутся в его SystemState
system_state = None


def configure(*, system_state):
    """Экземпляр SystemState процесса (runner.system_state)."""
    globals()["system_state"] = system_state


# ========== THREAD-SAFE HEARTBEAT ACCESS ==========
def get_last_heartbeat_timestamp() -> Optional[float]:
    """
    Thread-safe чтение last_heartbeat timestamp.
    
    Используется ThreadWatchdog для проверки состояния event loop
    из отдельного потока (вне asyncio).
    
    Returns:
        Optional[float]: Unix timestamp последнего heartbeat или None
    """
    with _heartbeat_lock:
        if system_state.system_health.last_heartbeat:
            return system_state.system_health.last_heartbeat.timestamp()
        return None


def update_heartbeat_thread_safe(when=None):
    """
    Thread-safe обновление heartbeat.

    Вызывается из asyncio heartbeat loop для обновления timestamp,
    который читается ThreadWatchdog.

    when — явная отметка времени (datetime с tz). Нужна тестам, чтобы
    воспроизвести зависший loop, не подменяя приватные поля.
    """
    with _heartbeat_lock:
        system_state.update_heartbeat(when)


# ========== THREAD-BASED WATCHDOG ==========
class ThreadWatchdogState(Enum):
    """
    HARDENING: Явные состояния lifecycle для ThreadWatchdog.
    
    INIT → ARMED → TRIGGERED → STOPPED
    """
    INIT = "INIT"  # Создан, но не запущен
    ARMED = "ARMED"  # Запущен, ждёт первого heartbeat и event loop
    TRIGGERED = "TRIGGERED"  # Обнаружил LOOP_STALL, отправил событие
    STOPPED = "STOPPED"  # Остановлен


class ThreadWatchdog:
    """
    HARDENING: Thread-based watchdog для детектирования блокировки event loop.
    
    SINGLE-WRITER PRINCIPLE: ThreadWatchdog НЕ МУТИРУЕТ состояние напрямую.
    Вместо этого отправляет события в state machine через thread-safe очередь.
    
    КРИТИЧНО: Работает в отдельном threading.Thread (daemon=True),
    НЕ использует asyncio, await, loop, tasks.
    
    WHY: Event loop НЕ МОЖЕТ детектировать собственную смерть.
    Если event loop заблокирован (например, CPU-bound chaos удерживает GIL),
    все asyncio задачи тоже заблокированы, и watchdog внутри asyncio не сработает.
    
    ThreadWatchdog работает ВНЕ asyncio и гарантированно обнаружит блокировку.
    
    HARDENING INVARIANTS:
    - ThreadWatchdog НЕ проверяет TTL (это делает state machine)
    - ThreadWatchdog НЕ вызывает os._exit (это делает state machine при FATAL)
    - ThreadWatchdog НЕ мутирует SystemState напрямую
    - Все переходы состояния происходят через state_machine.trigger_loop_stall_thread_safe()
    - ThreadWatchdog НЕ работает после FATAL (проверяет should_exit_fatal())
    - ThreadWatchdog НЕ триггерит повторно (idempotent через lifecycle state)
    """
    
    def __init__(self, state_machine_instance, heartbeat_timeout: float = THREAD_WATCHDOG_HEARTBEAT_TIMEOUT,
                 exit_fn=None, check_interval: float = THREAD_WATCHDOG_INTERVAL):
        """
        HARDENING: Принимает state machine, не system_state.
        ThreadWatchdog работает только с state machine для thread-safe переходов.

        exit_fn — подменяемая функция выхода, см. пояснение у FatalReaper.
        check_interval — период опроса. Вынесен из константы в параметр, чтобы
        тест на истечение TTL не ждал полный боевой цикл в 5 секунд: цикл
        начинается с ожидания, поэтому первая проверка происходит не раньше него.
        """
        self.state_machine = state_machine_instance
        self.heartbeat_timeout = heartbeat_timeout
        self.check_interval = check_interval
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self._exit_fn = exit_fn or _hard_exit
        self.triggered = False  # Idempotent: предотвращает повторные срабатывания
        self.trigger_lock = threading.Lock()
        
        # HARDENING: Явный lifecycle state
        self.lifecycle_state = ThreadWatchdogState.INIT
        self.lifecycle_lock = threading.Lock()
        self.first_heartbeat_received = False
        self.event_loop_set = False
    
    def start(self):
        """
        HARDENING: Запускает watchdog в отдельном потоке.
        
        Lifecycle: INIT → ARMED (после первого heartbeat и event loop)
        """
        with self.lifecycle_lock:
            if self.lifecycle_state != ThreadWatchdogState.INIT:
                logger.warning("ThreadWatchdog already started (state: %s)", self.lifecycle_state.value)
                return
            
            if self.thread is not None and self.thread.is_alive():
                logger.warning("ThreadWatchdog thread already running")
                return
        
        self.stop_event.clear()
        self.triggered = False
        self.first_heartbeat_received = False
        self.event_loop_set = False
        
        self.thread = threading.Thread(
            target=self._watchdog_loop,
            name="ThreadWatchdog",
            daemon=True
        )
        self.thread.start()
        
        logger.critical(
            "THREAD_WATCHDOG_STARTED heartbeat_timeout=%ss check_interval=%ss lifecycle_state=%s",
            self.heartbeat_timeout, THREAD_WATCHDOG_INTERVAL, ThreadWatchdogState.INIT.value
        )
    
    def arm(self):
        """
        HARDENING: Переводит watchdog в ARMED состояние.
        
        Вызывается после:
        - первого heartbeat
        - установки event loop в state machine
        
        ARMED означает, что watchdog готов к детектированию LOOP_STALL.
        """
        with self.lifecycle_lock:
            if self.lifecycle_state == ThreadWatchdogState.INIT:
                if self.first_heartbeat_received and self.event_loop_set:
                    self.lifecycle_state = ThreadWatchdogState.ARMED
                    logger.critical(
                        "THREAD_WATCHDOG_ARMED: first_heartbeat=%s event_loop_set=%s",
                        self.first_heartbeat_received, self.event_loop_set
                    )
                else:
                    logger.debug(
                        "THREAD_WATCHDOG_NOT_READY: first_heartbeat=%s event_loop_set=%s",
                        self.first_heartbeat_received, self.event_loop_set
                    )
    
    def stop(self, timeout: float = 5.0):
        """
        HARDENING: Останавливает watchdog.
        
        Lifecycle: любое состояние → STOPPED
        """
        with self.lifecycle_lock:
            if self.lifecycle_state == ThreadWatchdogState.STOPPED:
                return
            self.lifecycle_state = ThreadWatchdogState.STOPPED
        
        if self.thread is None or not self.thread.is_alive():
            return
        
        logger.info("Stopping ThreadWatchdog...")
        self.stop_event.set()
        
        if self.thread.is_alive():
            self.thread.join(timeout=timeout)
            if self.thread.is_alive():
                logger.warning("ThreadWatchdog did not stop within timeout")
            else:
                logger.info("ThreadWatchdog stopped")
    
    def _watchdog_loop(self):
        """
        HARDENING: Основной цикл watchdog (выполняется в отдельном потоке).
        
        SINGLE-WRITER: НЕ мутирует состояние напрямую.
        Только читает heartbeat timestamp и отправляет события в state machine.
        
        НЕ использует asyncio, await, loop, tasks.
        Только threading, time.
        
        Lifecycle: INIT → ARMED → TRIGGERED → STOPPED
        """
        while not self.stop_event.is_set():
            try:
                # Проверяем каждые N секунд
                if self.stop_event.wait(self.check_interval):
                    # stop_event установлен - выходим
                    break
                
                # HARDENING: Проверяем состояние через state machine (thread-safe чтение)
                # Если уже в FATAL, watchdog ОБЯЗАН остановиться
                if self.state_machine.should_exit_fatal():
                    logger.info("THREAD_WATCHDOG: System in FATAL state, exiting (invariant: no work after FATAL)")
                    with self.lifecycle_lock:
                        self.lifecycle_state = ThreadWatchdogState.STOPPED
                    break
                
                # HARDENING: SAFE_MODE TTL - DUPLICATE ENFORCEMENT В THREAD
                # Дублируем TTL-логику: если now - entered_at > SAFE_MODE_TTL → os._exit
                # НЕ ждём asyncio, TTL не должен зависеть от event loop
                current_state = self.state_machine.state
                if current_state == SystemStateEnum.SAFE_MODE:
                    safe_mode_entered_at = self.state_machine.get_safe_mode_entered_at()
                    safe_mode_ttl = self.state_machine.get_safe_mode_ttl()
                    
                    if safe_mode_entered_at is not None:
                        duration = (datetime.now(UTC) - safe_mode_entered_at).total_seconds()
                        
                        if duration >= safe_mode_ttl:
                            logger.critical(
                                "THREAD_WATCHDOG: SAFE_MODE TTL expired - duration=%.1fs >= ttl=%ss, calling exit(%s) (invariant: SAFE_MODE TTL => exit even if asyncio stalled)",
                                duration, safe_mode_ttl, FATAL_EXIT_CODE
                            )
                            # КРИТИЧНО: os._exit напрямую (через _hard_exit), не через asyncio
                            self._exit_fn(FATAL_EXIT_CODE)
                            return
                
                # HARDENING: Проверяем lifecycle state
                with self.lifecycle_lock:
                    if self.lifecycle_state == ThreadWatchdogState.STOPPED:
                        break
                    if self.lifecycle_state == ThreadWatchdogState.TRIGGERED:
                        # Уже сработал - только мониторим FATAL
                        continue
                
                # Thread-safe чтение last_heartbeat timestamp
                last_heartbeat_ts = get_last_heartbeat_timestamp()
                current_time = time.time()
                
                if last_heartbeat_ts is None:
                    # Heartbeat ещё не был обновлён - пропускаем проверку
                    continue
                
                # HARDENING: Отмечаем первый heartbeat
                if not self.first_heartbeat_received:
                    self.first_heartbeat_received = True
                    self.arm()  # Попытка перехода в ARMED
                
                # HARDENING: Проверяем, что мы в ARMED состоянии перед детектированием
                with self.lifecycle_lock:
                    if self.lifecycle_state != ThreadWatchdogState.ARMED:
                        # Ещё не готов - пропускаем проверку
                        continue
                
                time_since_heartbeat = current_time - last_heartbeat_ts
                
                # Проверяем timeout
                if time_since_heartbeat > self.heartbeat_timeout:
                    # HARDENING: LOOP_STALL DETECTED
                    # Отправляем событие в state machine, НЕ мутируем состояние напрямую
                    self._trigger_loop_stall(time_since_heartbeat, last_heartbeat_ts)
                
            except Exception as e:
                # Критическая ошибка в watchdog - логируем, но продолжаем
                logger.error(
                    "THREAD_WATCHDOG_ERROR: %s: %s", type(e).__name__, e,
                    exc_info=True
                )
                # Небольшая задержка перед следующей проверкой
                time.sleep(1.0)
        
        logger.info("ThreadWatchdog loop exited")
    
    def _trigger_loop_stall(self, time_since_heartbeat: float, last_heartbeat_ts: float):
        """
        HARDENING: Триггерит LOOP_STALL через state machine.
        
        SINGLE-WRITER PRINCIPLE: НЕ мутирует состояние напрямую.
        Отправляет событие в state machine, которое обрабатывается в asyncio loop.
        
        Thread-safe, idempotent (не срабатывает повторно).
        Lifecycle: ARMED → TRIGGERED
        """
        with self.trigger_lock:
            # HARDENING: Проверяем lifecycle state для idempotency
            with self.lifecycle_lock:
                if self.lifecycle_state == ThreadWatchdogState.TRIGGERED:
                    # Уже сработал - idempotent
                    return
                if self.lifecycle_state != ThreadWatchdogState.ARMED:
                    logger.warning(
                        "THREAD_WATCHDOG: Cannot trigger in state %s, must be ARMED",
                        self.lifecycle_state.value
                    )
                    return
                
                self.lifecycle_state = ThreadWatchdogState.TRIGGERED
            
            if self.triggered:
                # Дополнительная проверка для thread-safety
                return
            
            self.triggered = True
        
        # Генерируем incident_id
        incident_id = f"thread-watchdog-{uuid.uuid4().hex[:8]}"
        
        logger.critical(
            "THREAD_WATCHDOG_TRIGGERED time_since_heartbeat=%.1fs heartbeat_timeout=%ss last_heartbeat_ts=%s incident_id=%s",
            time_since_heartbeat, self.heartbeat_timeout, last_heartbeat_ts, incident_id
        )
        
        # HARDENING: Проверяем состояние через state machine (thread-safe чтение)
        # Если уже в SAFE_MODE или FATAL, не отправляем событие повторно
        current_state = self.state_machine.state
        if current_state == SystemStateEnum.SAFE_MODE:
            # HARDENING: TTL проверяется в state machine, не здесь
            logger.debug("THREAD_WATCHDOG: Already in SAFE_MODE, TTL check handled by state machine")
            return
        
        if current_state == SystemStateEnum.FATAL:
            logger.debug("THREAD_WATCHDOG: System in FATAL state, skipping trigger")
            return
        
        # HARDENING: Отправляем событие в state machine через thread-safe метод
        # State machine обработает переход в SAFE_MODE в asyncio loop
        success = self.state_machine.trigger_loop_stall_thread_safe(
            time_since_heartbeat=time_since_heartbeat,
            incident_id=incident_id
        )
        
        if success:
            logger.critical(
                "THREAD_WATCHDOG_EVENT_SENT: LOOP_STALL event queued for state machine incident_id=%s",
                incident_id
            )
        else:
            logger.error(
                "THREAD_WATCHDOG_EVENT_FAILED: Failed to queue LOOP_STALL event incident_id=%s",
                incident_id
            )


# ========== RUNTIME STATE (явное состояние вместо global) ==========
class RuntimeState:
    """
    Явное состояние runtime для watchdog и reaper.
    Устраняет необходимость в global declarations.
    """
    def __init__(self):
        self.thread_watchdog: Optional[ThreadWatchdog] = None
        self.fatal_reaper: Optional['FatalReaper'] = None
    
    def get_thread_watchdog(self) -> Optional[ThreadWatchdog]:
        """Возвращает экземпляр ThreadWatchdog"""
        return self.thread_watchdog
    
    def set_thread_watchdog(self, watchdog: Optional[ThreadWatchdog]):
        """Устанавливает экземпляр ThreadWatchdog"""
        self.thread_watchdog = watchdog
    
    def get_fatal_reaper(self) -> Optional['FatalReaper']:
        """Возвращает экземпляр FatalReaper"""
        return self.fatal_reaper
    
    def set_fatal_reaper(self, reaper: Optional['FatalReaper']):
        """Устанавливает экземпляр FatalReaper"""
        self.fatal_reaper = reaper


# Глобальный экземпляр RuntimeState (единственный global)
_runtime_state = RuntimeState()


def get_thread_watchdog() -> Optional[ThreadWatchdog]:
    """Возвращает глобальный экземпляр ThreadWatchdog"""
    return _runtime_state.get_thread_watchdog()


class FatalReaper:
    """
    HARDENING: Thread-level FATAL REAPER.

    Отдельный daemon thread, который:
    - НЕ использует asyncio
    - Раз в 1-2 секунды проверяет state_machine.state == FATAL
    - Если FATAL → вызывает exit_fn(FATAL_EXIT_CODE)

    Это последний рубеж - убивает процесс даже если asyncio умер.

    exit_fn существует ради тестируемости. Раньше здесь стоял голый os._exit, и
    проверить инвариант «FATAL ⇒ процесс обязан выйти» было нечем: тест, который
    его дёргал, убивал сам pytest — прогон обрывался на середине без сводки, с
    кодом 10 и без единой строки о том, что вообще произошло. Единственной защитой
    был skipif(CI == "true"), то есть в CI инвариант не проверялся вовсе, а локально
    ломал прогон всех остальных тестов. Подменяемая функция выхода даёт проверить
    ЧТО вызвано и С КАКИМ кодом, не завершая процесс.
    """

    def __init__(self, state_machine_instance, check_interval: float = 1.5, exit_fn=None):
        self.state_machine = state_machine_instance
        self.check_interval = check_interval
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self._exit_fn = exit_fn or _hard_exit
    
    def start(self):
        """Запускает FATAL_REAPER в отдельном daemon thread"""
        if self.thread is not None and self.thread.is_alive():
            logger.warning("FATAL_REAPER already running")
            return
        
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._reaper_loop,
            name="FATAL_REAPER",
            daemon=True
        )
        self.thread.start()
        logger.critical("FATAL_REAPER_STARTED check_interval=%ss", self.check_interval)
    
    def stop(self):
        """Останавливает FATAL_REAPER"""
        if self.thread is None or not self.thread.is_alive():
            return
        
        logger.info("Stopping FATAL_REAPER...")
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2.0)
            if self.thread.is_alive():
                logger.warning("FATAL_REAPER did not stop within timeout")
            else:
                logger.info("FATAL_REAPER stopped")
    
    def _reaper_loop(self):
        """
        HARDENING: Основной цикл reaper.
        
        НЕ использует asyncio, await, loop, tasks.
        Только threading, time, os.
        """
        logger.critical("FATAL_REAPER: Loop started")
        
        while not self.stop_event.is_set():
            try:
                # Проверяем каждые N секунд
                if self.stop_event.wait(self.check_interval):
                    # stop_event установлен - выходим
                    break
                
                # HARDENING: Thread-safe чтение состояния
                current_state = self.state_machine.state
                
                if current_state == SystemStateEnum.FATAL:
                    logger.critical(
                        "FATAL_REAPER: FATAL state detected - calling exit(%s) (invariant: FATAL => process MUST exit)",
                        FATAL_EXIT_CODE
                    )
                    # КРИТИЧНО: os._exit (через _hard_exit), не sys.exit —
                    # убивает процесс немедленно, не вызывая cleanup, что
                    # гарантирует выход даже если asyncio мёртв.
                    self._exit_fn(FATAL_EXIT_CODE)
                    return
                
            except Exception as e:
                # Критическая ошибка в reaper - логируем, но продолжаем
                logger.error(
                    "FATAL_REAPER_ERROR: %s: %s", type(e).__name__, e,
                    exc_info=True
                )
                # Небольшая задержка перед следующей проверкой
                time.sleep(1.0)
        
        logger.info("FATAL_REAPER: Loop exited")
