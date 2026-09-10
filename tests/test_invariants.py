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

# Добавляем путь к корню проекта
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from system_state_machine import SystemStateMachine, SystemState
from runner import FatalReaper, ThreadWatchdog, ThreadWatchdogState, FATAL_EXIT_CODE, SAFE_MODE_TTL


class TestFatalAlwaysExits:
    """Тест: FATAL всегда приводит к exit"""

    def test_fatal_reaper_exits_on_fatal(self):
        """
        FATAL_REAPER обязан завершить процесс при FATAL — и обязан сделать это
        с кодом FATAL_EXIT_CODE.

        Раньше этот тест звал настоящий os._exit и «проходил», убивая pytest:
        прогон обрывался с кодом 10 без сводки, а инвариант в CI не проверялся
        вовсе — его закрывал skipif(CI == "true"). Теперь выход подменяется,
        и проверяется факт вызова и код.
        """
        state_machine = SystemStateMachine()
        exit_calls = []
        reaper = FatalReaper(state_machine, check_interval=0.05,
                             exit_fn=lambda code: exit_calls.append(code))

        asyncio.run(state_machine.transition_to(
            SystemState.FATAL,
            "test: FATAL state",
            owner="test"
        ))

        reaper.start()
        deadline = time.monotonic() + 5.0
        while not exit_calls and time.monotonic() < deadline:
            time.sleep(0.02)
        reaper.stop()

        assert exit_calls, "FATAL_REAPER не вызвал выход при состоянии FATAL"
        assert exit_calls[0] == FATAL_EXIT_CODE, (
            f"FATAL_REAPER вышел с кодом {exit_calls[0]}, ожидался {FATAL_EXIT_CODE}: "
            "systemd отличает штатный выход от фатального именно по коду"
        )

    def test_fatal_reaper_does_not_exit_while_running(self):
        """
        Обратная сторона того же инварианта: пока состояние не FATAL, reaper
        обязан молчать. Без этой проверки тест выше проходил бы и на reaper-е,
        который вызывает выход безусловно.
        """
        state_machine = SystemStateMachine()
        exit_calls = []
        reaper = FatalReaper(state_machine, check_interval=0.05,
                             exit_fn=lambda code: exit_calls.append(code))

        reaper.start()
        time.sleep(0.3)
        reaper.stop()

        assert exit_calls == [], f"reaper вызвал выход вне FATAL: {exit_calls}"

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

    def test_thread_watchdog_exits_on_safe_mode_ttl(self):
        """
        Инвариант: истёкший TTL режима SAFE_MODE обязан убить процесс, даже если
        asyncio встал. Проверяется через подменяемый выход — раньше тест звал
        настоящий os._exit и уносил с собой весь прогон.
        """
        state_machine = SystemStateMachine(safe_mode_ttl=0.3)

        asyncio.run(state_machine.transition_to(
            SystemState.SAFE_MODE,
            "test: SAFE_MODE",
            owner="test"
        ))

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        state_machine.set_event_loop(loop)

        exit_calls = []
        watchdog = ThreadWatchdog(state_machine, heartbeat_timeout=30.0, check_interval=0.05,
                                  exit_fn=lambda code: exit_calls.append(code))
        watchdog.start()
        watchdog.first_heartbeat_received = True
        watchdog.event_loop_set = True
        watchdog.arm()

        try:
            deadline = time.monotonic() + 5.0
            while not exit_calls and time.monotonic() < deadline:
                time.sleep(0.02)
        finally:
            watchdog.stop()
            loop.close()
            asyncio.set_event_loop(None)

        assert exit_calls, "ThreadWatchdog не вызвал выход по истечении SAFE_MODE TTL"
        assert exit_calls[0] == FATAL_EXIT_CODE, (
            f"выход с кодом {exit_calls[0]}, ожидался {FATAL_EXIT_CODE}"
        )

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
        watchdog = ThreadWatchdog(state_machine, heartbeat_timeout=0.5, check_interval=0.05)

        # Имитируем зависший loop: heartbeat датирован прошлым.
        # Функция называется update_heartbeat_thread_safe; имени
        # update_heartbeat_timestamp в runner.py нет и, судя по git, не было —
        # тест ссылался на несуществующее API и падал бы с ImportError. Этого никто
        # не видел, потому что файл не доживал до него: первый же тест звал
        # настоящий os._exit и уносил весь прогон.
        from runner import update_heartbeat_thread_safe
        old_dt = datetime.now(UTC) - timedelta(seconds=1.0)
        old_time = old_dt.timestamp()
        update_heartbeat_thread_safe(old_dt)

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
        watchdog = ThreadWatchdog(state_machine, heartbeat_timeout=0.5, check_interval=0.05)

        # Имитируем зависший loop (см. пояснение в test_watchdog_detects_loop_stall)
        from runner import update_heartbeat_thread_safe
        old_dt = datetime.now(UTC) - timedelta(seconds=1.0)
        old_time = old_dt.timestamp()
        update_heartbeat_thread_safe(old_dt)

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
        """
        Переполнение очереди событий взводит аварийный выход с кодом 1 — ровно
        один раз, сколько бы событий ни отбросилось сверх порога.

        Раньше тест не проверял ничего, а взводил настоящий os._exit: через 10 с
        фоновый поток гасил весь прогон pytest, если набор не успевал закончиться.
        """
        exits = []
        state_machine = SystemStateMachine(exit_fn=exits.append, force_exit_delay=0.05)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        state_machine.set_event_loop(loop)

        # Очередь на 10 событий; цикл не запущен, поэтому события не разбираются
        # и всё сверх 10 отбрасывается. Порог — 5 отбросов подряд.
        for i in range(25):
            state_machine.trigger_loop_stall_thread_safe(
                time_since_heartbeat=10.0 + i,
                incident_id=f"test-{i}"
            )

        deadline = time.monotonic() + 2.0
        while not exits and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.2)  # второй поток, если бы его взвели, успел бы выстрелить

        assert exits == [1], "аварийный выход взводится один раз и с кодом 1"
        assert state_machine._event_queue_drops >= state_machine._event_queue_max_consecutive_drops

        loop.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
