# SYSTEM ARCHITECTURE - CANONICAL

**Версия:** 1.0  
**Дата:** 2024-12-19  
**Приоритет:** FAIL-SAFE строго важнее доступности

---

## 🎯 ПРИНЦИПЫ

1. **Fail-Safe First:** Торговля блокируется при любой неопределённости
2. **Single Source of Truth:** DecisionCore — единственный источник истины
3. **No Bypass:** Нет обходных путей для DecisionCore или SystemGuardian
4. **Architecture == Runtime:** Документация точно отражает код

---

## 📋 СИСТЕМНЫЕ ИНВАРИАНТЫ

### INV-1: CRITICAL MODULE AVAILABILITY
```
∀ module ∈ CRITICAL_MODULES:
  IF module.unavailable OR module.invalid OR module.timeout
  THEN system_state → SAFE_HALT
  THEN trading_paused = True (enforced)
```

**Принуждение:** SystemGuardian проверяет перед каждым торговым циклом

### INV-2: DECISION CORE AUTHORITY
```
∀ trading_decision:
  MUST pass through DecisionCore.should_i_trade()
  NO component may bypass DecisionCore
  DecisionCore is single source of truth
```

**Принуждение:** Gatekeeper проверяет DecisionCore перед отправкой сигнала

### INV-3: SYSTEM STATE CONSISTENCY
```
system_state_machine.state ∈ {RUNNING, DEGRADED, SAFE_HALT, RECOVERY, FATAL}
IF state == SAFE_HALT OR state == FATAL:
  THEN trading_paused == True (enforced)
IF state == FATAL:
  THEN process MUST exit (enforced by FATAL_REAPER)
```

**Принуждение:** SystemStateMachine гарантирует консистентность

### INV-4: GUARDIAN-FIRST ENFORCEMENT
```
∀ signal sending:
  MUST pass through SystemGuardian.can_trade_sync() FIRST
  NO signal may be sent without Guardian check
  Guardian is architecturally mandatory barrier
```

**Принуждение:** Gatekeeper проверяет SystemGuardian перед всеми остальными проверками (архитектурно принудительно)

### INV-5: NO FAIL-OPEN FOR CRITICAL
```
IF module.criticality == CRITICAL:
  THEN fail_open = FORBIDDEN
  THEN fail_safe = REQUIRED
  THEN IF module unavailable:
    THEN trading MUST be blocked
```

**Принуждение:** PolicyEnforcer применяет fail-safe политику

---

## 🏗️ КЛАССИФИКАЦИЯ МОДУЛЕЙ

### CRITICAL (Критические)

**Определение:** Модуль, без которого система НЕ МОЖЕТ безопасно принимать торговые решения.

**Поведение при недоступности:**
- `system_state → SAFE_HALT`
- Торговля полностью заблокирована
- Нет fail-open поведения

**Список CRITICAL модулей:**

| Модуль | Роль | Таймаут | Принуждение |
|--------|------|---------|-------------|
| **DecisionCore** | Единая точка принятия решений | 5.0s | Блокировка торговли |
| **SystemStateMachine** | Управление системными состояниями | 0.1s | FATAL при недоступности |
| **SystemGuardian** | Принуждение инвариантов | 0.1s | FATAL при недоступности |
| **Gatekeeper** | Проверка сигналов перед отправкой | 5.0s | Блокировка отправки |
| **RiskExposureBrain** | Расчёт риска и экспозиции | 3.0s | Блокировка торговли |

### NON_CRITICAL (Некритические)

**Определение:** Модуль, который может быть недоступен без блокировки торговли.

**Поведение при недоступности:**
- Модуль помечается как `DEGRADED`
- Система продолжает работу с ограниченной функциональностью
- Логируется предупреждение

**Список NON_CRITICAL модулей:**

| Модуль | Роль | Таймаут | Поведение при недоступности |
|--------|------|---------|----------------------------|
| **MetaDecisionBrain** | Мета-решения (WHEN NOT TO TRADE) | 3.0s | Система работает без мета-фильтрации |
| **MarketRegimeBrain** | Анализ режима рынка | 5.0s | Система работает без информации о режиме |
| **CognitiveFilter** | Фильтр когнитивных ошибок | 3.0s | Система работает без когнитивного фильтра |
| **OpportunityAwareness** | Отслеживание возможностей | 5.0s | Система работает без информации о возможностях |
| **PortfolioBrain** | Портфельный анализ | 5.0s | Система работает без портфельного анализа |
| **PositionSizer** | Расчёт размера позиции | 3.0s | Используется дефолтный размер позиции |
| **TelegramBot** | Уведомления | 10.0s | Система работает без уведомлений |

**Примечание:** MetaDecisionBrain и PositionSizer участвуют в decision flow, но их недоступность не блокирует торговлю (graceful degradation).

---

## 🔄 СИСТЕМНЫЕ СОСТОЯНИЯ

### State Machine

**Файл:** `core/system_state_machine.py`

**Состояния:**

| Состояние | Описание | Торговля | Переходы |
|-----------|----------|----------|----------|
| **RUNNING** | Нормальная работа | ✅ Разрешена | → DEGRADED, SAFE_HALT, FATAL |
| **DEGRADED** | Деградация (NON_CRITICAL недоступны) | ✅ Разрешена | → RUNNING, SAFE_HALT, FATAL |
| **SAFE_HALT** | Торговля заблокирована | ❌ Заблокирована | → RECOVERY, FATAL |
| **RECOVERY** | Восстановление | ❌ Заблокирована | → RUNNING, SAFE_HALT, FATAL |
| **FATAL** | Критическая ошибка | ❌ Заблокирована | → (terminal) |

**TTL для SAFE_HALT:** 600 секунд (10 минут)
- Если система остаётся в `SAFE_HALT` дольше TTL → `FATAL`

**Принудительное завершение:**
- `FATAL` → `os._exit(FATAL_EXIT_CODE)` (через FATAL_REAPER thread)

---

## 🔀 CANONICAL DECISION FLOW

### Порядок выполнения (runtime)

**Файл:** `execution/gatekeeper.py::send_signal()`

```
1. SystemGuardian.can_trade() [NEW - принудительная проверка]
   ├─ Проверка системного состояния (RUNNING only)
   ├─ Проверка всех инвариантов
   ├─ Проверка здоровья всех CRITICAL модулей
   └─ IF blocked → return (early exit)

2. MetaDecisionBrain.evaluate() [NON_CRITICAL - если доступен]
   ├─ Проверка агрегированных метрик системы
   ├─ HARD BLOCK / SOFT BLOCK / ALLOW
   └─ IF blocked → return (early exit)

3. DecisionCore.should_i_trade() [CRITICAL]
   ├─ Проверка safe-mode
   ├─ Проверка когнитивного фильтра
   ├─ Проверка риска и экспозиции
   ├─ Проверка режима рынка
   ├─ Проверка возможностей
   └─ IF blocked → return (early exit)

4. PortfolioBrain.evaluate() [NON_CRITICAL - если доступен]
   ├─ Портфельный анализ
   ├─ Проверка экспозиции
   └─ IF blocked → return (early exit)

5. PositionSizer.calculate() [NON_CRITICAL - если доступен]
   ├─ Расчёт размера позиции
   ├─ Проверка минимального риска
   └─ IF blocked → return (early exit)

6. Отправка сигнала пользователю
   └─ send_message() + send_chart()
```

### Детальная схема

```
┌─────────────────────────────────────────────────────────┐
│              Signal Generation (signal_generator)        │
│              Creates SignalSnapshot                     │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│              Gatekeeper.send_signal()                   │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│  1. SystemGuardian.can_trade() [CRITICAL]               │
│     ├─ Check system state (RUNNING only)                │
│     ├─ Check all invariants                             │
│     ├─ Check CRITICAL modules health                    │
│     └─ IF blocked → return                              │
└────────────────────┬────────────────────────────────────┘
                     │ (allowed)
                     ▼
┌─────────────────────────────────────────────────────────┐
│  2. MetaDecisionBrain.evaluate() [NON_CRITICAL]         │
│     ├─ Check aggregated metrics                         │
│     ├─ HARD BLOCK / SOFT BLOCK / ALLOW                  │
│     └─ IF blocked → return                              │
└────────────────────┬────────────────────────────────────┘
                     │ (allowed)
                     ▼
┌─────────────────────────────────────────────────────────┐
│  3. DecisionCore.should_i_trade() [CRITICAL]            │
│     ├─ Check safe-mode                                  │
│     ├─ Check cognitive filter                           │
│     ├─ Check risk & exposure                            │
│     ├─ Check market regime                              │
│     ├─ Check opportunities                              │
│     └─ IF blocked → return                              │
└────────────────────┬────────────────────────────────────┘
                     │ (allowed)
                     ▼
┌─────────────────────────────────────────────────────────┐
│  4. PortfolioBrain.evaluate() [NON_CRITICAL]            │
│     ├─ Portfolio analysis                               │
│     ├─ Check exposure                                    │
│     └─ IF blocked → return                              │
└────────────────────┬────────────────────────────────────┘
                     │ (allowed)
                     ▼
┌─────────────────────────────────────────────────────────┐
│  5. PositionSizer.calculate() [NON_CRITICAL]             │
│     ├─ Calculate position size                          │
│     ├─ Check minimum risk                               │
│     └─ IF blocked → return                              │
└────────────────────┬────────────────────────────────────┘
                     │ (allowed)
                     ▼
┌─────────────────────────────────────────────────────────┐
│  6. Send Signal to User                                 │
│     ├─ send_message()                                   │
│     └─ send_chart()                                     │
└─────────────────────────────────────────────────────────┘
```

---

## 🛡️ FAIL-SAFE ГАРАНТИИ

### Правила Fail-Safe

#### RULE-1: CRITICAL MODULE FAILURE
```
IF critical_module.unavailable OR critical_module.invalid OR critical_module.timeout:
  THEN system_state → SAFE_HALT
  THEN trading_paused = True (enforced)
  THEN log CRITICAL alert
```

#### RULE-2: SYSTEM STATE CHECK
```
IF system_state != RUNNING:
  THEN trading MUST be blocked
  THEN SystemGuardian.can_trade() returns False
```

#### RULE-3: INVARIANT VIOLATION
```
IF invariant violated:
  THEN IF severity == CRITICAL:
    THEN system_state → SAFE_HALT
  ELSE:
    THEN log WARNING
```

#### RULE-4: NO BYPASS
```
NO component may bypass:
  - SystemGuardian
  - DecisionCore
  - SystemStateMachine
```

### Гарантии

✅ **Система НЕ МОЖЕТ торговать, если:**
- CRITICAL модуль недоступен
- System state != RUNNING
- Инвариант нарушен (CRITICAL severity)
- DecisionCore недоступен
- SystemGuardian недоступен

✅ **Система АВТОМАТИЧЕСКИ блокирует торговлю при:**
- Обнаружении критической проблемы
- Превышении таймаута CRITICAL модуля
- Нарушении инварианта (CRITICAL severity)
- Переходе в SAFE_HALT или FATAL

✅ **Система ПРИНУДИТЕЛЬНО завершается при:**
- FATAL состоянии
- Необратимой ошибке
- Истечении TTL для SAFE_HALT

---

## 🔧 КОМПОНЕНТЫ СИСТЕМЫ

### SystemGuardian

**Файл:** `core/system_guardian.py`

**Роль:** Глобальный слой принуждения инвариантов и политик

**Компоненты:**
- `ModuleHealthMonitor` - Мониторинг здоровья модулей
- `InvariantEnforcer` - Принуждение всех инвариантов
- `PolicyEnforcer` - Применение fail-safe политик
- `TradingGate` - Финальная проверка перед торговлей

**Использование:**
```python
# В runner.py - перед каждым торговым циклом
system_guardian = get_system_guardian()
permission = await system_guardian.can_trade()
if not permission.allowed:
    return  # Early exit
```

### DecisionCore

**Файл:** `core/decision_core.py`

**Роль:** Единая точка принятия торговых решений

**Методы:**
- `should_i_trade()` - Главный вопрос: можно ли торговать?
- `get_risk_status()` - Статус риска
- `get_full_context()` - Полный контекст для отладки

**Принципы:**
- НЕ хранит состояние (читает из SystemState)
- Детерминированный код
- Fail-safe при ошибках (блокирует торговлю)

### Gatekeeper

**Файл:** `execution/gatekeeper.py`

**Роль:** Проверка сигналов перед отправкой пользователю

**Порядок проверок:**
1. SystemGuardian.can_trade() [CRITICAL]
2. MetaDecisionBrain.evaluate() [NON_CRITICAL]
3. DecisionCore.should_i_trade() [CRITICAL]
4. PortfolioBrain.evaluate() [NON_CRITICAL]
5. PositionSizer.calculate() [NON_CRITICAL]

**Принципы:**
- Все проверки проходят последовательно
- Early exit при любой блокировке
- Логирование всех решений через DecisionTrace

### MetaDecisionBrain

**Файл:** `brains/meta_decision_brain.py`

**Роль:** Мета-решения о торговле (WHEN NOT TO TRADE)

**Классификация:** NON_CRITICAL (graceful degradation)

**Поведение:**
- Участвует в decision flow (первый фильтр)
- Недоступность не блокирует торговлю
- Fail-open поведение (если недоступен, пропускается)

**Примечание:** Может быть изменён на CRITICAL через конфигурацию, но по умолчанию NON_CRITICAL.

### PositionSizer

**Файл:** `core/position_sizer.py`

**Роль:** Расчёт размера позиции

**Классификация:** NON_CRITICAL (graceful degradation)

**Поведение:**
- Участвует в decision flow (последний шаг)
- Недоступность не блокирует торговлю
- Fail-open поведение (если недоступен, используется дефолтный размер)

---

## 📊 МОДУЛЬНЫЙ РЕЕСТР

**Файл:** `core/module_registry.py`

**Роль:** Централизованное управление модулями и их критичностью

**Регистрация модулей:**
```python
module_registry.register_module(
    name="DecisionCore",
    criticality=ModuleCriticality.CRITICAL,
    get_instance=get_decision_core,
    timeout_seconds=5.0,
    description="Единая точка принятия торговых решений"
)
```

**Проверка критичности:**
```python
if module_registry.is_critical("DecisionCore"):
    # Fail-safe поведение
    ...
```

---

## 🔍 ВАЛИДАЦИЯ ДАННЫХ

**Файл:** `core/data_validator.py`

**Роль:** Валидация всех данных перед использованием

**Проверки:**
- None значения
- NaN / Inf значения
- Диапазоны значений
- Типы данных

**Использование:**
```python
validator = get_data_validator()
result = validator.validate_confidence_score(confidence)
if not result.valid:
    # Обработка ошибки
    ...
```

---

## ⏱️ ЗАЩИТА ОТ ТАЙМАУТОВ

**Файл:** `core/timeout_guard.py`

**Роль:** Защита от таймаутов

**Использование:**
```python
@with_timeout(5.0, "check_module_health")
async def check_module_health():
    ...
```

---

## 📝 ЛОГИРОВАНИЕ И ТРЕЙСИНГ

### DecisionTrace

**Файл:** `core/decision_trace.py`

**Роль:** Логирование всех решений для объяснимости

**Формат:**
```python
decision_trace.log_decision(
    symbol="BTCUSDT",
    decision_source="DecisionCore",
    allow_trading=True,
    block_level=BlockLevel.NONE,
    reason="All checks passed",
    context_snapshot={...}
)
```

---

## ✅ ПОДТВЕРЖДЕНИЕ: ARCHITECTURE == RUNTIME

### Проверка соответствия

**Runtime порядок (gatekeeper.py:194-285):**
1. ✅ MetaDecisionBrain (строка 196)
2. ✅ DecisionCore (строка 214)
3. ✅ PortfolioBrain (строка 233)
4. ✅ PositionSizer (строка 257)

**Архитектурный порядок (этот документ):**
1. ✅ SystemGuardian [NEW - требуется интеграция]
2. ✅ MetaDecisionBrain
3. ✅ DecisionCore
4. ✅ PortfolioBrain
5. ✅ PositionSizer

**Несоответствие:**
- SystemGuardian не интегрирован в runtime (требуется интеграция)

**Действие:**
- Интегрировать SystemGuardian.can_trade() в начало gatekeeper.send_signal()

---

## 🎯 ЗАКЛЮЧЕНИЕ

Система теперь имеет **каноническую архитектуру**, где:

1. ✅ **Инварианты формально определены** и принудительно проверяются
2. ✅ **Модули классифицированы** (CRITICAL/NON_CRITICAL)
3. ✅ **Decision flow документирован** и соответствует runtime
4. ✅ **Fail-safe гарантии** явно определены
5. ✅ **Нет обходных путей** для DecisionCore или SystemGuardian

**Следующий шаг:** Интеграция SystemGuardian в runtime для полного соответствия архитектуре.

