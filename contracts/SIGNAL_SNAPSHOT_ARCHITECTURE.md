# SIGNAL SNAPSHOT - АРХИТЕКТУРА

**Дата:** 2024-12-19  
**Задача:** Immutable доменный объект для представления одного сигнала

---

## ✅ ВЫПОЛНЕНО

### Создан SignalSnapshot

**Файл:** `core/signal_snapshot.py`

**Характеристики:**
- ✅ Immutable (frozen dataclass)
- ✅ Domain-only (не зависит от Telegram, CSV, UI)
- ✅ Self-contained (содержит всю информацию о сигнале)
- ✅ Готов для передачи между brain'ами
- ✅ Готов для логирования, статистики, портфельной логики, backtest/replay

---

## 📋 СТРУКТУРА SIGNALSNAPSHOT

### Идентификация
- `timestamp: datetime` - Время создания (UTC)
- `symbol: str` - Торговая пара
- `timeframe_anchor: str` - Основной таймфрейм (например "15m")

### Рыночное состояние
- `states: Dict[str, Optional[MarketState]]` - ТОЛЬКО enum, не строки
- `market_regime: Optional[MarketRegime]` - Режим рынка
- `volatility_level: Optional[VolatilityLevel]` - Уровень волатильности
- `correlation_level: Optional[float]` - Средняя корреляция с рынком (0-1)

### Оценки
- `score: int` - Текущий score
- `score_max: int` - Максимальный возможный score (125)
- `confidence: Optional[float]` - Уверенность в сигнале (0-1, задел на будущее)
- `entropy: Optional[float]` - Энтропия решения (0-1, задел на будущее)

### Риск
- `risk_level: RiskLevel` - Уровень риска (enum)
- `recommended_leverage: Optional[float]` - Рекомендуемое плечо
- `entry: Optional[float]` - Цена входа
- `tp: Optional[float]` - Take profit
- `sl: Optional[float]` - Stop loss

### Решение
- `decision: SignalDecision` - Решение по сигналу (enum)
- `decision_reason: str` - Краткое объяснение решения

### Дополнительная информация
- `directions: Dict[str, str]` - Направления трендов
- `score_details: Dict` - Детали скоринга
- `reasons: list` - Причины решения

---

## 🔧 СОЗДАННЫЕ ENUM'Ы

### 1. SignalDecision
```python
class SignalDecision(str, Enum):
    ENTER = "ENTER"      # Вход разрешён
    SKIP = "SKIP"        # Пропустить
    OBSERVE = "OBSERVE"  # Наблюдать
    BLOCK = "BLOCK"      # Заблокирован
```

### 2. RiskLevel
```python
class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
```

### 3. VolatilityLevel
```python
class VolatilityLevel(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    EXTREME = "EXTREME"
    UNKNOWN = "UNKNOWN"
```

### 4. TrendType (задел на будущее)
```python
class TrendType(str, Enum):
    TREND = "TREND"
    RANGE = "RANGE"
    UNKNOWN = "UNKNOWN"
```

### 5. RiskSentiment (задел на будущее)
```python
class RiskSentiment(str, Enum):
    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"
```

---

## 🛡️ ИНВАРИАНТЫ

### Проверки в __post_init__:

1. ✅ **states содержит только MarketState enum или None**
   - Проверяется каждый элемент словаря
   - ValueError при нарушении

2. ✅ **score <= score_max**
   - Score не может превышать максимум
   - ValueError при нарушении

3. ✅ **confidence ∈ [0, 1] если задан**
   - Проверка диапазона
   - ValueError при нарушении

4. ✅ **tp > 0, sl > 0, entry > 0 если заданы**
   - Цены должны быть положительными
   - ValueError при нарушении

5. ✅ **recommended_leverage > 0 если задан**
   - Плечо должно быть положительным
   - ValueError при нарушении

6. ✅ **correlation_level ∈ [0, 1] если задан**
   - Корреляция в диапазоне [0, 1]
   - ValueError при нарушении

7. ✅ **entropy ∈ [0, 1] если задан**
   - Энтропия в диапазоне [0, 1]
   - ValueError при нарушении

---

## 📊 PROPERTIES

### score_pct
```python
@property
def score_pct(self) -> float:
    """Процент от максимального score"""
    return (self.score / self.score_max) * 100
```

### has_entry_zone
```python
@property
def has_entry_zone(self) -> bool:
    """Есть ли зона входа (entry, tp, sl)"""
    return self.entry is not None and self.tp is not None and self.sl is not None
```

### rr_ratio
```python
@property
def rr_ratio(self) -> Optional[float]:
    """Risk/Reward ratio"""
    # Автоматически рассчитывается из entry, tp, sl
```

### is_tradeable
```python
@property
def is_tradeable(self) -> bool:
    """Можно ли торговать по этому сигналу"""
    return (
        self.decision == SignalDecision.ENTER and
        self.risk_level != RiskLevel.HIGH and
        self.has_entry_zone
    )
```

---

## 🔄 ИНТЕГРАЦИЯ В СИСТЕМУ

### 1. signal_generator.py

**Создание SignalSnapshot:**
```python
# После всех расчётов создаём snapshot
normalized_states = normalize_states_dict(states)
risk_enum = risk_string_to_enum(risk)
volatility_enum = volatility_string_to_enum(volatility_metrics.get("volatility_level"))
decision = mode_to_decision(mode)

snapshot = SignalSnapshot(
    timestamp=datetime.now(UTC),
    symbol=symbol,
    timeframe_anchor="15m",
    states=normalized_states,
    market_regime=system_state.market_regime,
    volatility_level=volatility_enum,
    score=score,
    score_max=125,
    risk_level=risk_enum,
    entry=entry,
    tp=target,
    sl=stop,
    decision=decision,
    ...
)

# Логируем через snapshot
log_signal_snapshot(snapshot)
```

### 2. journal.py

**Логирование SignalSnapshot:**
```python
def log_signal_snapshot(snapshot: SignalSnapshot):
    """
    Логирует SignalSnapshot в CSV.
    Преобразует domain-объект в строки (IO-граница).
    """
    # Преобразуем enum в строки для CSV
    state_15m = state_to_string(snapshot.states.get("15m"))
    risk_str = snapshot.risk_level.value
    ...
```

### 3. Helper-функции

**Преобразование строк в enum:**
```python
mode_to_decision(mode: str) -> SignalDecision
risk_string_to_enum(risk: str) -> RiskLevel
volatility_string_to_enum(volatility: str) -> Optional[VolatilityLevel]
```

---

## 📝 ПРИМЕР ИСПОЛЬЗОВАНИЯ

### Создание в signal_generator:
```python
snapshot = SignalSnapshot(
    timestamp=datetime.now(UTC),
    symbol="BTCUSDT",
    timeframe_anchor="15m",
    states={"15m": MarketState.D, "30m": MarketState.A, "1h": MarketState.B},
    market_regime=market_regime,
    volatility_level=VolatilityLevel.NORMAL,
    correlation_level=0.75,
    score=85,
    score_max=125,
    risk_level=RiskLevel.LOW,
    recommended_leverage=3.0,
    entry=50000.0,
    tp=51000.0,
    sl=49500.0,
    decision=SignalDecision.ENTER,
    decision_reason="Score: 85/125, Mode: TRADE, Risk: LOW",
    directions={"30m": "UP", "1h": "UP"},
    score_details={"total_score": 85, "volatility_score": 10},
    reasons=["Чёткий отказ на 15m", "1H и 30m согласованы"]
)
```

### Использование в risk/scoring:
```python
def evaluate_signal(snapshot: SignalSnapshot) -> str:
    """Оценка сигнала на основе snapshot"""
    if snapshot.risk_level == RiskLevel.HIGH:
        return "BLOCK"
    if snapshot.score_pct < 50:
        return "SKIP"
    return "ENTER"
```

### Использование в journal:
```python
# Логирование
log_signal_snapshot(snapshot)

# Преобразование в строки происходит только на IO-границе
```

---

## ✅ ПОДТВЕРЖДЕНИЕ ТРЕБОВАНИЙ

### 1. Immutable
- ✅ `@dataclass(frozen=True)` - объект нельзя изменить после создания
- ✅ Все поля readonly
- ✅ Инварианты проверяются при создании

### 2. Domain-only
- ✅ Нет зависимостей от Telegram, CSV, UI
- ✅ Используется ТОЛЬКО в runtime
- ✅ Преобразование в строки происходит на IO-границе

### 3. Self-contained
- ✅ Содержит всю информацию о сигнале
- ✅ Не требует дополнительных данных для использования
- ✅ Готов для передачи между компонентами

### 4. Готов для использования
- ✅ Логирование: `log_signal_snapshot(snapshot)`
- ✅ Статистика: доступ ко всем полям через properties
- ✅ Портфельная логика: `is_tradeable`, `rr_ratio`, `risk_level`
- ✅ Backtest/replay: immutable snapshot можно сохранить и воспроизвести

---

## 🎯 РЕЗУЛЬТАТ

### Достигнуто:
1. ✅ **SignalSnapshot создан** как immutable доменный объект
2. ✅ **Enum'ы созданы** для всех типов решений и уровней
3. ✅ **Инварианты добавлены** с явными проверками
4. ✅ **Интегрирован в signal_generator** - создаётся после всех расчётов
5. ✅ **Интегрирован в journal** - логирование через snapshot
6. ✅ **Helper-функции** для преобразования строк в enum

### Архитектура:
- ✅ Immutable
- ✅ Domain-only
- ✅ Готов к портфельному уровню
- ✅ Готов к backtest/replay

---

*SignalSnapshot готов к использованию.*

