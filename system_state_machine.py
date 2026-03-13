"""
System State Machine - Явная state machine для safe_mode

ЦЕЛЬ: Заменить boolean флаг на контролируемую state machine.

STATES:
- RUNNING: Нормальная работа
- DEGRADED: Деградация (ошибки, но не критично)
- SAFE_MODE: Защитный режим (блокирует торговлю)
- RECOVERING: Восстановление (после safe_mode)
- FATAL: Критическая ошибка (требует restart)

TRANSITIONS:
RUNNING -> DEGRADED: consecutive_errors >= WARN_THRESHOLD
DEGRADED -> SAFE_MODE: consecutive_errors >= CRITICAL_THRESHOLD OR loop_stall
SAFE_MODE -> RECOVERING: recovery_cycles >= SUCCESS_CYCLES
RECOVERING -> RUNNING: успешное восстановление
ANY -> FATAL: необратимая ошибка (deadlock, corruption)

OWNERS:
- RUNNING: market_analysis_loop
- DEGRADED: error_handler
- SAFE_MODE: loop_guard OR error_handler
- RECOVERING: recovery_mechanism
- FATAL: critical_error_handler

INVARIANTS:
- FATAL ⇒ process MUST exit (enforced by FATAL_REAPER thread)
- SAFE_MODE TTL ⇒ exit even if asyncio stalled (enforced by ThreadWatchdog)
- ThreadWatchdog never mutates state (only sends events)
- StateMachine is single-writer (all transitions via transition_to)
- No async dependency for death (os._exit from threads)
- Event queue overflow → FATAL (hard guarantee delivery)
- No state transitions after shutdown start
"""
import asyncio
import time
import logging
import uuid
import queue
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime, UTC

logger = logging.getLogger(__name__)


class SystemState(Enum):
    """Состояния системы"""
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    SAFE_MODE = "SAFE_MODE"
    RECOVERING = "RECOVERING"
    FATAL = "FATAL"


@dataclass
class StateTransition:
    """Переход состояния"""
    from_state: SystemState
    to_state: SystemState
    reason: str
    timestamp: datetime
    incident_id: str
    owner: str  # Кто инициировал переход
    metadata: Dict[str, Any] = field(default_factory=dict)


class SystemStateMachine:
    """
    State Machine для управления состоянием системы
    
    ИНВАРИАНТЫ:
    - FATAL не может быть очищен (требует restart)
    - SAFE_MODE не может быть terminal (должен перейти в RECOVERING или FATAL)
    - Переходы логируются с incident_id для корреляции
    - Каждый переход имеет owner (кто инициировал)
    """
    
    # Transition guards
    ALLOWED_TRANSITIONS = {
        SystemState.RUNNING: {SystemState.DEGRADED, SystemState.SAFE_MODE, SystemState.FATAL},
        SystemState.DEGRADED: {SystemState.RUNNING, SystemState.SAFE_MODE, SystemState.FATAL},
        SystemState.SAFE_MODE: {SystemState.RECOVERING, SystemState.FATAL},
        SystemState.RECOVERING: {SystemState.RUNNING, SystemState.SAFE_MODE, SystemState.FATAL},
        SystemState.FATAL: set(),  # FATAL - terminal state
    }
    
    def __init__(self, safe_mode_ttl: float = 600.0):
        self._state = SystemState.RUNNING
        self._state_lock = asyncio.Lock()
        self._transitions: list[StateTransition] = []
        self._state_entered_at: Dict[SystemState, datetime] = {
            SystemState.RUNNING: datetime.now(UTC)
        }
        self._recovery_cycles = 0
        self._consecutive_errors = 0
        
        # TTL для SAFE_MODE (максимальное время в safe_mode перед FATAL)
        # HARDENING: TTL управляется только state machine, не глобальными переменными
        self._safe_mode_ttl = safe_mode_ttl
        self._safe_mode_entered_at: Optional[datetime] = None
        
        # Heartbeat для SAFE_MODE
        self._last_heartbeat: Optional[datetime] = None
        self._heartbeat_interval = 60.0  # 1 минута
        
        # HARDENING: Thread-safe event queue для ThreadWatchdog → asyncio communication
        # ThreadWatchdog НЕ МОЖЕТ напрямую вызывать async методы
        import queue
        # HARDENING: Event queue с maxsize и drop policy
        # Если очередь переполнена N раз подряд → FATAL
        self._event_queue: queue.Queue = queue.Queue(maxsize=10)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._event_queue_drops = 0  # Счётчик отброшенных событий
        self._event_queue_consecutive_drops = 0  # Подряд идущие отбросы
        self._event_queue_max_consecutive_drops = 5  # После 5 подряд → FATAL
        self._shutdown_started = False  # Флаг начала shutdown (запрет transitions)
    
    @property
    def state(self) -> SystemState:
        """Текущее состояние (read-only)"""
        return self._state
    
    @property
    def is_safe_mode(self) -> bool:
        """Проверка safe_mode (для обратной совместимости)"""
        return self._state == SystemState.SAFE_MODE
    
    @property
    def is_fatal(self) -> bool:
        """Проверка FATAL состояния"""
        return self._state == SystemState.FATAL
    
    @property
    def trading_paused(self) -> bool:
        """
        HARDENING: trading_paused как derived property из state machine.
        
        ИНВАРИАНТ: SAFE_MODE ⇒ trading_paused == True
        trading_paused == True если:
        - SAFE_MODE активен
        - FATAL активен
        
        NOTE: manual_pause обрабатывается отдельно через control_plane_state
        и учитывается в sync_to_system_state()
        """
        # HARDENING: SAFE_MODE и FATAL всегда блокируют торговлю
        if self._state == SystemState.SAFE_MODE or self._state == SystemState.FATAL:
            return True
        
        return False
    
    def get_trading_paused(self, manual_pause_active: bool = False) -> bool:
        """
        HARDENING: Получает trading_paused с учётом manual_pause.
        
        Args:
            manual_pause_active: Флаг manual pause из control plane
        
        Returns:
            True если торговля приостановлена (SAFE_MODE, FATAL, или manual_pause)
        """
        # HARDENING: Инвариант SAFE_MODE ⇒ trading_paused
        if self.trading_paused:
            return True
        
        # Manual pause также блокирует торговлю
        return manual_pause_active
    
    def sync_to_system_state(self, system_state_instance, manual_pause_active: bool = False) -> None:
        """
        HARDENING: Синхронизирует state machine состояние с system_state.system_health.
        
        Вызывается после каждого перехода состояния для поддержания consistency.
        Это ЕДИНСТВЕННОЕ место где system_state.system_health.safe_mode и trading_paused мутируются.
        
        Args:
            system_state_instance: Экземпляр SystemState для синхронизации
            manual_pause_active: Флаг manual pause из control plane
        """
        # HARDENING: Инвариант SAFE_MODE ⇒ trading_paused
        system_state_instance.system_health.safe_mode = self.is_safe_mode
        
        # HARDENING: trading_paused учитывает и state machine, и manual_pause
        system_state_instance.system_health.trading_paused = self.get_trading_paused(manual_pause_active)
        
        # HARDENING: Assertion для проверки инварианта
        if self.is_safe_mode:
            assert system_state_instance.system_health.trading_paused, \
                "INVARIANT VIOLATION: SAFE_MODE must imply trading_paused == True"
    
    @property
    def recovery_cycles(self) -> int:
        """Количество циклов восстановления"""
        return self._recovery_cycles
    
    @property
    def consecutive_errors(self) -> int:
        """Количество последовательных ошибок"""
        return self._consecutive_errors
    
    async def transition_to(
        self,
        new_state: SystemState,
        reason: str,
        owner: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Переход в новое состояние
        
        Args:
            new_state: Новое состояние
            reason: Причина перехода
            owner: Кто инициировал переход (task name, function name)
            metadata: Дополнительные данные
        
        Returns:
            True если переход выполнен, False если запрещён
        """
        # HARDENING: Запрет transitions после shutdown
        if self._shutdown_started:
            logger.warning(
                f"STATE_MACHINE: Transition blocked - shutdown started "
                f"(attempted: {self._state.value} → {new_state.value}, owner={owner})"
            )
            return False
        
        async with self._state_lock:
            old_state = self._state
            
            # Проверка allowed transitions
            if new_state not in self.ALLOWED_TRANSITIONS[old_state]:
                logger.warning(
                    f"STATE_TRANSITION_DENIED "
                    f"from={old_state.value} to={new_state.value} "
                    f"reason={reason} owner={owner}"
                )
                return False
            
            # FATAL не может быть изменён
            if old_state == SystemState.FATAL:
                logger.critical(
                    f"STATE_TRANSITION_BLOCKED "
                    f"from=FATAL to={new_state.value} "
                    f"reason=FATAL is terminal state"
                )
                return False
            
            # Создаём incident_id для корреляции
            incident_id = f"state-{uuid.uuid4().hex[:8]}"
            
            # Выполняем переход
            self._state = new_state
            self._state_entered_at[new_state] = datetime.now(UTC)
            
            # Специальная обработка для SAFE_MODE
            if new_state == SystemState.SAFE_MODE:
                self._safe_mode_entered_at = datetime.now(UTC)
                self._recovery_cycles = 0  # Сбрасываем recovery cycles
            elif old_state == SystemState.SAFE_MODE:
                self._safe_mode_entered_at = None
            
            # Специальная обработка для RECOVERING
            if new_state == SystemState.RECOVERING:
                self._recovery_cycles = 0  # Начинаем подсчёт заново
            
            # Создаём transition record
            transition = StateTransition(
                from_state=old_state,
                to_state=new_state,
                reason=reason,
                timestamp=datetime.now(UTC),
                incident_id=incident_id,
                owner=owner,
                metadata=metadata or {}
            )
            self._transitions.append(transition)
            
            # Логируем переход
            duration = None
            if old_state in self._state_entered_at:
                duration = (datetime.now(UTC) - self._state_entered_at[old_state]).total_seconds()
            
            logger.critical(
                f"STATE_TRANSITION "
                f"incident_id={incident_id} "
                f"from={old_state.value} to={new_state.value} "
                f"reason={reason} owner={owner} "
                f"duration_in_old_state={duration} "
                f"metadata={metadata}"
            )
            
            return True
    
    async def record_error(self, error_msg: str) -> None:
        """Запись ошибки (увеличивает consecutive_errors)"""
        async with self._state_lock:
            self._consecutive_errors += 1
            
            # Автоматические переходы на основе ошибок
            if self._consecutive_errors >= 5 and self._state != SystemState.SAFE_MODE:
                await self.transition_to(
                    SystemState.SAFE_MODE,
                    f"consecutive_errors >= 5 (current: {self._consecutive_errors})",
                    owner="error_handler",
                    metadata={"error_count": self._consecutive_errors, "last_error": error_msg}
                )
            elif self._consecutive_errors >= 3 and self._state == SystemState.RUNNING:
                await self.transition_to(
                    SystemState.DEGRADED,
                    f"consecutive_errors >= 3 (current: {self._consecutive_errors})",
                    owner="error_handler",
                    metadata={"error_count": self._consecutive_errors, "last_error": error_msg}
                )
    
    async def reset_errors(self) -> None:
        """Сброс ошибок (успешный цикл)"""
        async with self._state_lock:
            if self._consecutive_errors > 0:
                old_errors = self._consecutive_errors
                self._consecutive_errors = 0
                
                # Автоматический переход из DEGRADED в RUNNING
                if self._state == SystemState.DEGRADED:
                    await self.transition_to(
                        SystemState.RUNNING,
                        f"errors reset (was {old_errors})",
                        owner="recovery_mechanism"
                    )
    
    async def record_recovery_cycle(self, success: bool) -> bool:
        """
        Запись цикла восстановления
        
        Args:
            success: Успешен ли цикл
        
        Returns:
            True если recovery завершён (достаточно успешных циклов)
        """
        async with self._state_lock:
            if self._state != SystemState.SAFE_MODE and self._state != SystemState.RECOVERING:
                return False
            
            if success:
                self._recovery_cycles += 1
                
                # Проверяем, достаточно ли циклов для перехода в RECOVERING
                if self._state == SystemState.SAFE_MODE and self._recovery_cycles >= 3:
                    await self.transition_to(
                        SystemState.RECOVERING,
                        f"recovery_cycles >= 3 (current: {self._recovery_cycles})",
                        owner="recovery_mechanism",
                        metadata={"recovery_cycles": self._recovery_cycles}
                    )
                    return True
                
                # Проверяем, достаточно ли циклов для перехода в RUNNING
                if self._state == SystemState.RECOVERING and self._recovery_cycles >= 3:
                    await self.transition_to(
                        SystemState.RUNNING,
                        f"recovery completed (cycles: {self._recovery_cycles})",
                        owner="recovery_mechanism",
                        metadata={"recovery_cycles": self._recovery_cycles}
                    )
                    return True
            else:
                # Ошибка во время recovery - сбрасываем счётчик
                if self._recovery_cycles > 0:
                    logger.warning(
                        f"RECOVERY_CYCLE_FAILED "
                        f"state={self._state.value} "
                        f"recovery_cycles={self._recovery_cycles} "
                        f"resetting counter"
                    )
                    self._recovery_cycles = 0
            
            return False
    
    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """
        HARDENING: Устанавливает event loop для thread-safe вызовов из ThreadWatchdog.
        Должен быть вызван один раз при старте системы.
        """
        self._loop = loop
    
    def trigger_loop_stall_thread_safe(self, time_since_heartbeat: float, incident_id: Optional[str] = None) -> bool:
        """
        HARDENING: Thread-safe триггер LOOP_STALL из ThreadWatchdog.
        
        SINGLE-WRITER PRINCIPLE: ThreadWatchdog НЕ МУТИРУЕТ состояние напрямую.
        Вместо этого отправляет событие в очередь, которое обрабатывается в asyncio loop.
        
        Args:
            time_since_heartbeat: Время с последнего heartbeat
            incident_id: ID инцидента (если None, генерируется автоматически)
        
        Returns:
            True если событие поставлено в очередь
        """
        if self._loop is None:
            logger.error("STATE_MACHINE: Event loop not set, cannot trigger LOOP_STALL from thread")
            return False
        
        if incident_id is None:
            incident_id = f"thread-watchdog-{uuid.uuid4().hex[:8]}"
        
        # HARDENING: Проверяем текущее состояние БЕЗ блокировки (только чтение)
        # Если уже в FATAL, не отправляем событие
        if self._state == SystemState.FATAL:
            return False
        
        # HARDENING: Отправляем событие в очередь для обработки в asyncio loop
        # Это гарантирует, что все переходы состояния происходят в одном потоке (asyncio)
        event = {
            "type": "LOOP_STALL",
            "time_since_heartbeat": time_since_heartbeat,
            "incident_id": incident_id,
            "owner": "ThreadWatchdog"
        }
        
        # HARDENING: Проверяем shutdown - запрет на новые события
        if self._shutdown_started:
            logger.warning("STATE_MACHINE: Shutdown started, rejecting new events")
            return False
        
        # HARDENING: Проверяем, что loop не закрыт
        if self._loop is None or self._loop.is_closed():
            logger.critical("STATE_MACHINE: Event loop closed, cannot deliver event")
            # Loop закрыт → немедленный FATAL (но мы не можем вызвать async transition из thread)
            # Это обработается в FATAL_REAPER
            return False
        
        try:
            self._event_queue.put_nowait(event)
            # Сбрасываем счётчик подряд идущих отбросов
            self._event_queue_consecutive_drops = 0
            # Планируем обработку события в asyncio loop
            try:
                self._loop.call_soon_threadsafe(self._process_event_queue)
            except RuntimeError:
                # Loop закрыт во время вызова
                logger.critical("STATE_MACHINE: Event loop closed during call_soon_threadsafe")
                return False
            return True
        except queue.Full:
            self._event_queue_drops += 1
            self._event_queue_consecutive_drops += 1
            logger.error(
                f"STATE_MACHINE: Event queue full, LOOP_STALL event dropped "
                f"(drops={self._event_queue_drops}, consecutive={self._event_queue_consecutive_drops})"
            )
            
            # HARDENING: Если очередь переполнена N раз подряд → FATAL
            if self._event_queue_consecutive_drops >= self._event_queue_max_consecutive_drops:
                logger.critical(
                    f"STATE_MACHINE: EVENT_DELIVERY_FAILURE - "
                    f"{self._event_queue_consecutive_drops} consecutive drops, "
                    f"triggering FATAL transition"
                )
                # Пытаемся вызвать FATAL transition через asyncio (если loop жив)
                if self._loop is not None and not self._loop.is_closed():
                    try:
                        # Создаём задачу для FATAL transition
                        asyncio.run_coroutine_threadsafe(
                            self.transition_to(
                                SystemState.FATAL,
                                f"EVENT_DELIVERY_FAILURE: {self._event_queue_consecutive_drops} consecutive queue drops",
                                owner="event_queue_guard",
                                metadata={"drops": self._event_queue_drops, "consecutive": self._event_queue_consecutive_drops}
                            ),
                            self._loop
                        )
                    except RuntimeError:
                        # Loop закрыт - FATAL_REAPER обработает
                        pass
            return False
    
    def _process_event_queue(self) -> None:
        """
        HARDENING: Обрабатывает события из очереди в asyncio loop.
        Должен вызываться только из asyncio loop (через call_soon_threadsafe).
        """
        try:
            while True:
                try:
                    event = self._event_queue.get_nowait()
                except queue.Empty:
                    break
                
                # Создаём задачу для обработки события
                asyncio.create_task(self._handle_event(event))
        except Exception as e:
            logger.error(f"STATE_MACHINE: Error processing event queue: {type(e).__name__}: {e}")
    
    async def _handle_event(self, event: dict) -> None:
        """
        HARDENING: Обрабатывает одно событие из очереди.
        Все переходы состояния происходят здесь, в asyncio loop.
        """
        event_type = event.get("type")
        
        if event_type == "LOOP_STALL":
            # HARDENING: Переход в SAFE_MODE только через state machine
            # ThreadWatchdog НЕ МОЖЕТ напрямую изменить состояние
            await self.transition_to(
                SystemState.SAFE_MODE,
                f"LOOP_STALL detected by ThreadWatchdog (time_since_heartbeat: {event['time_since_heartbeat']:.1f}s)",
                owner=event.get("owner", "ThreadWatchdog"),
                metadata={
                    "time_since_heartbeat": event["time_since_heartbeat"],
                    "incident_id": event.get("incident_id")
                }
            )
    
    async def check_safe_mode_ttl(self) -> bool:
        """
        HARDENING: Проверка TTL для SAFE_MODE.
        Вся логика TTL находится в state machine, не в ThreadWatchdog.
        
        Returns:
            True если TTL истёк (переход в FATAL выполнен)
        """
        async with self._state_lock:
            if self._state != SystemState.SAFE_MODE:
                return False
            
            if self._safe_mode_entered_at is None:
                return False
            
            duration = (datetime.now(UTC) - self._safe_mode_entered_at).total_seconds()
            
            if duration >= self._safe_mode_ttl:
                await self.transition_to(
                    SystemState.FATAL,
                    f"SAFE_MODE TTL expired (duration: {duration:.1f}s, limit: {self._safe_mode_ttl}s)",
                    owner="safe_mode_ttl_guard",
                    metadata={"duration": duration, "ttl": self._safe_mode_ttl}
                )
                return True
            
            return False
    
    def mark_shutdown_started(self) -> None:
        """
        HARDENING: Помечает начало shutdown.
        После этого все state transitions запрещены.
        """
        self._shutdown_started = True
        logger.critical("STATE_MACHINE: Shutdown started - all state transitions disabled")
    
    def get_safe_mode_entered_at(self) -> Optional[datetime]:
        """
        HARDENING: Thread-safe чтение safe_mode_entered_at для ThreadWatchdog.
        НЕ использует async/await, безопасно вызывать из thread.
        """
        return self._safe_mode_entered_at
    
    def get_safe_mode_ttl(self) -> float:
        """
        HARDENING: Thread-safe чтение safe_mode_ttl для ThreadWatchdog.
        """
        return self._safe_mode_ttl
    
    def should_exit_fatal(self) -> bool:
        """
        HARDENING: Проверка, нужно ли выполнить os._exit(FATAL_EXIT_CODE).
        Может быть вызвана из любого потока (thread-safe чтение).
        
        Returns:
            True если состояние FATAL и нужно выйти
        """
        return self._state == SystemState.FATAL
    
    async def update_heartbeat(self) -> None:
        """Обновление heartbeat (для SAFE_MODE мониторинга)"""
        async with self._state_lock:
            self._last_heartbeat = datetime.now(UTC)
    
    def get_state_info(self) -> Dict[str, Any]:
        """Получить информацию о текущем состоянии"""
        duration = None
        if self._state in self._state_entered_at:
            duration = (datetime.now(UTC) - self._state_entered_at[self._state]).total_seconds()
        
        return {
            "state": self._state.value,
            "duration_in_state": duration,
            "consecutive_errors": self._consecutive_errors,
            "recovery_cycles": self._recovery_cycles,
            "safe_mode_entered_at": self._safe_mode_entered_at.isoformat() if self._safe_mode_entered_at else None,
            "last_heartbeat": self._last_heartbeat.isoformat() if self._last_heartbeat else None,
            "transitions_count": len(self._transitions),
            "last_transition": {
                "from": self._transitions[-1].from_state.value if self._transitions else None,
                "to": self._transitions[-1].to_state.value if self._transitions else None,
                "reason": self._transitions[-1].reason if self._transitions else None,
                "incident_id": self._transitions[-1].incident_id if self._transitions else None,
                "timestamp": self._transitions[-1].timestamp.isoformat() if self._transitions else None,
            } if self._transitions else None
        }


# Глобальный экземпляр
_state_machine: Optional[SystemStateMachine] = None


def get_state_machine(safe_mode_ttl: float = 600.0) -> SystemStateMachine:
    """
    Получить глобальный экземпляр state machine.
    
    HARDENING: safe_mode_ttl может быть передан для конфигурации.
    """
    global _state_machine
    if _state_machine is None:
        _state_machine = SystemStateMachine(safe_mode_ttl=safe_mode_ttl)
    return _state_machine


def set_state_machine(sm: SystemStateMachine) -> None:
    """Установить глобальный экземпляр (для тестов)"""
    global _state_machine
    _state_machine = sm

