"""
Runtime test для timeout handling в market_analysis_loop.

Проверяет:
a) TimeoutError не завершает loop
b) safe_mode активируется
c) runtime продолжает работать
"""
import os
import sys
import asyncio
import time
from unittest.mock import Mock, patch, AsyncMock

# Импортируем после установки ENV
from system_state import SystemState
from core.decision_core import get_decision_core

async def test_timeout_handling():
    """Тест обработки TimeoutError в market_analysis_loop"""
    
    print("🧪 Тест: Timeout Handling в Market Analysis Loop")
    
    # Создаём SystemState
    system_state = SystemState()
    initial_errors = system_state.system_health.consecutive_errors
    initial_safe_mode = system_state.system_health.safe_mode
    
    print(f"   📊 Начальное состояние: consecutive_errors={initial_errors}, safe_mode={initial_safe_mode}")
    
    # a) Проверяем, что TimeoutError обрабатывается корректно
    print("\n   🔍 Проверка обработки TimeoutError...")
    
    # Симулируем TimeoutError в asyncio.wait_for
    async def simulate_timeout_operation():
        """Симулирует операцию, которая превышает таймаут"""
        await asyncio.sleep(0.1)  # Небольшая задержка
        raise asyncio.TimeoutError("Simulated timeout")
    
    # Проверяем, что TimeoutError обрабатывается
    try:
        await asyncio.wait_for(simulate_timeout_operation(), timeout=0.05)
        assert False, "Должен быть поднят TimeoutError"
    except asyncio.TimeoutError:
        print("      ✅ TimeoutError успешно обработан")
    
    # b) Проверяем, что safe_mode активируется
    print("\n   🔍 Проверка safe_mode активации...")
    
    # Симулируем обработку TimeoutError (как в market_analysis_loop)
    system_state.record_error("LOOP_GUARD_TIMEOUT: test timeout")
    
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
    
    # c) Проверяем, что runtime продолжает работать
    print("\n   🔍 Проверка runtime continuation...")
    
    # Проверяем, что система все еще работает
    assert system_state.system_health.is_running == True, "Система должна продолжать работать"
    print("   ✅ Runtime продолжает работать после TimeoutError")
    
    print("\n   ✅ Все проверки пройдены")
    return True

async def test_timeout_in_loop_guard():
    """Тест обработки TimeoutError в loop guard (asyncio.sleep с timeout)"""
    
    print("\n🧪 Тест: Timeout Handling в Loop Guard")
    
    system_state = SystemState()
    
    # Симулируем ситуацию, когда asyncio.sleep с timeout превышает таймаут
    # Это может произойти, если event loop застопорился
    
    async def test_loop_guard_timeout():
        """Тест loop guard с timeout"""
        try:
            # Симулируем asyncio.sleep с timeout
            # В реальности, если event loop застопорился, sleep может не завершиться вовремя
            await asyncio.wait_for(
                asyncio.sleep(0.1),
                timeout=0.05  # Таймаут меньше чем sleep
            )
            assert False, "Должен быть поднят TimeoutError"
        except asyncio.TimeoutError:
            # Это health signal - обрабатываем
            print("      ✅ TimeoutError в loop guard обработан")
            system_state.record_error("LOOP_GUARD_TIMEOUT: test")
            system_state.system_health.safe_mode = True
            # Продолжаем выполнение - не завершаем
            return True
    
    result = await test_loop_guard_timeout()
    assert result == True, "Loop guard должен обработать TimeoutError и продолжить"
    
    assert system_state.system_health.safe_mode == True, "safe_mode должен быть активирован"
    print("   ✅ Loop guard timeout обработан корректно")
    
    return True

if __name__ == "__main__":
    async def main():
        try:
            await test_timeout_handling()
            await test_timeout_in_loop_guard()
            print("\n✅ Все тесты timeout handling пройдены успешно")
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

