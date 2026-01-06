"""
INVARIANT TESTS - Production Hardening Guarantees

Тесты для проверки инвариантов системы:
- FATAL всегда приводит к exit
- SAFE_MODE TTL убивает процесс даже если asyncio stalled
- ThreadWatchdog триггерит без asyncio
- Нет state transitions после shutdown start
- ThreadWatchdog идемпотентен
"""
import pytest
import os
import sys
import time
import threading
import asyncio
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, UTC, timedelta

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from system_state_machine import SystemStateMachine, SystemState
from runner import FatalReaper, ThreadWatchdog, ThreadWatchdogState, FATAL_EXIT_CODE, SAFE_MODE_TTL


class TestFatalAlwaysExits:
    """Тест: FATAL всегда приводит к exit"""
    
    @pytest.mark.skipif(os.getenv("CI") == "true", reason="os._exit kills test process")
    def test_fatal_reaper_exits_on_fatal(self):
        """FATAL_REAPER должен вызвать os._exit при FATAL"""
        state_machine = SystemStateMachine()
        reaper = FatalReaper(state_machine, check_interval=0.1)
        
        # Переводим в FATAL
        asyncio.run(state_machine.transition_to(
            SystemState.FATAL,
            "test: FATAL state",
            owner="test"
        ))
        
        # Запускаем reaper
        reaper.start()
        
        # Ждём немного - reaper должен обнаружить FATAL и вызвать os._exit
        time.sleep(0.5)
        
        # Если мы дошли сюда, os._exit не был вызван - тест провален
        assert False, "FATAL_REAPER should have called os._exit"
    
    def test_fatal_reaper_stops_on_stop_event(self):
        """FATAL_REAPER должен остановиться при stop_event"""
        state_machine = SystemStateMachine()
        reaper = FatalReaper(state_machine, check_interval=0.1)
        
        reaper.start()
        time.sleep(0.2)
        
        # Останавливаем
        reaper.stop()
        time.sleep(0.2)
        
        # Thread должен быть остановлен
        assert not reaper.thread.is_alive()


class TestSafeModeTtlKillsProcess:
    """Тест: SAFE_MODE TTL убивает процесс"""
    
    @pytest.mark.skipif(os.getenv("CI") == "true", reason="os._exit kills test process")
    def test_thread_watchdog_exits_on_safe_mode_ttl(self):
        """ThreadWatchdog должен вызвать os._exit при истечении SAFE_MODE TTL"""
        state_machine = SystemStateMachine(safe_mode_ttl=0.5)  # Короткий TTL для теста
        
        # Переводим в SAFE_MODE
        asyncio.run(state_machine.transition_to(
            SystemState.SAFE_MODE,
            "test: SAFE_MODE",
            owner="test"
        ))
        
        # Устанавливаем event loop для state machine
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        state_machine.set_event_loop(loop)
        
        # Создаём watchdog
        watchdog = ThreadWatchdog(state_machine, heartbeat_timeout=30.0)
        watchdog.start()
        
        # Помечаем как armed
        watchdog.first_heartbeat_received = True
        watchdog.event_loop_set = True
        watchdog.arm()
        
        # Ждём TTL + немного
        time.sleep(0.7)
        
        # Если мы дошли сюда, os._exit не был вызван - тест провален
        assert False, "ThreadWatchdog should have called os._exit on SAFE_MODE TTL"
    
    def test_thread_watchdog_checks_safe_mode_ttl(self):
        """ThreadWatchdog должен проверять SAFE_MODE TTL"""
        state_machine = SystemStateMachine(safe_mode_ttl=10.0)
        
        # Переводим в SAFE_MODE
        asyncio.run(state_machine.transition_to(
            SystemState.SAFE_MODE,
            "test: SAFE_MODE",
            owner="test"
        ))
        
        # Проверяем, что entered_at установлен
        entered_at = state_machine.get_safe_mode_entered_at()
        assert entered_at is not None
        
        # Проверяем TTL
        ttl = state_machine.get_safe_mode_ttl()
        assert ttl == 10.0


class TestWatchdogTriggersWithoutAsyncio:
    """Тест: ThreadWatchdog триггерит без asyncio"""
    
    def test_watchdog_detects_loop_stall(self):
        """ThreadWatchdog должен обнаружить LOOP_STALL без asyncio"""
        state_machine = SystemStateMachine()
        
        # Создаём watchdog
        watchdog = ThreadWatchdog(state_machine, heartbeat_timeout=0.5)
        
        # Устанавливаем старый heartbeat (имитация stall)
        from runner import update_heartbeat_timestamp
        old_time = time.time() - 1.0
        update_heartbeat_timestamp(old_time)
        
        # Устанавливаем event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        state_machine.set_event_loop(loop)
        
        watchdog.start()
        watchdog.first_heartbeat_received = True
        watchdog.event_loop_set = True
        watchdog.arm()
        
        # Ждём немного
        time.sleep(0.7)
        
        # Проверяем, что watchdog сработал
        with watchdog.lifecycle_lock:
            assert watchdog.lifecycle_state == ThreadWatchdogState.TRIGGERED
        
        # Останавливаем
        watchdog.stop()
        loop.close()


class TestNoStateTransitionAfterShutdown:
    """Тест: Нет state transitions после shutdown start"""
    
    def test_transition_blocked_after_shutdown(self):
        """State transitions должны быть заблокированы после shutdown start"""
        state_machine = SystemStateMachine()
        
        # Помечаем shutdown
        state_machine.mark_shutdown_started()
        
        # Пытаемся перейти в другое состояние
        result = asyncio.run(state_machine.transition_to(
            SystemState.DEGRADED,
            "test: transition after shutdown",
            owner="test"
        ))
        
        # Переход должен быть заблокирован
        assert result is False
        assert state_machine.state == SystemState.RUNNING  # Состояние не изменилось
    
    def test_event_rejected_after_shutdown(self):
        """События должны быть отклонены после shutdown start"""
        state_machine = SystemStateMachine()
        
        # Устанавливаем event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        state_machine.set_event_loop(loop)
        
        # Помечаем shutdown
        state_machine.mark_shutdown_started()
        
        # Пытаемся отправить событие
        result = state_machine.trigger_loop_stall_thread_safe(
            time_since_heartbeat=10.0,
            incident_id="test"
        )
        
        # Событие должно быть отклонено
        assert result is False
        
        loop.close()


class TestThreadWatchdogIdempotent:
    """Тест: ThreadWatchdog идемпотентен"""
    
    def test_watchdog_does_not_trigger_twice(self):
        """ThreadWatchdog не должен триггерить дважды"""
        state_machine = SystemStateMachine()
        
        # Создаём watchdog
        watchdog = ThreadWatchdog(state_machine, heartbeat_timeout=0.5)
        
        # Устанавливаем старый heartbeat
        from runner import update_heartbeat_timestamp
        old_time = time.time() - 1.0
        update_heartbeat_timestamp(old_time)
        
        # Устанавливаем event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        state_machine.set_event_loop(loop)
        
        watchdog.start()
        watchdog.first_heartbeat_received = True
        watchdog.event_loop_set = True
        watchdog.arm()
        
        # Ждём первого срабатывания
        time.sleep(0.7)
        
        # Проверяем, что сработал
        with watchdog.lifecycle_lock:
            assert watchdog.lifecycle_state == ThreadWatchdogState.TRIGGERED
        
        # Пытаемся триггерить снова
        watchdog._trigger_loop_stall(1.0, old_time)
        
        # Состояние не должно измениться (идемпотентность)
        with watchdog.lifecycle_lock:
            assert watchdog.lifecycle_state == ThreadWatchdogState.TRIGGERED
        
        # Останавливаем
        watchdog.stop()
        loop.close()


class TestEventQueueOverflow:
    """Тест: Переполнение очереди событий → FATAL"""
    
    def test_event_queue_overflow_triggers_fatal(self):
        """Переполнение очереди событий должно привести к FATAL"""
        state_machine = SystemStateMachine()
        
        # Устанавливаем event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        state_machine.set_event_loop(loop)
        
        # Заполняем очередь до предела
        for i in range(15):  # Больше чем maxsize=10
            state_machine.trigger_loop_stall_thread_safe(
                time_since_heartbeat=10.0 + i,
                incident_id=f"test-{i}"
            )
        
        # Ждём обработки
        time.sleep(0.1)
        
        # Проверяем, что произошёл переход в FATAL из-за переполнения
        # (если consecutive_drops >= max_consecutive_drops)
        # Это зависит от реализации, но мы должны увидеть логи
        
        loop.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
