# DECISION TRACE - АРХИТЕКТУРА

**Дата:** 2024-12-19  
**Задача:** Создать систему логирования решений торговой системы

---

## ✅ ВЫПОЛНЕНО

### 1. Создан Decision Trace

**Файл:** `core/decision_trace.py`

**Класс:** `DecisionTrace`

**Принцип:**
- DecisionTrace НЕ влияет на торговую логику
- Он ТОЛЬКО записывает решения
- Легковесная запись (быстро, без блокировок)

---

## 📋 СТРУКТУРА

### DecisionRecord (dataclass)

```python
@dataclass
class DecisionRecord:
    timestamp: datetime
    symbol: str
    decision_source: str
    allow_trading: bool
    block_level: BlockLevel
    reason: str
    context_snapshot: Dict[str, Any]
```

### BlockLevel (enum)

```python
class BlockLevel(str, Enum):
    HARD = "HARD"
    SOFT = "SOFT"
    NONE = "NONE"
```

---

## 🔧 API

### log_decision()

```python
def log_decision(
    symbol: str,
    decision_source: str,
    allow_trading: bool,
    block_level: BlockLevel,
    reason: str,
    context_snapshot: Optional[Dict[str, Any]] = None,
    timestamp: Optional[datetime] = None
) -> int
```

**Параметры:**
- `symbol`: Торговая пара (или "SYSTEM" для системных решений)
- `decision_source`: Источник решения (например, "MetaDecisionBrain", "PortfolioBrain", "Gatekeeper")
- `allow_trading`: Разрешена ли торговля
- `block_level`: Уровень блокировки (HARD, SOFT, NONE)
- `reason`: Объяснение решения
- `context_snapshot`: Снимок контекста на момент решения (опционально)
- `timestamp`: Время принятия решения (опционально, по умолчанию текущее время)

**Возвращает:**
- `int`: ID записи в базе данных

### get_recent_decisions()

```python
def get_recent_decisions(
    limit: int = 100,
    symbol: Optional[str] = None,
    decision_source: Optional[str] = None,
    allow_trading: Optional[bool] = None
) -> List[DecisionRecord]
```

**Параметры:**
- `limit`: Максимальное количество записей
- `symbol`: Фильтр по символу (опционально)
- `decision_source`: Фильтр по источнику решения (опционально)
- `allow_trading`: Фильтр по разрешению торговли (опционально)

**Возвращает:**
- `List[DecisionRecord]`: Список записей о решениях

### clear_old_records()

```python
def clear_old_records(days: int = 30) -> int
```

**Параметры:**
- `days`: Количество дней для хранения (записи старше будут удалены)

**Возвращает:**
- `int`: Количество удалённых записей

---

## 🗄️ СХЕМА БАЗЫ ДАННЫХ

### Таблица: decision_trace

```sql
CREATE TABLE decision_trace (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    decision_source TEXT NOT NULL,
    allow_trading INTEGER NOT NULL,
    block_level TEXT NOT NULL,
    reason TEXT NOT NULL,
    context_snapshot TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
```

### Индексы:

- `idx_decision_trace_timestamp` - для быстрого поиска по времени
- `idx_decision_trace_symbol` - для фильтрации по символу
- `idx_decision_trace_source` - для фильтрации по источнику
- `idx_decision_trace_allow_trading` - для фильтрации по разрешению

---

## 📊 ДОПОЛНИТЕЛЬНЫЕ МЕТОДЫ

### get_statistics()

```python
def get_statistics(
    days: int = 7,
    symbol: Optional[str] = None
) -> Dict[str, Any]
```

**Возвращает статистику:**
- Общее количество решений
- Количество разрешённых/заблокированных
- Количество HARD/SOFT блокировок
- Статистика по источникам решений

**Использование:** Для Drift Detector

---

## 🔄 АРХИТЕКТУРА ДЛЯ REPLAY / DRIFT DETECTOR

### DecisionReplay

```python
class DecisionReplay:
    def replay_decisions(
        start_time: datetime,
        end_time: datetime,
        symbol: Optional[str] = None
    ) -> List[DecisionRecord]
```

**Назначение:** Воспроизведение последовательности решений для анализа

### DriftDetector

```python
class DriftDetector:
    def detect_drift(
        baseline_days: int = 7,
        comparison_days: int = 7,
        threshold: float = 0.2
    ) -> Dict[str, Any]
```

**Назначение:** Обнаружение дрейфа в решениях со временем

**Логика:**
- Сравнивает статистику базовой линии с текущей статистикой
- Если разница превышает threshold, считается дрейфом

---

## 📝 ПРИМЕРЫ ИСПОЛЬЗОВАНИЯ

### Пример 1: Логирование решения

```python
from core.decision_trace import DecisionTrace, BlockLevel

trace = DecisionTrace()

# Логирование решения от MetaDecisionBrain
trace.log_decision(
    symbol="SYSTEM",
    decision_source="MetaDecisionBrain",
    allow_trading=False,
    block_level=BlockLevel.HARD,
    reason="High entropy (0.75) combined with low confidence (0.30)",
    context_snapshot={
        "entropy_score": 0.75,
        "confidence_score": 0.30,
        "portfolio_exposure": 0.5
    }
)
```

### Пример 2: Получение последних решений

```python
# Получить последние 50 решений
recent = trace.get_recent_decisions(limit=50)

# Получить решения по символу
btc_decisions = trace.get_recent_decisions(limit=100, symbol="BTCUSDT")

# Получить только заблокированные решения
blocked = trace.get_recent_decisions(limit=100, allow_trading=False)
```

### Пример 3: Статистика

```python
# Получить статистику за последние 7 дней
stats = trace.get_statistics(days=7)

print(f"Всего решений: {stats['total_decisions']}")
print(f"Разрешено: {stats['allowed']}")
print(f"Заблокировано: {stats['blocked']}")
print(f"HARD блокировок: {stats['hard_blocks']}")
```

### Пример 4: Очистка старых записей

```python
# Удалить записи старше 30 дней
deleted = trace.clear_old_records(days=30)
print(f"Удалено {deleted} записей")
```

### Пример 5: Replay

```python
from core.decision_trace import DecisionReplay
from datetime import datetime, UTC, timedelta

replay = DecisionReplay(trace)

# Воспроизвести решения за последний час
end_time = datetime.now(UTC)
start_time = end_time - timedelta(hours=1)

decisions = replay.replay_decisions(start_time, end_time)
```

### Пример 6: Drift Detection

```python
from core.decision_trace import DriftDetector

detector = DriftDetector(trace)

# Обнаружить дрейф
drift_result = detector.detect_drift(
    baseline_days=7,
    comparison_days=7,
    threshold=0.2
)

if drift_result["drift_detected"]:
    print("Обнаружен дрейф в решениях!")
    print(f"Дрейф: {drift_result['details']['drift']:.2f}")
```

---

## 🔄 ИНТЕГРАЦИЯ В СИСТЕМУ

### Интеграция в MetaDecisionBrain:

```python
from core.decision_trace import DecisionTrace, BlockLevel

trace = DecisionTrace()

# После принятия решения
result = meta_brain.evaluate(...)

trace.log_decision(
    symbol="SYSTEM",
    decision_source="MetaDecisionBrain",
    allow_trading=result.allow_trading,
    block_level=result.block_level or BlockLevel.NONE,
    reason=result.reason,
    context_snapshot={
        "confidence_score": confidence_score,
        "entropy_score": entropy_score,
        "portfolio_exposure": portfolio_exposure
    }
)
```

### Интеграция в PortfolioBrain:

```python
# После портфельного анализа
analysis = portfolio_brain.evaluate(...)

trace.log_decision(
    symbol=snapshot.symbol,
    decision_source="PortfolioBrain",
    allow_trading=analysis.decision == PortfolioDecision.ALLOW,
    block_level=BlockLevel.HARD if analysis.decision == PortfolioDecision.BLOCK else BlockLevel.NONE,
    reason=analysis.reason,
    context_snapshot={
        "portfolio_entropy": analysis.portfolio_entropy,
        "risk_utilization": analysis.risk_utilization_ratio
    }
)
```

---

## ✅ ПОДТВЕРЖДЕНИЕ ТРЕБОВАНИЙ

### 1. Архитектура
- ✅ Файл `decision_trace.py` создан
- ✅ Класс `DecisionTrace` реализован
- ✅ НЕ влияет на торговую логику
- ✅ ТОЛЬКО записывает решения

### 2. API
- ✅ `log_decision()` реализован
- ✅ `get_recent_decisions()` реализован
- ✅ `clear_old_records()` реализован

### 3. База данных
- ✅ Использует SQLite (общая БД)
- ✅ Таблица создаётся автоматически
- ✅ Запись максимально лёгкая (быстро, без блокировок)
- ✅ Явная и простая схема таблицы

### 4. Дополнительно
- ✅ `DecisionRecord` dataclass создан
- ✅ Подробные docstring добавлены
- ✅ Архитектура для Replay / Drift Detector подготовлена

### 5. Ограничения
- ✅ Нет singleton
- ✅ Явная и простая схема
- ✅ Обработка ошибок (не влияет на торговую логику)

---

## 🎯 РЕЗУЛЬТАТ

### Достигнуто:
1. ✅ **DecisionTrace создан** - система логирования решений
2. ✅ **DecisionRecord dataclass** - для типизированных записей
3. ✅ **API реализован** - log_decision, get_recent_decisions, clear_old_records
4. ✅ **SQLite интеграция** - общая БД, автоматическое создание таблицы
5. ✅ **Replay / Drift Detector** - архитектура подготовлена

### Архитектура:
- ✅ Не влияет на торговую логику
- ✅ Легковесная запись
- ✅ Готов к расширению
- ✅ Explainable (context_snapshot)

---

*DecisionTrace готов к использованию.*

