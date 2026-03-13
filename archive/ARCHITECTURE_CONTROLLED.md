# Контролируемая Архитектура - Документация

## Цель

Система должна быть **контролируемой** и **наблюдаемой**:
- Уметь **УМИРАТЬ правильно** (воспроизводимый CRITICAL)
- Уметь **ВОССТАНАВЛИВАТЬСЯ автоматически**
- Иметь **наблюдаемость** (task dump, structured logs)
- Быть **управляемой** без ручных костылей

## Компоненты

### 1. Chaos Engine (`chaos_engine.py`)

**Цель**: Воспроизводимый event loop stall для тестирования recovery.

**Паттерны**:
- `cross_lock_deadlock`: asyncio.Lock + threading.Lock deadlock
- `sync_io_block`: Блокирующий I/O в async контексте
- `recursive_await`: Бесконечная рекурсия await
- `cpu_bound_loop`: CPU-bound цикл без yield (hold GIL)

**Использование**:
```bash
# Включить chaos
export CHAOS_ENABLED=true

# Инъекция через HTTP
curl -X POST http://127.0.0.1:8080/admin/chaos/inject \
  -H "Content-Type: application/json" \
  -d '{"type": "cpu_bound_loop", "duration": 300}'
```

**ВАЖНО**: Это НЕ sleep, это РЕАЛЬНЫЙ stall, который блокирует event loop.

### 2. State Machine (`system_state_machine.py`)

**Цель**: Заменить boolean `safe_mode` на контролируемую state machine.

**States**:
- `RUNNING`: Нормальная работа
- `DEGRADED`: Деградация (ошибки, но не критично)
- `SAFE_MODE`: Защитный режим (блокирует торговлю)
- `RECOVERING`: Восстановление (после safe_mode)
- `FATAL`: Критическая ошибка (требует restart)

**Transitions**:
```
RUNNING -> DEGRADED: consecutive_errors >= 3
DEGRADED -> SAFE_MODE: consecutive_errors >= 5 OR loop_stall
SAFE_MODE -> RECOVERING: recovery_cycles >= 3
RECOVERING -> RUNNING: recovery_cycles >= 3 (успешные)
ANY -> FATAL: необратимая ошибка OR SAFE_MODE TTL expired
```

**Инварианты**:
- FATAL не может быть очищен (требует restart)
- SAFE_MODE не может быть terminal (должен перейти в RECOVERING или FATAL)
- Каждый переход логируется с `incident_id` для корреляции

### 3. Task Dump (`task_dump.py`)

**Цель**: Observability для asyncio tasks при CRITICAL состояниях.

**Формат**:
```json
{
  "incident_id": "chaos-abc123",
  "timestamp": "2024-01-01T12:00:00Z",
  "total_tasks": 10,
  "tasks": [
    {
      "task_id": 12345,
      "task_name": "MarketAnalysisLoop",
      "state": "running",
      "coroutine_stack": [...],
      "exception": null
    }
  ]
}
```

**Использование**: Автоматически вызывается при:
- LOOP_GUARD_TIMEOUT
- Переходе в SAFE_MODE
- CRITICAL ошибках

### 4. Systemd Integration (`systemd_integration.py`)

**Цель**: Правильная интеграция с systemd для контролируемых restart.

**Exit Codes**:
- `0`: SUCCESS (graceful shutdown) - не рестартовать
- `2`: RECOVERABLE (recoverable failure) - рестартовать
- `10`: CRITICAL (deadlock detected) - рестартовать
- `77`: CONFIG_ERROR - не рестартовать

**Watchdog**:
- `sd_notify WATCHDOG=1` каждые 30 секунд
- `WatchdogSec=60` в unit file
- Если heartbeat пропущен -> systemd убивает процесс

**Unit File**: `market-bot.service.new`

## Интеграция в runner.py

### Инициализация

```python
# При старте
state_machine = get_state_machine()
systemd = get_systemd_integration()
systemd.notify_ready()

# Watchdog heartbeat task
async def watchdog_heartbeat():
    while True:
        systemd.notify_watchdog()
        await asyncio.sleep(30.0)
```

### Использование State Machine

```python
# Вместо system_state.system_health.safe_mode = True
await state_machine.transition_to(
    SystemState.SAFE_MODE,
    reason="loop_stall_detected",
    owner="loop_guard"
)

# Проверка состояния
if state_machine.is_safe_mode:
    # Блокировать торговлю
    pass
```

### Task Dump при CRITICAL

```python
# При обнаружении stall
incident_id = f"stall-{uuid.uuid4().hex[:8]}"
log_task_dump(incident_id, context="LOOP_STALL_DETECTED")

# Переход в SAFE_MODE
await state_machine.transition_to(
    SystemState.SAFE_MODE,
    reason="loop_stall_detected",
    owner="loop_guard",
    metadata={"incident_id": incident_id}
)
```

### Exit при FATAL

```python
# При FATAL состоянии
if state_machine.is_fatal:
    systemd.exit_with_code(
        ExitCode.CRITICAL,
        reason="FATAL state reached"
    )
```

## Логирование

### Структурированные логи

Все критические события логируются в формате:
```
STATE_TRANSITION incident_id=state-abc123 from=RUNNING to=SAFE_MODE reason=loop_stall_detected owner=loop_guard duration_in_old_state=120.5
```

### Incident Correlation

Каждый инцидент имеет `incident_id`, который используется для корреляции:
- Chaos injection: `chaos-abc123`
- State transition: `state-abc123`
- Task dump: использует тот же `incident_id`

## Тестирование

### 1. Chaos Injection Test

```bash
# Включить chaos
export CHAOS_ENABLED=true

# Запустить бота
python runner.py

# В другом терминале - инъекция chaos
curl -X POST http://127.0.0.1:8080/admin/chaos/inject \
  -H "Content-Type: application/json" \
  -d '{"type": "cpu_bound_loop", "duration": 300}'

# Наблюдать:
# 1. Event loop stall
# 2. LOOP_GUARD_TIMEOUT
# 3. Task dump
# 4. Переход в SAFE_MODE
# 5. Recovery cycles
# 6. Переход в RUNNING
```

### 2. State Machine Test

```python
# Проверка transitions
state_machine = get_state_machine()

# RUNNING -> DEGRADED
await state_machine.record_error("test error")
assert state_machine.state == SystemState.DEGRADED

# DEGRADED -> SAFE_MODE
await state_machine.record_error("error 2")
await state_machine.record_error("error 3")
await state_machine.record_error("error 4")
await state_machine.record_error("error 5")
assert state_machine.state == SystemState.SAFE_MODE

# SAFE_MODE -> RECOVERING
for _ in range(3):
    await state_machine.record_recovery_cycle(True)
assert state_machine.state == SystemState.RECOVERING

# RECOVERING -> RUNNING
for _ in range(3):
    await state_machine.record_recovery_cycle(True)
assert state_machine.state == SystemState.RUNNING
```

### 3. Systemd Test

```bash
# Установить unit file
sudo cp market-bot.service.new /etc/systemd/system/market-bot.service
sudo systemctl daemon-reload

# Запустить
sudo systemctl start market-bot

# Проверить watchdog
sudo systemctl status market-bot
# Должен показывать "Active: active (running)"

# Инъекция chaos -> FATAL -> restart
curl -X POST http://127.0.0.1:8080/admin/chaos/inject

# Проверить логи
sudo journalctl -u market-bot -f
# Должны видеть:
# - STATE_TRANSITION
# - TASK_DUMP
# - SYSTEM_EXIT exit_code=10
```

## План Chaos + Regression Tests

### 1. Unit Tests

- [ ] State machine transitions
- [ ] Chaos patterns (каждый паттерн отдельно)
- [ ] Task dump format
- [ ] Systemd integration (mock)

### 2. Integration Tests

- [ ] Chaos injection -> SAFE_MODE -> RECOVERING -> RUNNING
- [ ] SAFE_MODE TTL expiration -> FATAL
- [ ] Watchdog heartbeat при нормальной работе
- [ ] Watchdog timeout при stall

### 3. Regression Tests

- [ ] Старые тесты должны проходить
- [ ] Обратная совместимость с boolean safe_mode
- [ ] HTTP endpoints работают корректно

## Пример Логов Реального Инцидента

```
2024-01-01T12:00:00Z CRITICAL CHAOS_INJECTION_TRIGGERED incident_id=chaos-abc123 chaos_type=cpu_bound_loop duration=300.0s
2024-01-01T12:00:00Z CRITICAL TASK_DUMP_START incident_id=chaos-abc123 context=CHAOS_INJECTION_START total_tasks=10
2024-01-01T12:00:00Z CRITICAL TASK_DUMP_TASK incident_id=chaos-abc123 task_id=12345 task_name=MarketAnalysisLoop state=running
2024-01-01T12:05:00Z CRITICAL LOOP_GUARD_TIMEOUT incident_id=chaos-abc123 timeout=300s
2024-01-01T12:05:00Z CRITICAL STATE_TRANSITION incident_id=state-def456 from=RUNNING to=SAFE_MODE reason=loop_stall_detected owner=loop_guard duration_in_old_state=300.0
2024-01-01T12:05:00Z CRITICAL TASK_DUMP_START incident_id=state-def456 context=SAFE_MODE total_tasks=10
2024-01-01T12:08:00Z INFO RECOVERY_CYCLE_SUCCESS incident_id=state-def456 recovery_cycles=1
2024-01-01T12:11:00Z INFO RECOVERY_CYCLE_SUCCESS incident_id=state-def456 recovery_cycles=2
2024-01-01T12:14:00Z INFO RECOVERY_CYCLE_SUCCESS incident_id=state-def456 recovery_cycles=3
2024-01-01T12:14:00Z CRITICAL STATE_TRANSITION incident_id=state-ghi789 from=SAFE_MODE to=RECOVERING reason=recovery_cycles >= 3 owner=recovery_mechanism
2024-01-01T12:17:00Z INFO RECOVERY_CYCLE_SUCCESS incident_id=state-ghi789 recovery_cycles=1
2024-01-01T12:20:00Z INFO RECOVERY_CYCLE_SUCCESS incident_id=state-ghi789 recovery_cycles=2
2024-01-01T12:23:00Z INFO RECOVERY_CYCLE_SUCCESS incident_id=state-ghi789 recovery_cycles=3
2024-01-01T12:23:00Z CRITICAL STATE_TRANSITION incident_id=state-jkl012 from=RECOVERING to=RUNNING reason=recovery completed owner=recovery_mechanism
```

## Запреты

❌ Не увеличивать таймауты  
❌ Не «лечить» sleep  
❌ Не предлагать manual restart  
❌ Не прятать баги флагами  
❌ Не отключать loop guard  

## Финальная Цель

Система должна:
✔ Уметь УМИРАТЬ правильно  
✔ Уметь ВОССТАНАВЛИВАТЬСЯ автоматически  
✔ Иметь воспроизводимый CRITICAL  
✔ Быть наблюдаемой  
✔ Быть управляемой без ручных костылей  

**ЕСЛИ CRITICAL НЕЛЬЗЯ ВЫЗВАТЬ ПО КОМАНДЕ — СИСТЕМА ВНЕ КОНТРОЛЯ.**

