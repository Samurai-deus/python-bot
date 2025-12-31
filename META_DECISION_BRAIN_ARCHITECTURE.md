# META DECISION BRAIN - АРХИТЕКТУРА

**Дата:** 2024-12-19  
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

---

## 📤 OUTPUT

Возвращает `MetaDecisionResult`:

- `allow_trading: bool` - Разрешена ли торговля
- `reason: str` - Объяснение решения (explainable)
- `block_level: Optional[BlockLevel]` - Уровень блокировки (HARD/SOFT)
- `cooldown_minutes: int` - Время ожидания в минутах

---

## 🔹 HARD BLOCK (Жёсткая блокировка)

Если ЛЮБОЕ выполнено → **HARD BLOCK**:

1. **entropy > 0.7 AND confidence < 0.4**
   - Высокая энтропия + низкая уверенность = система неопределённа
   - Cooldown: 30 минут

2. **portfolio_exposure > 0.8**
   - Экспозиция превышает безопасный лимит (80%)
   - Cooldown: 30 минут

3. **system_health == DEGRADED**
   - Система испытывает проблемы
   - Cooldown: 30 минут

---

## 🔹 SOFT BLOCK (Мягкая блокировка)

Если выполнено → **SOFT BLOCK**:

1. **signals_count_recent > 10**
   - Слишком много сигналов за период (риск overtrading)
   - Cooldown: 15 минут

2. **confidence средний, entropy средний**
   - Средние значения (0.4-0.6) указывают на неопределённость
   - Если exposure > 0.5 → Cooldown: 10 минут

3. **Плохие результаты в recent_outcomes**
   - Больше 60% отрицательных результатов
   - Cooldown: 20 минут

4. **Высокая экспозиция с низкой уверенностью**
   - exposure > 0.6 AND confidence < 0.5
   - Cooldown: 15 минут

5. **Конец сессии с высокой энтропией**
   - SESSION_END AND entropy > 0.6
   - Cooldown: 5 минут

---

## 🔹 ALLOW (Разрешение)

Торговля разрешена только если:

- НЕТ ни одного HARD BLOCK условия
- НЕТ ни одного SOFT BLOCK условия

---

## 🛡️ АРХИТЕКТУРНЫЕ ПРИНЦИПЫ

### 1. Чистый детерминированный код
- ✅ Нет singleton
- ✅ Нет глобальных состояний
- ✅ Все методы детерминированы
- ✅ Одинаковые входные данные → одинаковый результат

### 2. Лёгкий и быстрый
- ✅ Нет внешних зависимостей (кроме core.decision_core)
- ✅ Минимальные вычисления
- ✅ Быстрая проверка условий

### 3. Explainable
- ✅ Каждое решение имеет объяснение (reason)
- ✅ Метод `explain_decision()` для подробного объяснения
- ✅ Понятные причины блокировки

### 4. Точка расширения
- ✅ Класс `MetaDecisionBrainExtension` для будущих расширений
- ✅ Можно добавлять новые проверки без изменения основного класса

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

### Использование в Decision Core:

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

---

## 🎯 РЕЗУЛЬТАТ

### Достигнуто:
1. ✅ **MetaDecisionBrain создан** - верхнеуровневый модуль для WHEN NOT TO TRADE
2. ✅ **Все enum'ы созданы** - SystemHealthStatus, BlockLevel, TimeContext
3. ✅ **MetaDecisionResult dataclass** - с explainable reason
4. ✅ **HARD BLOCK логика** - все 3 условия реализованы
5. ✅ **SOFT BLOCK логика** - все 5 условий реализованы
6. ✅ **Точка расширения** - MetaDecisionBrainExtension класс

### Архитектура:
- ✅ Чистый детерминированный код
- ✅ Без состояния и singleton
- ✅ Лёгкий и быстрый
- ✅ Explainable решения
- ✅ Готов к расширению

---

*MetaDecisionBrain готов к использованию.*

