# META DECISION BRAIN - АРХИТЕКТУРА

**Дата:** 2024-12-19  
**Последнее обновление:** 2024-12-19  
**Задача:** Спроектировать и реализовать MetaDecisionBrain — верхнеуровневый модуль для решения WHEN NOT TO TRADE

---

## ✅ ВЫПОЛНЕНО

### 1. Создан Meta Decision Brain

**Файл:** `brains/meta_decision_brain.py`

**Класс:** `MetaDecisionBrain`

**Принцип:**
- MetaDecisionBrain НЕ работает с рынком напрямую
- Он работает ТОЛЬКО с агрегированными метриками системы
- Это мета-уровень принятия решений
- Явные переходы состояний через методы `_transition_to_*`
- Детерминированный код без состояния

---

## 📋 СТРУКТУРА

### SystemHealthStatus (enum)

```python
class SystemHealthStatus(str, Enum):
    OK = "OK"
    DEGRADED = "DEGRADED"
```

### BlockLevel (enum)

```python
class BlockLevel(str, Enum):
    HARD = "HARD"  # Жёсткая блокировка
    SOFT = "SOFT"  # Мягкая блокировка
```

### TimeContext (enum)

```python
class TimeContext(str, Enum):
    SESSION_START = "SESSION_START"
    SESSION_MID = "SESSION_MID"
    SESSION_END = "SESSION_END"
    AFTER_HOURS = "AFTER_HOURS"
    UNKNOWN = "UNKNOWN"
```

### DecisionState (enum)

```python
class DecisionState(str, Enum):
    """
    Явные состояния решения о торговле.
    Используется для явного определения текущего состояния системы
    и явных переходов между состояниями.
    """
    ALLOW = "ALLOW"  # Торговля разрешена
    HARD_BLOCK = "HARD_BLOCK"  # Жёсткая блокировка торговли
    SOFT_BLOCK = "SOFT_BLOCK"  # Мягкая блокировка торговли
```

### MetaDecisionResult (dataclass)

```python
@dataclass
class MetaDecisionResult:
    allow_trading: bool
    reason: str  # Explainable reason
    block_level: Optional[BlockLevel] = None
    cooldown_minutes: int = 0
```

---

## 📥 INPUT

Метод `evaluate()` принимает:

- `market_regime: Optional[MarketRegime]` - Режим рынка
- `confidence_score: float` - Средняя уверенность системы (0.0 - 1.0)
- `entropy_score: float` - Средняя энтропия системы (0.0 - 1.0)
- `portfolio_exposure: float` - Экспозиция портфеля (0.0 - 1.0)
- `recent_outcomes: Optional[List[float]]` - Список последних результатов
- `signals_count_recent: int` - Количество сигналов за период
- `system_health: SystemHealthStatus` - Состояние здоровья системы
- `time_context: TimeContext` - Контекст времени

**Нормализация входных данных:**
- Все значения `confidence_score`, `entropy_score`, `portfolio_exposure` автоматически нормализуются в диапазон [0.0, 1.0]
- Метод `_normalize_inputs()` обеспечивает корректность данных перед обработкой

---

## 📤 OUTPUT

Возвращает `MetaDecisionResult`:

- `allow_trading: bool` - Разрешена ли торговля
- `reason: str` - Объяснение решения (explainable)
- `block_level: Optional[BlockLevel]` - Уровень блокировки (HARD/SOFT)
- `cooldown_minutes: int` - Время ожидания в минутах

---

## 🔹 HARD BLOCK (Жёсткая блокировка)

**Приоритет:** 1 (высший) - проверяется первым

Если ЛЮБОЕ выполнено → **HARD BLOCK** (явный переход через `_transition_to_hard_block()`):

1. **entropy > 0.7 AND confidence < 0.4**
   - Высокая энтропия + низкая уверенность = система неопределённа
   - Cooldown: 30 минут (`HARD_BLOCK_COOLDOWN_MINUTES`)

2. **portfolio_exposure > 0.8**
   - Экспозиция превышает безопасный лимит (80%)
   - Cooldown: 30 минут (`HARD_BLOCK_COOLDOWN_MINUTES`)

3. **system_health == DEGRADED**
   - Система испытывает проблемы
   - Cooldown: 30 минут (`HARD_BLOCK_COOLDOWN_MINUTES`)

**Метод проверки:** `_evaluate_hard_block_transition()` → `_check_hard_block_conditions()`

---

## 🔹 SOFT BLOCK (Мягкая блокировка)

**Приоритет:** 2 (средний) - проверяется вторым, только если HARD BLOCK не сработал

Если выполнено → **SOFT BLOCK** (явный переход через `_transition_to_soft_block()`):

1. **signals_count_recent > 10**
   - Слишком много сигналов за период (риск overtrading)
   - Cooldown: 15 минут (`SOFT_BLOCK_COOLDOWN_OVERTRADING`)

2. **confidence средний, entropy средний**
   - Средние значения (0.4-0.6) указывают на неопределённость
   - Если exposure > 0.5 → Cooldown: 10 минут (`SOFT_BLOCK_COOLDOWN_UNCERTAINTY`)

3. **Плохие результаты в recent_outcomes**
   - Больше 60% отрицательных результатов
   - Cooldown: 20 минут (`SOFT_BLOCK_COOLDOWN_BAD_OUTCOMES`)

4. **Высокая экспозиция с низкой уверенностью**
   - exposure > 0.6 AND confidence < 0.5
   - Cooldown: 15 минут (`SOFT_BLOCK_COOLDOWN_HIGH_EXPOSURE`)

5. **Конец сессии с высокой энтропией**
   - SESSION_END AND entropy > 0.6
   - Cooldown: 5 минут (`SOFT_BLOCK_COOLDOWN_SESSION_END`)

**Метод проверки:** `_evaluate_soft_block_transition()` → `_check_soft_block_conditions()`

---

## 🔹 ALLOW (Разрешение)

**Приоритет:** 3 (низший) - состояние по умолчанию

Торговля разрешена только если:

- НЕТ ни одного HARD BLOCK условия
- НЕТ ни одного SOFT BLOCK условия

**Метод перехода:** `_transition_to_allow()` - явный переход в состояние ALLOW

**Cooldown:** 0 минут (`ALLOW_COOLDOWN_MINUTES`)

---

## 🛡️ АРХИТЕКТУРНЫЕ ПРИНЦИПЫ

### 1. Чистый детерминированный код
- ✅ Нет singleton
- ✅ Нет глобальных состояний
- ✅ Все методы детерминированы
- ✅ Одинаковые входные данные → одинаковый результат
- ✅ Явные переходы состояний через методы `_transition_to_*`

### 2. Лёгкий и быстрый
- ✅ Нет внешних зависимостей (кроме core.decision_core)
- ✅ Минимальные вычисления
- ✅ Быстрая проверка условий
- ✅ Нормализация входных данных через `_normalize_inputs()`

### 3. Явные переходы состояний
- ✅ Явные константы приоритетов: `CHECK_PRIORITY_HARD_BLOCK`, `CHECK_PRIORITY_SOFT_BLOCK`, `CHECK_PRIORITY_ALLOW`
- ✅ Явные константы cooldown: `HARD_BLOCK_COOLDOWN_MINUTES`, `SOFT_BLOCK_COOLDOWN_*`
- ✅ Явные методы переходов: `_transition_to_hard_block()`, `_transition_to_soft_block()`, `_transition_to_allow()`
- ✅ Порядок проверок: HARD_BLOCK (приоритет 1) → SOFT_BLOCK (приоритет 2) → ALLOW (приоритет 3)

### 4. Explainable
- ✅ Каждое решение имеет объяснение (reason)
- ✅ Метод `explain_decision()` для подробного объяснения
- ✅ Понятные причины блокировки

### 5. Точка расширения
- ✅ Класс `MetaDecisionBrainExtension` для будущих расширений
- ✅ Можно добавлять новые проверки без изменения основного класса

---

## ⚙️ ВНУТРЕННЯЯ АРХИТЕКТУРА

### Порядок проверок (явный приоритет)

```python
# Константы приоритетов
CHECK_PRIORITY_HARD_BLOCK = 1  # Высший приоритет
CHECK_PRIORITY_SOFT_BLOCK = 2  # Средний приоритет
CHECK_PRIORITY_ALLOW = 3       # Низший приоритет (по умолчанию)
```

**Последовательность выполнения в `evaluate()`:**

1. **Нормализация входных данных** (`_normalize_inputs()`)
   - `confidence_score` → [0.0, 1.0]
   - `entropy_score` → [0.0, 1.0]
   - `portfolio_exposure` → [0.0, 1.0]

2. **Проверка HARD BLOCK** (приоритет 1)
   - `_evaluate_hard_block_transition()` → `_check_hard_block_conditions()`
   - Если условие выполнено → `_transition_to_hard_block()` → возврат результата

3. **Проверка SOFT BLOCK** (приоритет 2, только если HARD BLOCK не сработал)
   - `_evaluate_soft_block_transition()` → `_check_soft_block_conditions()`
   - Если условие выполнено → `_transition_to_soft_block()` → возврат результата

4. **Переход в ALLOW** (приоритет 3, по умолчанию)
   - `_transition_to_allow()` → возврат результата

### Константы Cooldown

```python
# HARD BLOCK cooldown
HARD_BLOCK_COOLDOWN_MINUTES = 30

# SOFT BLOCK cooldown (разные для разных условий)
SOFT_BLOCK_COOLDOWN_OVERTRADING = 15      # Слишком много сигналов
SOFT_BLOCK_COOLDOWN_UNCERTAINTY = 10      # Средние значения confidence/entropy
SOFT_BLOCK_COOLDOWN_BAD_OUTCOMES = 20    # Плохие результаты
SOFT_BLOCK_COOLDOWN_HIGH_EXPOSURE = 15   # Высокая экспозиция + низкая уверенность
SOFT_BLOCK_COOLDOWN_SESSION_END = 5       # Конец сессии + высокая энтропия

# ALLOW cooldown
ALLOW_COOLDOWN_MINUTES = 0
```

### Методы переходов состояний

```python
def _transition_to_hard_block(self, reason: str) -> MetaDecisionResult:
    """Явный переход в состояние HARD_BLOCK"""
    return MetaDecisionResult(
        allow_trading=False,
        reason=reason,
        block_level=BlockLevel.HARD,
        cooldown_minutes=self.HARD_BLOCK_COOLDOWN_MINUTES
    )

def _transition_to_soft_block(self, reason: str, cooldown_minutes: int) -> MetaDecisionResult:
    """Явный переход в состояние SOFT_BLOCK"""
    return MetaDecisionResult(
        allow_trading=False,
        reason=reason,
        block_level=BlockLevel.SOFT,
        cooldown_minutes=cooldown_minutes
    )

def _transition_to_allow(self) -> MetaDecisionResult:
    """Явный переход в состояние ALLOW"""
    return MetaDecisionResult(
        allow_trading=True,
        reason="No blocking conditions detected. System is ready for trading.",
        block_level=None,
        cooldown_minutes=self.ALLOW_COOLDOWN_MINUTES
    )
```

---

## 📊 ПРИМЕРЫ ИСПОЛЬЗОВАНИЯ

### Пример 1: HARD BLOCK

```python
brain = MetaDecisionBrain()

result = brain.evaluate(
    confidence_score=0.3,
    entropy_score=0.75,
    portfolio_exposure=0.5,
    system_health=SystemHealthStatus.OK
)

# Результат:
# allow_trading = False
# block_level = BlockLevel.HARD
# reason = "HARD BLOCK: High entropy (0.75) combined with low confidence (0.30)..."
# cooldown_minutes = 30
```

### Пример 2: SOFT BLOCK

```python
result = brain.evaluate(
    confidence_score=0.5,
    entropy_score=0.5,
    portfolio_exposure=0.6,
    signals_count_recent=12,
    system_health=SystemHealthStatus.OK
)

# Результат:
# allow_trading = False
# block_level = BlockLevel.SOFT
# reason = "SOFT BLOCK: Too many signals in recent period (12)..."
# cooldown_minutes = 15
```

### Пример 3: ALLOW

```python
result = brain.evaluate(
    confidence_score=0.7,
    entropy_score=0.3,
    portfolio_exposure=0.4,
    signals_count_recent=3,
    system_health=SystemHealthStatus.OK
)

# Результат:
# allow_trading = True
# block_level = None
# reason = "No blocking conditions detected. System is ready for trading."
# cooldown_minutes = 0
```

---

## 🔄 ИНТЕГРАЦИЯ

### Использование в Gatekeeper (текущая реализация)

**Файл:** `execution/gatekeeper.py`

MetaDecisionBrain интегрирован как **первый фильтр** в `send_signal()`, проверяется ДО DecisionCore и PortfolioBrain:

```python
from brains.meta_decision_brain import (
    MetaDecisionBrain, MetaDecisionResult, SystemHealthStatus, TimeContext
)

# В Gatekeeper.__init__()
if META_DECISION_AVAILABLE:
    try:
        self.meta_decision_brain = MetaDecisionBrain()
    except Exception as e:
        logger.warning(f"MetaDecisionBrain недоступен: {type(e).__name__}: {e}")
        self.meta_decision_brain = None

# В Gatekeeper.send_signal() - ПЕРВЫЙ ФИЛЬТР
if self.meta_decision_brain and snapshot:
    meta_result = self._check_meta_decision(snapshot, system_state)
    if meta_result and not meta_result.allow_trading:
        # MetaDecisionBrain заблокировал торговлю
        print(f"   🚫 MetaDecisionBrain заблокировал сигнал для {symbol}: {meta_result.reason}")
        self.blocked_signals_count += 1
        return  # Early exit - не вызываем DecisionCore, PortfolioBrain
```

**Fail-safe поведение:**
- MetaDecisionBrain является **опциональным модулем**
- Если модуль недоступен (ImportError), система продолжает работу
- Если ошибка при вызове `evaluate()`, сигнал не блокируется (fail-safe)
- Логируется предупреждение, но торговля не останавливается

**Метод `_check_meta_decision()`:**
- Извлекает данные из `SignalSnapshot` и `SystemState`
- Вычисляет `portfolio_exposure` из открытых позиций
- Преобразует `system_health` в `SystemHealthStatus`
- Вызывает `meta_decision_brain.evaluate()` с агрегированными метриками

### Использование в Decision Core (потенциальное)

```python
from brains.meta_decision_brain import MetaDecisionBrain, SystemHealthStatus, TimeContext

meta_brain = MetaDecisionBrain()

# Вычисляем агрегированные метрики
confidence_avg = calculate_average_confidence(signals)
entropy_avg = calculate_average_entropy(signals)
exposure = calculate_portfolio_exposure(positions)

# Проверяем через MetaDecisionBrain
meta_result = meta_brain.evaluate(
    market_regime=system_state.market_regime,
    confidence_score=confidence_avg,
    entropy_score=entropy_avg,
    portfolio_exposure=exposure,
    recent_outcomes=recent_pnl,
    signals_count_recent=len(recent_signals),
    system_health=SystemHealthStatus.DEGRADED if system_state.system_health.safe_mode else SystemHealthStatus.OK,
    time_context=TimeContext.SESSION_MID
)

if not meta_result.allow_trading:
    # Блокируем торговлю
    return TradingDecision(
        can_trade=False,
        reason=f"MetaDecisionBrain: {meta_result.reason}",
        ...
    )
```

---

## ✅ ПОДТВЕРЖДЕНИЕ ТРЕБОВАНИЙ

### 1. Архитектура
- ✅ Модуль `meta_decision_brain.py` создан
- ✅ Класс `MetaDecisionBrain` реализован
- ✅ НЕ работает с рынком напрямую
- ✅ Работает ТОЛЬКО с агрегированными метриками

### 2. Input/Output
- ✅ Все требуемые input параметры реализованы
- ✅ `MetaDecisionResult` dataclass создан
- ✅ Все поля присутствуют

### 3. Логика
- ✅ HARD BLOCK условия реализованы (3 условия)
- ✅ SOFT BLOCK условия реализованы (5 условий)
- ✅ ALLOW только если нет блокирующих условий

### 4. Ограничения
- ✅ Нет singleton
- ✅ Нет глобальных состояний
- ✅ Чистый детерминированный код
- ✅ Лёгкий, быстрый, без внешних зависимостей

### 5. Дополнительно
- ✅ Подробные docstring
- ✅ Explainable reason для каждого блока
- ✅ Точка расширения для future brains
- ✅ Явные константы для cooldown и приоритетов
- ✅ Нормализация входных данных
- ✅ Явные переходы состояний

### 6. Интеграция
- ✅ Интегрирован в Gatekeeper как первый фильтр
- ✅ Fail-safe поведение (опциональный модуль)
- ✅ Логирование решений через DecisionTrace
- ✅ Early exit при блокировке (не вызывает DecisionCore)

---

## 🎯 РЕЗУЛЬТАТ

### Достигнуто:
1. ✅ **MetaDecisionBrain создан** - верхнеуровневый модуль для WHEN NOT TO TRADE
2. ✅ **Все enum'ы созданы** - SystemHealthStatus, BlockLevel, TimeContext, DecisionState
3. ✅ **MetaDecisionResult dataclass** - с explainable reason
4. ✅ **HARD BLOCK логика** - все 3 условия реализованы с явными переходами
5. ✅ **SOFT BLOCK логика** - все 5 условий реализованы с явными переходами
6. ✅ **Точка расширения** - MetaDecisionBrainExtension класс
7. ✅ **Явные переходы состояний** - методы `_transition_to_*` с константами приоритетов
8. ✅ **Константы cooldown** - все значения вынесены в константы класса
9. ✅ **Нормализация входных данных** - метод `_normalize_inputs()`
10. ✅ **Интеграция в Gatekeeper** - первый фильтр в `send_signal()`

### Архитектура:
- ✅ Чистый детерминированный код
- ✅ Без состояния и singleton
- ✅ Лёгкий и быстрый
- ✅ Explainable решения
- ✅ Готов к расширению
- ✅ Явные переходы состояний
- ✅ Fail-safe поведение (опциональный модуль)

---

*MetaDecisionBrain готов к использованию.*

