# Event Loop Architecture

## Принципы

1. **ОДИН event loop на весь процесс**
   - Создается в `if __name__ == "__main__":` через `asyncio.run(main())`
   - Никогда не пересоздается
   - Никогда не вложен

2. **Все async операции через await или asyncio.create_task**
   - Никаких `asyncio.run()`, `loop.run_until_complete()`, `new_event_loop()` в runtime коде
   - Все задачи создаются через `asyncio.create_task()` и управляются централизованно

3. **Централизованное управление задачами**
   - Все задачи регистрируются в `_background_tasks`
   - Shutdown отменяет все задачи и ждет их завершения

4. **Graceful shutdown**
   - SIGTERM/SIGINT устанавливает `_shutdown_requested = True`
   - Все задачи проверяют `_shutdown_requested` и `system_state.system_health.is_running`
   - Задачи корректно обрабатывают `asyncio.CancelledError`

5. **Timeout handling**
   - `asyncio.wait_for()` используется ТОЛЬКО для реальных операций (не для `asyncio.sleep()`)
   - `asyncio.sleep()` не может превысить timeout, поэтому `wait_for(sleep(...), timeout=...)` - антипаттерн
   - Timeout errors обрабатываются как health signals, не fatal errors

## Структура

```
if __name__ == "__main__":
    asyncio.run(main())  # ЕДИНСТВЕННОЕ место создания event loop

async def main():
    # Создание всех задач
    tasks = [
        asyncio.create_task(market_analysis_loop()),
        asyncio.create_task(runtime_heartbeat_loop()),
        ...
    ]
    
    # Ожидание завершения с обработкой ошибок
    await asyncio.gather(*tasks, return_exceptions=True)
    
    # Graceful shutdown
    finally:
        # Отмена всех задач
        # Ожидание завершения
```

## Задачи

- `market_analysis_loop()` - основной цикл анализа рынка
- `runtime_heartbeat_loop()` - runtime heartbeat
- `heartbeat_loop()` - Telegram heartbeat
- `daily_report_loop()` - ежедневные отчеты
- `telegram_supervisor()` - supervisor для Telegram polling
- `synthetic_decision_tick_loop()` - synthetic decision tick (опционально)
- `loop_stall_injection_task()` - loop stall injection (опционально)

## Shutdown Sequence

1. SIGTERM/SIGINT → `signal_handler()` → `_shutdown_requested = True`
2. Все задачи проверяют `_shutdown_requested` и выходят из циклов
3. `main()` выходит из `asyncio.gather()`
4. `finally` блок:
   - `system_state.system_health.is_running = False`
   - Отмена всех задач через `cancel_all_background_tasks()`
   - Отмена основных задач
   - Ожидание завершения с таймаутом
   - Cleanup (PID file, logs)

## Антипаттерны (ИЗБЕГАТЬ)

1. ❌ `asyncio.wait_for(asyncio.sleep(...), timeout=...)` - бессмысленно
2. ❌ `asyncio.run()` в runtime коде - создает новый loop
3. ❌ `loop.run_until_complete()` - управляет loop
4. ❌ `asyncio.new_event_loop()` - создает новый loop
5. ❌ `asyncio.gather(*tasks)` без `return_exceptions=True` - падает при первой ошибке

## Правильные паттерны

1. ✅ `await asyncio.sleep(duration)` - простая пауза
2. ✅ `await asyncio.wait_for(real_operation(), timeout=...)` - для реальных операций
3. ✅ `await asyncio.gather(*tasks, return_exceptions=True)` - с обработкой ошибок
4. ✅ Проверка `_shutdown_requested` в циклах
5. ✅ Обработка `asyncio.CancelledError` в задачах

