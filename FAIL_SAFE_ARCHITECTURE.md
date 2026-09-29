# FAIL-SAFE ARCHITECTURE - Формальная спецификация

> **Историческое описание сигнального бота (март 2026).** Исполнителей И13/И14/И18, исследовательской
> программы и нынешнего состояния здесь нет — см. `CLAUDE.md`, `docs/TRADER_PLAN.md`. Сигнальная
> торговля с 14.09.2026 выключена. Пометка — аудит 29.09.2026.


**Дата:** 2024-12-19  
**Версия:** 1.0  
**Приоритет:** CRITICAL - Fail-safe строго важнее доступности

---

## 🎯 ЦЕЛЬ

Спроектировать архитектуру, где система **НЕ МОЖЕТ** работать в аналитически небезопасном состоянии.

**Катастрофический отказ определяется как:**
> "Система продолжает работать, пока критический аналитический модуль недоступен или невалиден."

---

## 1️⃣ ФОРМАЛЬНЫЕ СИСТЕМНЫЕ ИНВАРИАНТЫ

### Определение инварианта

**Инвариант** — это логическое условие, которое **ВСЕГДА** должно быть истинным во время выполнения системы. Нарушение инварианта = катастрофический отказ.

### Список инвариантов

#### INV-1: CRITICAL MODULE AVAILABILITY
```
∀ module ∈ CRITICAL_MODULES:
  IF module.unavailable OR module.invalid OR module.timeout
  THEN system_state → SAFE_HALT
```

**Принудительное выполнение:**
- `SystemGuardian` проверяет каждый CRITICAL модуль перед каждым торговым решением
- Если модуль недоступен → немедленный переход в `SAFE_HALT`
- Нет обходных путей

#### INV-2: DECISION CORE AUTHORITY
```
∀ trading_decision:
  MUST pass through DecisionCore.should_i_trade()
  NO component may bypass DecisionCore
  DecisionCore is single source of truth
```

**Принудительное выполнение:**
- `Gatekeeper` проверяет `DecisionCore` перед любой отправкой сигнала
- `SystemGuardian` блокирует любые попытки обхода
- Все решения логируются с `decision_source="DecisionCore"`

#### INV-3: META DECISION BRAIN CRITICALITY
```
IF MetaDecisionBrain is CRITICAL:
  IF MetaDecisionBrain.unavailable OR MetaDecisionBrain.invalid
  THEN trading MUST be blocked
  NO fail-open allowed
```

**Принудительное выполнение:**
- Если `MetaDecisionBrain` помечен как `CRITICAL`, его недоступность → `SAFE_HALT`
- Fail-open поведение запрещено для CRITICAL модулей

#### INV-4: STATE MACHINE CONSISTENCY
```
system_state_machine.state ∈ {RUNNING, DEGRADED, SAFE_HALT, RECOVERY, FATAL}
IF state == SAFE_HALT OR state == FATAL:
  THEN trading_paused == True (enforced)
IF state == FATAL:
  THEN process MUST exit (enforced by FATAL_REAPER)
```

**Принудительное выполнение:**
- `SystemStateMachine` гарантирует консистентность через `sync_to_system_state()`
- `FATAL_REAPER` thread принудительно завершает процесс при `FATAL`

#### INV-5: DATA VALIDITY
```
∀ data used in trading decision:
  MUST be validated (not None, not NaN, not stale)
  IF validation fails:
    THEN data source marked as INVALID
    THEN system_state → DEGRADED (if recoverable) OR SAFE_HALT (if critical)
```

**Принудительное выполнение:**
- `DataValidator` проверяет все входные данные перед использованием
- Невалидные данные → пометка источника как `INVALID`
- Критические невалидные данные → `SAFE_HALT`

#### INV-6: TIMEOUT ENFORCEMENT
```
∀ operation in trading decision path:
  MUST complete within TIMEOUT
  IF timeout exceeded:
    THEN operation marked as FAILED
    THEN module marked as DEGRADED
    THEN system_state → DEGRADED (if recoverable) OR SAFE_HALT (if critical)
```

**Принудительное выполнение:**
- Все операции обёрнуты в `timeout_guard()`
- Превышение таймаута → пометка модуля как `DEGRADED`
- Критические таймауты → `SAFE_HALT`

---

## 2️⃣ КЛАССИФИКАЦИЯ МОДУЛЕЙ

### CRITICAL (Критические)

**Определение:** Модуль, без которого система **НЕ МОЖЕТ** безопасно принимать торговые решения.

**Последствия недоступности:**
- `system_state → SAFE_HALT`
- Торговля полностью заблокирована
- Нет fail-open поведения

**Список CRITICAL модулей:**

1. **DecisionCore**
   - **Роль:** Единая точка принятия решений
   - **Недоступность:** `SAFE_HALT` немедленно
   - **Валидация:** Должен отвечать в течение `DECISION_CORE_TIMEOUT` (5 секунд)

2. **SystemStateMachine**
   - **Роль:** Управление системными состояниями
   - **Недоступность:** `FATAL` (требует restart)
   - **Валидация:** Должен отвечать мгновенно (синхронные операции)

3. **SystemGuardian**
   - **Роль:** Принуждение инвариантов
   - **Недоступность:** `FATAL` (требует restart)
   - **Валидация:** Должен отвечать мгновенно

4. **RiskExposureBrain** (если используется)
   - **Роль:** Расчёт риска и экспозиции
   - **Недоступность:** `SAFE_HALT`
   - **Валидация:** Должен отвечать в течение `BRAIN_TIMEOUT` (3 секунды)

### NON_CRITICAL (Некритические)

**Определение:** Модуль, который может быть недоступен без блокировки торговли.

**Последствия недоступности:**
- Модуль помечается как `DEGRADED`
- Система продолжает работу с ограниченной функциональностью
- Логируется предупреждение

**Список NON_CRITICAL модулей:**

1. **MetaDecisionBrain**
   - **Роль:** Мета-решения о торговле
   - **Недоступность:** Система продолжает работу, но без мета-фильтрации
   - **Валидация:** Опциональная проверка

2. **MarketRegimeBrain**
   - **Роль:** Анализ режима рынка
   - **Недоступность:** Система работает без информации о режиме
   - **Валидация:** Опциональная проверка

3. **CognitiveFilter**
   - **Роль:** Фильтр когнитивных ошибок
   - **Недоступность:** Система работает без когнитивного фильтра
   - **Валидация:** Опциональная проверка

4. **OpportunityAwareness**
   - **Роль:** Отслеживание возможностей
   - **Недоступность:** Система работает без информации о возможностях
   - **Валидация:** Опциональная проверка

5. **PortfolioBrain**
   - **Роль:** Портфельный анализ
   - **Недоступность:** Система работает без портфельного анализа
   - **Валидация:** Опциональная проверка

6. **PositionSizer**
   - **Роль:** Расчёт размера позиции
   - **Недоступность:** Используется дефолтный размер позиции
   - **Валидация:** Опциональная проверка

7. **TelegramBot**
   - **Роль:** Уведомления
   - **Недоступность:** Система работает без уведомлений
   - **Валидация:** Опциональная проверка

### КОНФИГУРИРУЕМАЯ КРИТИЧНОСТЬ

**Принцип:** Критичность модуля может быть изменена через конфигурацию.

**Файл конфигурации:** `module_criticality.json`

```json
{
  "modules": {
    "MetaDecisionBrain": {
      "criticality": "CRITICAL",
      "timeout_seconds": 3.0,
      "fail_safe_behavior": "BLOCK_TRADING"
    },
    "MarketRegimeBrain": {
      "criticality": "NON_CRITICAL",
      "timeout_seconds": 5.0,
      "fail_safe_behavior": "CONTINUE_WITH_DEGRADED"
    }
  }
}
```

---

## 3️⃣ СИСТЕМНЫЕ СОСТОЯНИЯ (State Machine)

### Расширенная State Machine

**Файл:** `core/system_state_machine.py` (расширение существующей)

### Состояния

#### RUNNING
**Описание:** Нормальная работа системы

**Разрешённые действия:**
- Все торговые операции
- Все аналитические операции
- Все уведомления

**Запрещённые действия:**
- Нет ограничений

**Переходы:**
- `RUNNING → DEGRADED`: Обнаружена деградация (некритическая ошибка)
- `RUNNING → SAFE_HALT`: Обнаружена критическая проблема
- `RUNNING → FATAL`: Необратимая ошибка

#### DEGRADED
**Описание:** Система работает с ограниченной функциональностью

**Разрешённые действия:**
- Торговые операции (если DecisionCore доступен)
- Аналитические операции (частично)
- Уведомления о деградации

**Запрещённые действия:**
- Использование недоступных NON_CRITICAL модулей

**Переходы:**
- `DEGRADED → RUNNING`: Восстановление после успешных циклов
- `DEGRADED → SAFE_HALT`: Деградация критического модуля
- `DEGRADED → FATAL`: Необратимая ошибка

#### SAFE_HALT
**Описание:** Торговля полностью заблокирована

**Разрешённые действия:**
- Мониторинг состояния
- Восстановление модулей
- Логирование
- Уведомления о блокировке

**Запрещённые действия:**
- **ЛЮБЫЕ торговые операции**
- Отправка сигналов
- Исполнение сделок

**Переходы:**
- `SAFE_HALT → RECOVERY`: Начало восстановления
- `SAFE_HALT → FATAL`: TTL истёк или необратимая ошибка

**TTL:** 600 секунд (10 минут)
- Если система остаётся в `SAFE_HALT` дольше TTL → `FATAL`

#### RECOVERY
**Описание:** Система восстанавливается после `SAFE_HALT`

**Разрешённые действия:**
- Проверка доступности модулей
- Валидация данных
- Тестовые операции (без торговли)

**Запрещённые действия:**
- **ЛЮБЫЕ торговые операции**
- Отправка сигналов
- Исполнение сделок

**Переходы:**
- `RECOVERY → RUNNING`: Успешное восстановление (3 успешных цикла)
- `RECOVERY → SAFE_HALT`: Ошибка во время восстановления
- `RECOVERY → FATAL`: Необратимая ошибка

#### FATAL
**Описание:** Критическая ошибка, требующая restart

**Разрешённые действия:**
- Логирование
- Уведомления о критической ошибке
- **os._exit(FATAL_EXIT_CODE)** (принудительно)

**Запрещённые действия:**
- **ВСЕ операции** (кроме завершения процесса)

**Переходы:**
- `FATAL → (нет)` - Terminal state

**Принудительное завершение:**
- `FATAL_REAPER` thread принудительно завершает процесс через `os._exit()`
- Нет возможности восстановления из `FATAL`

### Таблица переходов

| From State | To State | Trigger | Owner |
|------------|----------|---------|-------|
| RUNNING | DEGRADED | NON_CRITICAL module unavailable | SystemGuardian |
| RUNNING | SAFE_HALT | CRITICAL module unavailable | SystemGuardian |
| RUNNING | FATAL | Unrecoverable error | ErrorHandler |
| DEGRADED | RUNNING | 3 successful cycles | RecoveryMechanism |
| DEGRADED | SAFE_HALT | CRITICAL module unavailable | SystemGuardian |
| DEGRADED | FATAL | Unrecoverable error | ErrorHandler |
| SAFE_HALT | RECOVERY | All CRITICAL modules available | RecoveryMechanism |
| SAFE_HALT | FATAL | TTL expired (>600s) | TTLGuard |
| RECOVERY | RUNNING | 3 successful cycles | RecoveryMechanism |
| RECOVERY | SAFE_HALT | Recovery failed | RecoveryMechanism |
| RECOVERY | FATAL | Unrecoverable error | ErrorHandler |

---

## 4️⃣ FAIL-SAFE BY DEFAULT

### Принцип

**"Если сомневаешься — блокируй торговлю"**

### Правила Fail-Safe

#### RULE-1: CRITICAL MODULE FAILURE
```
IF critical_module.unavailable OR critical_module.invalid OR critical_module.timeout:
  THEN system_state → SAFE_HALT
  THEN trading_paused = True (enforced)
  THEN log CRITICAL alert
```

#### RULE-2: DATA VALIDATION FAILURE
```
IF critical_data.invalid OR critical_data.stale:
  THEN data_source.marked = INVALID
  THEN IF data_source is CRITICAL:
    THEN system_state → SAFE_HALT
  ELSE:
    THEN system_state → DEGRADED
```

#### RULE-3: TIMEOUT ENFORCEMENT
```
IF operation.timeout_exceeded:
  THEN operation.marked = FAILED
  THEN module.marked = DEGRADED
  THEN IF module is CRITICAL:
    THEN system_state → SAFE_HALT
  ELSE:
    THEN system_state → DEGRADED
```

#### RULE-4: EXCEPTION HANDLING
```
IF exception in trading decision path:
  THEN log ERROR with full context
  THEN IF exception is recoverable:
    THEN system_state → DEGRADED
  ELSE:
    THEN system_state → SAFE_HALT
```

#### RULE-5: NO FAIL-OPEN FOR CRITICAL
```
IF critical_module:
  THEN fail_open = FORBIDDEN
  THEN fail_safe = REQUIRED
  THEN IF module unavailable:
    THEN trading MUST be blocked
```

---

## 5️⃣ УСИЛЕНИЕ DECISION CORE

### Decision Core как Single Source of Truth

**Принцип:** Все торговые решения проходят через `DecisionCore.should_i_trade()`

### Архитектура

```
┌─────────────────────────────────────────┐
│         SystemGuardian                   │
│  (Enforces Decision Core Authority)      │
└─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────┐
│         DecisionCore                     │
│  (Single Source of Truth)                │
│  - should_i_trade()                     │
│  - get_risk_status()                     │
│  - get_full_context()                    │
└─────────────────────────────────────────┘
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
┌──────────────┐      ┌──────────────────┐
│  Gatekeeper  │      │  Other Consumers │
│  (enforced)  │      │  (enforced)      │
└──────────────┘      └──────────────────┘
```

### Принудительные проверки

1. **Gatekeeper проверка:**
   ```python
   def send_signal(...):
       # INV-2: Decision Core Authority
       decision = self.decision_core.should_i_trade(symbol, system_state)
       if not decision.can_trade:
           return  # Early exit - NO bypass
   ```

2. **SystemGuardian проверка:**
   ```python
   def enforce_decision_core_authority():
       # Проверяем, что все решения проходят через DecisionCore
       if any_bypass_detected:
           system_state → SAFE_HALT
   ```

3. **Логирование всех решений:**
   ```python
   # Все решения логируются с decision_source="DecisionCore"
   decision_trace.log_decision(
       decision_source="DecisionCore",
       allow_trading=decision.can_trade,
       reason=decision.reason
   )
   ```

---

## 6️⃣ ГЛОБАЛЬНЫЙ СЛОЙ ПОЛИТИКИ / ПРИНУЖДЕНИЯ

### SystemGuardian

**Файл:** `core/system_guardian.py`

**Роль:** Принуждение всех инвариантов и политик

### Компоненты

#### 1. ModuleHealthMonitor
**Роль:** Мониторинг здоровья всех модулей

```python
class ModuleHealthMonitor:
    def check_module_health(self, module: Module) -> ModuleHealth:
        """
        Проверяет здоровье модуля:
        - Доступность
        - Валидность данных
        - Таймауты
        - Последний heartbeat
        """
        pass
    
    def mark_module_degraded(self, module: Module, reason: str):
        """Помечает модуль как DEGRADED"""
        pass
    
    def mark_module_critical_failure(self, module: Module, reason: str):
        """Помечает модуль как CRITICAL FAILURE → SAFE_HALT"""
        pass
```

#### 2. InvariantEnforcer
**Роль:** Принуждение всех инвариантов

```python
class InvariantEnforcer:
    def check_all_invariants(self) -> List[InvariantViolation]:
        """
        Проверяет все инварианты:
        - INV-1: CRITICAL MODULE AVAILABILITY
        - INV-2: DECISION CORE AUTHORITY
        - INV-3: META DECISION BRAIN CRITICALITY
        - INV-4: STATE MACHINE CONSISTENCY
        - INV-5: DATA VALIDITY
        - INV-6: TIMEOUT ENFORCEMENT
        """
        pass
    
    def enforce_invariant(self, invariant: Invariant):
        """Принуждает выполнение инварианта"""
        pass
```

#### 3. PolicyEnforcer
**Роль:** Принуждение политик fail-safe

```python
class PolicyEnforcer:
    def enforce_fail_safe_policy(self, module: Module, failure: Failure):
        """
        Применяет fail-safe политику:
        - CRITICAL module failure → SAFE_HALT
        - NON_CRITICAL module failure → DEGRADED
        - Data validation failure → блокировка использования
        - Timeout → пометка модуля как DEGRADED
        """
        pass
```

#### 4. TradingGate
**Роль:** Финальная проверка перед торговлей

```python
class TradingGate:
    def can_trade(self) -> TradingPermission:
        """
        Финальная проверка перед торговлей:
        - System state (RUNNING only)
        - All CRITICAL modules available
        - DecisionCore available
        - No invariant violations
        """
        pass
    
    def block_trading(self, reason: str):
        """Принудительно блокирует торговлю"""
        system_state → SAFE_HALT
```

### Интеграция SystemGuardian

```python
# В runner.py - перед каждым торговым циклом
async def market_analysis_loop():
    # 1. SystemGuardian проверяет все инварианты
    violations = await system_guardian.check_all_invariants()
    if violations:
        await system_guardian.handle_violations(violations)
        return  # Early exit
    
    # 2. Проверка здоровья модулей
    health_status = await system_guardian.check_module_health()
    if health_status.has_critical_failures:
        system_state → SAFE_HALT
        return
    
    # 3. TradingGate проверка
    permission = await trading_gate.can_trade()
    if not permission.allowed:
        return  # Early exit
    
    # 4. Нормальная работа
    # ...
```

---

## 7️⃣ РЕАЛИЗАЦИЯ

### Новые файлы

1. **`core/system_guardian.py`** - SystemGuardian
2. **`core/module_registry.py`** - Реестр модулей с критичностью
3. **`core/data_validator.py`** - Валидация данных
4. **`core/timeout_guard.py`** - Защита от таймаутов
5. **`config/module_criticality.json`** - Конфигурация критичности

### Модификации существующих файлов

1. **`core/system_state_machine.py`** - Расширение состояний
2. **`core/decision_core.py`** - Усиление авторитета
3. **`execution/gatekeeper.py`** - Интеграция SystemGuardian
4. **`runner.py`** - Интеграция проверок перед каждым циклом

---

## 8️⃣ ОБОСНОВАНИЕ FAIL-SAFE

### Почему система теперь fail-safe по конструкции

1. **Формальные инварианты:**
   - Все инварианты явно определены и принудительно проверяются
   - Нарушение инварианта → немедленная блокировка торговли

2. **Классификация модулей:**
   - CRITICAL модули → fail-safe поведение (блокировка при недоступности)
   - NON_CRITICAL модули → graceful degradation

3. **State Machine:**
   - Явные состояния с чёткими правилами переходов
   - `SAFE_HALT` и `FATAL` гарантируют блокировку торговли
   - TTL для `SAFE_HALT` предотвращает бесконечное зависание

4. **Fail-Safe по умолчанию:**
   - Все правила fail-safe явно определены
   - Нет fail-open поведения для CRITICAL модулей
   - "Если сомневаешься — блокируй"

5. **Decision Core Authority:**
   - Единая точка принятия решений
   - Нет обходных путей
   - Все решения логируются

6. **SystemGuardian:**
   - Глобальный слой принуждения
   - Проверка всех инвариантов перед каждым циклом
   - TradingGate как финальная проверка

### Гарантии

✅ **Система НЕ МОЖЕТ торговать, если:**
- CRITICAL модуль недоступен
- Данные невалидны
- Инвариант нарушен
- System state != RUNNING
- DecisionCore недоступен

✅ **Система АВТОМАТИЧЕСКИ блокирует торговлю при:**
- Обнаружении критической проблемы
- Превышении таймаута
- Нарушении инварианта
- Невалидных данных

✅ **Система ПРИНУДИТЕЛЬНО завершается при:**
- FATAL состоянии
- Необратимой ошибке
- Истечении TTL для SAFE_HALT

---

## 9️⃣ МИГРАЦИЯ

### Этапы внедрения

1. **Этап 1: Инфраструктура**
   - Создать `SystemGuardian`
   - Создать `ModuleRegistry`
   - Расширить `SystemStateMachine`

2. **Этап 2: Классификация**
   - Классифицировать все модули
   - Создать `module_criticality.json`
   - Обновить конфигурацию

3. **Этап 3: Интеграция**
   - Интегрировать `SystemGuardian` в `runner.py`
   - Обновить `Gatekeeper` для проверок
   - Обновить `DecisionCore` для усиления авторитета

4. **Этап 4: Тестирование**
   - Тесты инвариантов
   - Тесты fail-safe поведения
   - Тесты state machine переходов

5. **Этап 5: Мониторинг**
   - Логирование всех проверок
   - Алерты при нарушениях
   - Метрики здоровья системы

---

## 🔟 ЗАКЛЮЧЕНИЕ

Система теперь **fail-safe по конструкции** благодаря:

1. ✅ Формальным инвариантам с принудительным выполнением
2. ✅ Классификации модулей (CRITICAL/NON_CRITICAL)
3. ✅ Расширенной state machine с явными состояниями
4. ✅ Fail-safe правилам по умолчанию
5. ✅ Усиленному авторитету Decision Core
6. ✅ Глобальному слою принуждения (SystemGuardian)

**Катастрофический отказ невозможен**, так как система автоматически блокирует торговлю при любом нарушении инварианта или недоступности критического модуля.

