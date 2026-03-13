#!/usr/bin/env python3
"""
Тестовый скрипт для проверки отправки сообщений в Telegram
"""
from telegram_bot import send_message, send_chart
from system_state import get_system_state

def test_send_message():
    """Тестирует отправку простого сообщения"""
    print("=" * 50)
    print("ТЕСТ: Отправка простого сообщения")
    print("=" * 50)
    
    test_message = "🧪 Тестовое сообщение от бота\n\nЕсли вы видите это сообщение, значит отправка работает!"
    send_message(test_message)
    
    print("\nОжидайте 10 секунд для завершения отправки...")
    import time
    time.sleep(10)
    print("Тест завершен\n")

def test_send_chart():
    """Тестирует отправку графика"""
    print("=" * 50)
    print("ТЕСТ: Отправка графика")
    print("=" * 50)
    
    test_symbol = "BTCUSDT"
    send_chart(test_symbol)
    
    print("\nОжидайте 10 секунд для завершения отправки...")
    import time
    time.sleep(10)
    print("Тест завершен\n")

def test_signal_cache():
    """Показывает текущий кэш сигналов (через SystemState)"""
    print("=" * 50)
    print("ТЕКУЩИЙ КЭШ СИГНАЛОВ")
    print("=" * 50)
    
    system_state = get_system_state()
    if system_state:
        # SystemState хранит кэш сигналов внутри
        print("Кэш сигналов хранится в SystemState")
        print("Используйте system_state.is_new_signal() для проверки")
    else:
        print("SystemState не инициализирован")
    
    print()

def reset_cache():
    """Сбрасывает кэш сигналов (через SystemState)"""
    print("=" * 50)
    print("СБРОС КЭША СИГНАЛОВ")
    print("=" * 50)
    
    system_state = get_system_state()
    if system_state:
        # SystemState управляет кэшем сигналов
        print("Кэш сигналов управляется через SystemState")
        print("Используйте system_state для управления кэшем")
    else:
        print("SystemState не инициализирован")
    
    print()

if __name__ == "__main__":
    import sys
    
    print("\n" + "=" * 50)
    print("ТЕСТИРОВАНИЕ TELEGRAM БОТА")
    print("=" * 50 + "\n")
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "message":
            test_send_message()
        elif command == "chart":
            test_send_chart()
        elif command == "cache":
            test_signal_cache()
        elif command == "reset":
            reset_cache()
        elif command == "all":
            test_send_message()
            test_send_chart()
            test_signal_cache()
        else:
            print(f"Неизвестная команда: {command}")
            print("\nДоступные команды:")
            print("  python test_telegram.py message  - тест отправки сообщения")
            print("  python test_telegram.py chart    - тест отправки графика")
            print("  python test_telegram.py cache   - показать кэш сигналов")
            print("  python test_telegram.py reset   - сбросить кэш сигналов")
            print("  python test_telegram.py all      - выполнить все тесты")
    else:
        print("Использование:")
        print("  python test_telegram.py message  - тест отправки сообщения")
        print("  python test_telegram.py chart    - тест отправки графика")
        print("  python test_telegram.py cache   - показать кэш сигналов")
        print("  python test_telegram.py reset   - сбросить кэш сигналов")
        print("  python test_telegram.py all      - выполнить все тесты")
        print("\nПример:")
        print("  python test_telegram.py message")

