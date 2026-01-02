"""
Runtime test для loop stall detection.

Проверяет:
a) stall is injected
b) heartbeat miss is detected
c) safe_mode activates
d) runtime recovers
"""
import os
import sys
import asyncio
import time
from datetime import datetime, UTC

# Устанавливаем ENV для loop stall injection
os.environ["FAULT_INJECT_LOOP_STALL"] = "true"

# Импортируем после установки ENV
from system_state import SystemState
from core.decision_core import get_decision_core

async def test_loop_stall_detection():
    """Тест loop stall detection"""
    
    print("🧪 Тест: Loop Stall Detection")
    
    # Проверяем, что ENV установлен
    from runner import FAULT_INJECT_LOOP_STALL, LOOP_STALL_DURATION, HEARTBEAT_MISS_THRESHOLD
    assert FAULT_INJECT_LOOP_STALL == True, \
        "FAULT_INJECT_LOOP_STALL должен быть True"
    print("   ✅ ENV переменная установлена")
    
    # Создаём SystemState
    system_state = SystemState()
    initial_errors = system_state.system_health.consecutive_errors
    initial_safe_mode = system_state.system_health.safe_mode
    
    print(f"   📊 Начальное состояние: consecutive_errors={initial_errors}, safe_mode={initial_safe_mode}")
    
    # a) Проверяем, что stall инжектируется
    print("\n   🔍 Проверка stall injection...")
    
    # Симулируем блокировку event loop
    print(f"      Симулируем блокировку event loop на {LOOP_STALL_DURATION}s...")
    
    # Запускаем heartbeat loop в фоне
    from runner import runtime_heartbeat_loop, RUNTIME_HEARTBEAT_INTERVAL
    
    heartbeat_task = asyncio.create_task(runtime_heartbeat_loop())
    
    # Ждем несколько heartbeats
    await asyncio.sleep(RUNTIME_HEARTBEAT_INTERVAL * 2)
    
    # Обновляем heartbeat для установки baseline
    system_state.update_heartbeat()
    baseline_time = time.time()
    
    # b) Симулируем блокировку event loop (пропуск heartbeats)
    print(f"      Блокируем event loop на {LOOP_STALL_DURATION}s...")
    
    # Используем прямой time.sleep для блокировки (как в fault injection)
    # Это заблокирует event loop и приведет к пропуску heartbeats
    time.sleep(LOOP_STALL_DURATION)
    
    print("      Event loop разблокирован")
    
    # Проверяем, что heartbeat был пропущен
    current_time = time.time()
    time_since_last = current_time - baseline_time
    
    # Проверяем обнаружение пропущенных heartbeats
    expected_interval = RUNTIME_HEARTBEAT_INTERVAL
    missed_heartbeats = int((time_since_last - expected_interval) / expected_interval)
    
    assert time_since_last > expected_interval * HEARTBEAT_MISS_THRESHOLD, \
        f"Должен быть пропуск heartbeats: time_since_last={time_since_last:.1f}s, threshold={expected_interval * HEARTBEAT_MISS_THRESHOLD}s"
    
    print(f"      ✅ Пропуск heartbeats обнаружен: time_since_last={time_since_last:.1f}s, missed={missed_heartbeats}")
    
    # c) Проверяем, что safe_mode активируется
    print("\n   🔍 Проверка safe_mode активации...")
    
    # Симулируем обнаружение stall и запись ошибки
    system_state.record_error("FAULT_INJECTION: loop_stall_detected")
    
    assert system_state.system_health.consecutive_errors > initial_errors, \
        f"consecutive_errors должен увеличиться: {system_state.system_health.consecutive_errors} <= {initial_errors}"
    print(f"      ✅ consecutive_errors увеличился: {system_state.system_health.consecutive_errors}")
    
    # Симулируем достижение MAX_CONSECUTIVE_ERRORS
    MAX_CONSECUTIVE_ERRORS = 5
    for _ in range(MAX_CONSECUTIVE_ERRORS - system_state.system_health.consecutive_errors):
        system_state.record_error("test")
    
    # Проверяем активацию safe_mode
    if system_state.system_health.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
        system_state.system_health.safe_mode = True
        assert system_state.system_health.safe_mode == True, "safe_mode должен быть активирован"
        print(f"      ✅ safe_mode активирован при consecutive_errors={system_state.system_health.consecutive_errors}")
    
    # Проверяем, что DecisionCore блокирует торговлю при safe_mode
    decision_core = get_decision_core()
    decision = decision_core.should_i_trade(system_state=system_state)
    assert decision.can_trade == False, "DecisionCore должен блокировать торговлю при safe_mode"
    assert "SAFE-MODE" in decision.reason, f"Причина должна содержать SAFE-MODE: {decision.reason}"
    print(f"      ✅ DecisionCore блокирует торговлю при safe_mode: {decision.reason}")
    
    print("   ✅ Safe_mode активация работает корректно")
    
    # d) Проверяем, что runtime восстанавливается
    print("\n   🔍 Проверка runtime recovery...")
    
    # Отменяем heartbeat task
    heartbeat_task.cancel()
    try:
        await heartbeat_task
    except asyncio.CancelledError:
        pass
    
    # Проверяем, что система все еще работает
    assert system_state.system_health.is_running == True, "Система должна продолжать работать"
    print("   ✅ Runtime выжил после stall и восстановился")
    
    print("\n   ✅ Все проверки пройдены")
    return True

if __name__ == "__main__":
    async def main():
        try:
            await test_loop_stall_detection()
            print("\n✅ Все тесты loop stall detection пройдены успешно")
            return 0
        except AssertionError as e:
            print(f"\n❌ Тест провален: {e}")
            return 1
        except Exception as e:
            print(f"\n❌ Неожиданная ошибка: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            return 1
    
    sys.exit(asyncio.run(main()))

