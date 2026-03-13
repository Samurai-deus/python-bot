# POSITION SIZER - АРХИТЕКТУРА

**Дата:** 2024-12-19  
**Задача:** Создать модуль PositionSizer для расчета размера позиции

---

## ✅ ВЫПОЛНЕНО

### 1. Создан Position Sizer

**Файл:** `core/position_sizer.py`

**Класс:** `PositionSizer`

**Принцип:**
- PositionSizer НЕ принимает решения о входе — только размер
- Рассчитывает допустимый размер позиции на основе confidence, entropy и состояния портфеля
- Если итоговый риск < min_threshold → position_allowed = False

---

## 📋 СТРУКТУРА

### PositionSizingConfig

```python
class PositionSizingConfig:
    max_risk_per_trade: float = 2.0  # Базовый риск на сделку (% от баланса)
    min_risk_threshold: float = 0.5  # Минимальный порог риска
    confidence_min: float = 0.2  # Минимальная confidence
    confidence_max: float = 1.0  # Максимальная confidence
    entropy_min: float = 0.1  # Минимальный entropy_factor
    entropy_max: float = 1.0  # Максимальный entropy_factor
```

### PositionSizingResult (dataclass)

```python
@dataclass
class PositionSizingResult:
    position_allowed: bool
    final_risk: float  # % от баланса
    base_risk: float  # % от баланса
    confidence_factor: float
    entropy_factor: float
    portfolio_factor: float
    reason: str
    position_size_usd: Optional[float] = None
```

### PortfolioStateProtocol (Protocol)

```python
class PortfolioStateProtocol(Protocol):
    def total_exposure(self) -> float: ...
    def available_risk_ratio(self) -> float: ...
```

---

## 🔧 ЛОГИКА РАСЧЁТА

### Формула:

```
base_risk = config.max_risk_per_trade
confidence_factor = clamp(confidence, 0.2, 1.0)
entropy_factor = clamp(1 - entropy, 0.1, 1.0)
portfolio_factor = portfolio_state.available_risk_ratio()

final_risk = base_risk * confidence_factor * entropy_factor * portfolio_factor
```

### Проверка минимального порога:

```
ЕСЛИ final_risk < config.min_risk_threshold:
    position_allowed = False
    reason = "Risk too small after scaling"
ИНАЧЕ:
    position_allowed = True
    position_size_usd = (balance * final_risk) / 100.0
```

---

## 📥 INPUT

Метод `calculate()` принимает:

- `confidence: float` - Уверенность системы (0.0 - 1.0)
- `entropy: float` - Когнитивная неопределённость (0.0 - 1.0)
- `portfolio_state: PortfolioStateProtocol` - Состояние портфеля
- `symbol: str` - Торговая пара
- `balance: Optional[float]` - Текущий баланс (опционально)

---

## 📤 OUTPUT

Возвращает `PositionSizingResult`:

- `position_allowed: bool` - Разрешена ли позиция
- `final_risk: float` - Итоговый риск (% от баланса)
- `base_risk: float` - Базовый риск до применения факторов
- `confidence_factor: float` - Множитель от confidence
- `entropy_factor: float` - Множитель от entropy
- `portfolio_factor: float` - Множитель от состояния портфеля
- `reason: str` - Объяснение результата
- `position_size_usd: Optional[float]` - Размер позиции в USDT

---

## 📊 ПРИМЕРЫ РАСЧЁТА

### Пример 1: Позиция разрешена

```python
from core.position_sizer import PositionSizer, PortfolioStateAdapter
from core.portfolio_brain import PortfolioState

# Создаём PositionSizer
sizer = PositionSizer()

# Создаём PortfolioState
portfolio_state = PortfolioState(
    total_exposure=5000.0,
    long_exposure=3000.0,
    short_exposure=2000.0,
    net_exposure=1000.0,
    risk_budget=10000.0,
    used_risk=5000.0
)

# Адаптер для PortfolioState
adapter = PortfolioStateAdapter(portfolio_state)

# Рассчитываем размер позиции
result = sizer.calculate(
    confidence=0.8,
    entropy=0.3,
    portfolio_state=adapter,
    symbol="BTCUSDT",
    balance=10000.0
)

# Результат:
# position_allowed = True
# final_risk = 2.0 * 0.8 * 0.7 * 0.5 = 0.56%
# position_size_usd = 10000.0 * 0.56 / 100 = 56.0 USDT
```

### Пример 2: Позиция не разрешена (риск слишком мал)

```python
result = sizer.calculate(
    confidence=0.3,  # Низкая уверенность
    entropy=0.8,     # Высокая неопределённость
    portfolio_state=adapter,
    symbol="BTCUSDT",
    balance=10000.0
)

# Результат:
# position_allowed = False
# final_risk = 2.0 * 0.3 * 0.2 * 0.5 = 0.06% < 0.5% (min_threshold)
# reason = "Risk too small after scaling (0.06% < 0.50%)"
```

### Пример 3: Высокая уверенность, низкая неопределённость

```python
result = sizer.calculate(
    confidence=0.9,  # Высокая уверенность
    entropy=0.2,     # Низкая неопределённость
    portfolio_state=adapter,
    symbol="BTCUSDT",
    balance=10000.0
)

# Результат:
# position_allowed = True
# final_risk = 2.0 * 0.9 * 0.8 * 0.5 = 0.72%
# position_size_usd = 10000.0 * 0.72 / 100 = 72.0 USDT
```

---

## 🔄 ИНТЕГРАЦИЯ В СИСТЕМУ

### Интеграция с PortfolioBrain:

```python
from core.position_sizer import PositionSizer, PortfolioStateAdapter
from core.portfolio_brain import PortfolioState

# После портфельного анализа
portfolio_state = calculate_portfolio_state(...)
adapter = PortfolioStateAdapter(portfolio_state)

# После создания SignalSnapshot
sizer = PositionSizer()
result = sizer.calculate(
    confidence=snapshot.confidence,
    entropy=snapshot.entropy,
    portfolio_state=adapter,
    symbol=snapshot.symbol
)

if result.position_allowed:
    # Используем result.position_size_usd для открытия позиции
    position_size = result.position_size_usd
else:
    # Позиция не разрешена - риск слишком мал
    print(f"Position not allowed: {result.reason}")
```

### Интеграция с SignalSnapshot:

```python
# В signal_generator после создания snapshot
from core.position_sizer import PositionSizer, PortfolioStateAdapter

sizer = PositionSizer()
portfolio_adapter = PortfolioStateAdapter(portfolio_state)

sizing_result = sizer.calculate(
    confidence=snapshot.confidence,
    entropy=snapshot.entropy,
    portfolio_state=portfolio_adapter,
    symbol=snapshot.symbol
)

# Обновляем snapshot с размером позиции (если разрешено)
if sizing_result.position_allowed:
    # Используем размер из PositionSizer
    position_size = sizing_result.position_size_usd
else:
    # Позиция не разрешена
    print(f"Position sizing blocked: {sizing_result.reason}")
```

---

## 🔧 АРХИТЕКТУРА ДЛЯ БУДУЩИХ ФАКТОРОВ

### PositionSizingFactor (базовый класс)

```python
class PositionSizingFactor:
    def calculate_factor(...) -> float:
        """Вычисляет множитель для размера позиции"""
        return 1.0
```

### RegimeFactor

```python
class RegimeFactor(PositionSizingFactor):
    """Фактор на основе режима рынка"""
    def calculate_factor(..., market_regime=None, **kwargs) -> float:
        # В трендовом режиме можно увеличить размер
        if market_regime and market_regime.trend_type == "TREND":
            return 1.1
        return 1.0
```

### VolatilityFactor

```python
class VolatilityFactor(PositionSizingFactor):
    """Фактор на основе волатильности"""
    def calculate_factor(..., volatility_level=None, **kwargs) -> float:
        # При высокой волатильности уменьшаем размер
        if volatility_level == "HIGH":
            return 0.8
        return 1.0
```

---

## 🛡️ АРХИТЕКТУРНЫЕ ПРИНЦИПЫ

### 1. Чистый детерминированный код
- ✅ Нет singleton
- ✅ Нет глобального состояния
- ✅ Все методы детерминированы
- ✅ Одинаковые входные данные → одинаковый результат

### 2. Расширяемость
- ✅ Protocol для PortfolioState (можно использовать любой объект)
- ✅ PositionSizingFactor для будущих факторов
- ✅ Конфигурация вынесена в отдельный класс

### 3. Без магических чисел
- ✅ Все параметры в PositionSizingConfig
- ✅ Используются значения из config.py
- ✅ Явные константы с объяснениями

### 4. Не принимает решения о входе
- ✅ Только рассчитывает размер
- ✅ position_allowed = False только если риск слишком мал
- ✅ Не блокирует сигналы, только размер

---

## ✅ ПОДТВЕРЖДЕНИЕ ТРЕБОВАНИЙ

### 1. Архитектура
- ✅ Файл `position_sizer.py` создан
- ✅ Класс `PositionSizer` реализован
- ✅ НЕ принимает решения о входе — только размер
- ✅ Если итоговый риск < min_threshold → position_allowed = False

### 2. Input/Output
- ✅ Все требуемые input параметры реализованы
- ✅ `PositionSizingResult` dataclass создан
- ✅ Все поля присутствуют

### 3. Логика
- ✅ Формула расчёта реализована
- ✅ Проверка минимального порога реализована
- ✅ Все факторы применяются корректно

### 4. Дополнительно
- ✅ `PositionSizingResult` dataclass создан
- ✅ Подробные docstring добавлены
- ✅ Код чистый, читаемый и расширяемый
- ✅ Архитектура для будущих факторов подготовлена

### 5. Ограничения
- ✅ Нет singleton
- ✅ Нет глобального состояния
- ✅ Нет магических чисел без конфига

---

## 🎯 РЕЗУЛЬТАТ

### Достигнуто:
1. ✅ **PositionSizer создан** - расчет размера позиции на основе confidence, entropy и портфеля
2. ✅ **PositionSizingResult dataclass** - для типизированных результатов
3. ✅ **Логика расчёта** - все факторы применяются корректно
4. ✅ **Проверка минимального порога** - position_allowed = False если риск слишком мал
5. ✅ **Архитектура для будущих факторов** - RegimeFactor, VolatilityFactor

### Архитектура:
- ✅ Чистый детерминированный код
- ✅ Без singleton и глобального состояния
- ✅ Расширяемый для будущих факторов
- ✅ Не принимает решения о входе

---

*PositionSizer готов к использованию.*

