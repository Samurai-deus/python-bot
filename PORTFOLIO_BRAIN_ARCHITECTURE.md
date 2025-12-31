# PORTFOLIO BRAIN - АРХИТЕКТУРА

**Дата:** 2024-12-19  
**Задача:** Научить систему думать "Улучшает ли ЭТОТ сигнал ПОРТФЕЛЬ?"

---

## ✅ ВЫПОЛНЕНО

### 1. Создан Portfolio Brain

**Файл:** `core/portfolio_brain.py`

**Класс:** `PortfolioBrain`

**Принцип:**
- PortfolioBrain НЕ анализирует рынок
- Он анализирует систему как целое
- Отвечает на вопрос: "Улучшает ли ЭТОТ сигнал ПОРТФЕЛЬ?"

---

## 📋 IMMUTABLE ОБЪЕКТЫ

### PositionSnapshot

```python
@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    direction: PositionDirection  # LONG | SHORT
    size: float  # Размер позиции в USDT
    entry_price: float
    unrealized_pnl: float
    market_state: Optional[MarketState]
    confidence: float  # Confidence сигнала на момент входа
    entropy: float  # Entropy сигнала на момент входа
```

### PortfolioState

```python
@dataclass(frozen=True)
class PortfolioState:
    total_exposure: float  # Суммарная экспозиция в USDT
    long_exposure: float  # Экспозиция LONG в USDT
    short_exposure: float  # Экспозиция SHORT в USDT
    net_exposure: float  # Чистая экспозиция (long - short)
    
    risk_budget: float  # Доступный риск-бюджет в USDT
    used_risk: float  # Использованный риск в USDT
    
    regime_exposure: Dict[MarketState, float]  # Экспозиция по MarketState
    symbol_exposure: Dict[str, float]  # Экспозиция по символам
```

### PortfolioDecision (enum)

```python
class PortfolioDecision(str, Enum):
    ALLOW = "ALLOW"  # Разрешить сигнал
    REDUCE = "REDUCE"  # Разрешить с уменьшенным размером
    BLOCK = "BLOCK"  # Заблокировать сигнал
    SCALE_DOWN = "SCALE_DOWN"  # Уменьшить размер из-за перегрузки
```

---

## 🔹 БЛОКИРУЮЩИЕ УСЛОВИЯ (HARD)

Если ЛЮБОЕ выполнено → **BLOCK**:

1. **total_exposure > risk_budget**
   - Портфель превышает риск-бюджет

2. **entropy портфеля > 0.75**
   - Портфель слишком хаотичен

3. **60% портфеля в одном MarketState**
   - И новый сигнал усиливает это состояние

4. **новый сигнал усиливает уже доминирующее направление**
   - Портфель перегружен этим состоянием (>50%)

5. **confidence нового сигнала < 0.4**
   - Сигнал имеет слишком низкую уверенность

---

## 🔹 УМЕНЬШЕНИЕ (SOFT BLOCK)

Если выполнено → **SCALE_DOWN**:

1. **Высокая корреляция сигнала с портфелем**
   - Корреляция > 0.7 → множитель 0.5

2. **Усиливает уже перегруженный режим**
   - Экспозиция в состоянии > 40% → множитель 0.6

3. **confidence < средний confidence портфеля**
   - confidence < average * 0.8 → множитель 0.7

---

## 🔹 РАЗРЕШЕНИЕ

Если выполнено → **ALLOW**:

1. **Сигнал диверсифицирует режимы**
   - Экспозиция в состоянии < 20%

2. **Снижает net_exposure**
   - Портфель перегружен в одну сторону

3. **confidence > median(confidence портфеля)**
   - confidence > average * 1.2

4. **entropy сигнала < entropy портфеля**
   - entropy < portfolio_entropy * 0.8

---

## 🔹 REDUCE

Если выполнено → **REDUCE**:

- Портфель перегружен (exposure > 80% risk_budget)
- Но сигнал стратегически полезен (confidence > 0.7 или entropy < 0.3)
- → Разрешить минимальный размер (множитель 0.3)

---

## 📊 АГРЕГИРОВАННЫЕ МЕТРИКИ

PortfolioBrain вычисляет:

1. **portfolio_entropy**
   - Средняя энтропия позиций (взвешенная по размеру)

2. **dominant_market_state**
   - MarketState с максимальной экспозицией

3. **exposure_by_state**
   - Экспозиция по каждому MarketState

4. **exposure_by_direction**
   - Экспозиция LONG и SHORT

5. **average_confidence**
   - Средняя confidence портфеля (взвешенная по размеру)

6. **risk_utilization_ratio**
   - used_risk / risk_budget

---

## 🔄 ИНТЕГРАЦИЯ В DECISION FLOW

### Decision Pipeline:

```
Signal →
  Score →
    Risk →
      Confidence / Entropy →
        PortfolioBrain →
          FINAL_DECISION
```

### Интеграция в Gatekeeper:

```python
# В gatekeeper.send_signal():
if snapshot:
    portfolio_analysis = self._check_portfolio(snapshot)
    if portfolio_analysis.decision == PortfolioDecision.BLOCK:
        return  # Блокируем сигнал
    
    # Применяем размер позиции
    if portfolio_analysis.recommended_size_multiplier < 1.0:
        signal_data["position_size"] *= portfolio_analysis.recommended_size_multiplier
```

### Интеграция в signal_generator:

```python
# Передаём snapshot в gatekeeper
gatekeeper.send_signal(
    ...,
    snapshot=snapshot  # Для портфельного анализа
)
```

---

## 📱 TELEGRAM / IO

### Отображение в Telegram:

```
🧺 Portfolio:
• Решение: SCALE_DOWN
• Причина: Overexposed to MarketState.D
• Экспозиция: 78%
```

### Формат сообщения:

```python
if portfolio_analysis:
    extra += f"\n\n🧺 Portfolio:"
    extra += f"\n• Решение: {portfolio_analysis.decision.value}"
    extra += f"\n• Причина: {portfolio_analysis.reason}"
    if portfolio_analysis.risk_utilization_ratio > 0:
        extra += f"\n• Экспозиция: {portfolio_analysis.risk_utilization_ratio * 100:.1f}%"
```

---

## 🛡️ ЗАПРЕТЫ

✅ PortfolioBrain НЕ знает цену  
✅ НЕ знает таймфреймы  
✅ НЕ смотрит индикаторы  
✅ НЕ открывает сделки  

PortfolioBrain анализирует только:
- Состояние портфеля
- Агрегированные метрики
- Соотношения и балансы

---

## 🔧 HELPER ФУНКЦИИ

### convert_trades_to_positions()

```python
def convert_trades_to_positions(
    open_trades: List[Dict],
    current_prices: Optional[Dict[str, float]] = None
) -> List[PositionSnapshot]:
    """Преобразует открытые сделки из БД в PositionSnapshot"""
```

### calculate_portfolio_state()

```python
def calculate_portfolio_state(
    open_positions: List[PositionSnapshot],
    risk_budget: float,
    initial_balance: float = 10000.0
) -> PortfolioState:
    """Вычисляет PortfolioState на основе открытых позиций"""
```

---

## ✅ ПОДТВЕРЖДЕНИЕ ТРЕБОВАНИЙ

### 1. Чистый класс
- ✅ PortfolioBrain - чистый класс (без состояния)
- ✅ Метод `evaluate()` - детерминированная функция

### 2. Immutable объекты
- ✅ PositionSnapshot - frozen dataclass
- ✅ PortfolioState - frozen dataclass

### 3. Блокирующие условия
- ✅ Все 5 условий реализованы
- ✅ HARD блокировка → BLOCK

### 4. Уменьшение размера
- ✅ SCALE_DOWN с множителем
- ✅ REDUCE для стратегически полезных сигналов

### 5. Интеграция
- ✅ Интегрирован в Gatekeeper
- ✅ Интегрирован в signal_generator
- ✅ Отображается в Telegram

---

## 🎯 РЕЗУЛЬТАТ

### Достигнуто:
1. ✅ **PortfolioBrain создан** - анализирует систему как целое
2. ✅ **Immutable объекты** - PositionSnapshot, PortfolioState
3. ✅ **Блокирующие условия** - все 5 реализованы
4. ✅ **Уменьшение размера** - SCALE_DOWN и REDUCE
5. ✅ **Интеграция** - в Gatekeeper и signal_generator
6. ✅ **IO** - отображение в Telegram

### Архитектура:
- ✅ Чистый класс
- ✅ Domain-only (не знает цены, таймфреймы, индикаторы)
- ✅ Готов к портфельному уровню
- ✅ Интегрирован в decision flow

---

*Portfolio Brain готов к использованию.*

