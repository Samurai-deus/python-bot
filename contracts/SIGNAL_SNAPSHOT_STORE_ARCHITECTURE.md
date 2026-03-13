# SIGNAL SNAPSHOT STORE - АРХИТЕКТУРА

**Дата:** 2024-12-19  
**Задача:** Создать хранилище для агрегированных снимков состояния системы

---

## ✅ ВЫПОЛНЕНО

### 1. Создан Signal Snapshot Store

**Файл:** `core/signal_snapshot_store.py`

**Класс:** `SignalSnapshotStore`

**Принцип:**
- Snapshot НЕ влияет на торговые решения
- Snapshot создаётся ДО MetaDecisionBrain и PositionSizer
- Фиксирует агрегированный снимок состояния рынка и системы

---

## 📋 СТРУКТУРА

### SignalSnapshotRecord (dataclass)

```python
@dataclass
class SignalSnapshotRecord:
    snapshot_id: Optional[int] = None
    timestamp: datetime
    symbol: str
    states: Dict[str, str]  # MarketState как строки для JSON
    confidence: float
    entropy: float
    score: float
    risk_level: str
    indicators: Dict[str, Any]
    portfolio_state: Dict[str, Any]
    decision_flags: Dict[str, Any]
```

**Примечание:**
- Это запись для хранения в БД
- Отличается от доменного объекта `SignalSnapshot` из `core/signal_snapshot.py`
- Используется для анализа, replay, drift detection

---

## 🔧 API

### save_snapshot()

```python
def save_snapshot(
    timestamp: datetime,
    symbol: str,
    states: Dict[str, str],
    confidence: float,
    entropy: float,
    score: float,
    risk_level: str,
    indicators: Optional[Dict[str, Any]] = None,
    portfolio_state: Optional[Dict[str, Any]] = None,
    decision_flags: Optional[Dict[str, Any]] = None
) -> int
```

**Параметры:**
- `timestamp`: Время создания snapshot
- `symbol`: Торговая пара
- `states`: Состояния по таймфреймам (Dict[str, MarketState как строка])
- `confidence`: Уверенность системы (0.0 - 1.0)
- `entropy`: Когнитивная неопределённость (0.0 - 1.0)
- `score`: Score сигнала
- `risk_level`: Уровень риска ("LOW", "MEDIUM", "HIGH")
- `indicators`: Словарь индикаторов (опционально)
- `portfolio_state`: Агрегированное состояние портфеля (опционально)
- `decision_flags`: Флаги решений (опционально)

**Возвращает:**
- `int`: ID сохранённого snapshot

### get_snapshot_by_id()

```python
def get_snapshot_by_id(snapshot_id: int) -> Optional[SignalSnapshotRecord]
```

**Возвращает:**
- `SignalSnapshotRecord` или `None` если не найден

### get_recent_snapshots()

```python
def get_recent_snapshots(
    symbol: Optional[str] = None,
    limit: int = 100
) -> List[SignalSnapshotRecord]
```

**Возвращает:**
- `List[SignalSnapshotRecord]`: Список последних snapshot'ов

---

## 🗄️ СХЕМА БАЗЫ ДАННЫХ

### Таблица: signal_snapshots

```sql
CREATE TABLE signal_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    states TEXT NOT NULL,  -- JSON
    confidence REAL NOT NULL,
    entropy REAL NOT NULL,
    score REAL NOT NULL,
    risk_level TEXT NOT NULL,
    indicators TEXT NOT NULL,  -- JSON
    portfolio_state TEXT NOT NULL,  -- JSON
    decision_flags TEXT NOT NULL,  -- JSON
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
```

### Индексы:

- `idx_signal_snapshots_timestamp` - для быстрого поиска по времени
- `idx_signal_snapshots_symbol` - для фильтрации по символу
- `idx_signal_snapshots_symbol_timestamp` - составной индекс для быстрого поиска по символу и времени

---

## 🔄 АРХИТЕКТУРА ДЛЯ REPLAY ENGINE

### ReplayEngine

```python
class ReplayEngine:
    def replay_snapshots(
        start_time: datetime,
        end_time: datetime,
        symbol: Optional[str] = None
    ) -> List[SignalSnapshotRecord]
    
    def replay_signal_generation(
        snapshot: SignalSnapshotRecord
    ) -> Dict[str, Any]
```

**Назначение:** Воспроизведение последовательности snapshot'ов для анализа

---

## 📝 ПРИМЕРЫ ИСПОЛЬЗОВАНИЯ

### Пример 1: Сохранение snapshot

```python
from core.signal_snapshot_store import SignalSnapshotStore
from core.market_state import state_to_string

store = SignalSnapshotStore()

# Сохраняем snapshot ДО MetaDecisionBrain и PositionSizer
snapshot_id = store.save_snapshot(
    timestamp=datetime.now(UTC),
    symbol="BTCUSDT",
    states={
        "15m": state_to_string(MarketState.D),
        "30m": state_to_string(MarketState.A),
        "1h": state_to_string(MarketState.B)
    },
    confidence=0.75,
    entropy=0.25,
    score=85.0,
    risk_level="LOW",
    indicators={
        "rsi_15m": 45.2,
        "macd_signal": "BULLISH",
        "atr_pct": 1.2
    },
    portfolio_state={
        "total_exposure": 5000.0,
        "available_risk_ratio": 0.5
    },
    decision_flags={
        "meta_decision": "ALLOW",
        "portfolio": "ALLOW"
    }
)
```

### Пример 2: Получение snapshot по ID

```python
snapshot = store.get_snapshot_by_id(snapshot_id)
if snapshot:
    print(f"Snapshot {snapshot.snapshot_id}: {snapshot.symbol}, confidence={snapshot.confidence}")
```

### Пример 3: Получение последних snapshot'ов

```python
# Последние 50 snapshot'ов
recent = store.get_recent_snapshots(limit=50)

# Последние snapshot'ы по символу
btc_snapshots = store.get_recent_snapshots(symbol="BTCUSDT", limit=100)
```

### Пример 4: Replay Engine

```python
from core.signal_snapshot_store import ReplayEngine
from datetime import timedelta

replay = ReplayEngine(store)

# Воспроизвести snapshot'ы за последний час
end_time = datetime.now(UTC)
start_time = end_time - timedelta(hours=1)

snapshots = replay.replay_snapshots(start_time, end_time, symbol="BTCUSDT")

for snapshot in snapshots:
    result = replay.replay_signal_generation(snapshot)
    print(f"Replay: {result['symbol']} at {result['timestamp']}")
```

---

## 🔄 ИНТЕГРАЦИЯ В СИСТЕМУ

### Интеграция в signal_generator:

```python
from core.signal_snapshot_store import SignalSnapshotStore
from core.market_state import state_to_string

store = SignalSnapshotStore()

# После создания SignalSnapshot, но ДО MetaDecisionBrain и PositionSizer
snapshot_id = store.save_snapshot(
    timestamp=snapshot.timestamp,
    symbol=snapshot.symbol,
    states={tf: state_to_string(state) for tf, state in snapshot.states.items()},
    confidence=snapshot.confidence,
    entropy=snapshot.entropy,
    score=snapshot.score,
    risk_level=snapshot.risk_level.value,
    indicators={
        "rsi": momentum_data.get("rsi_15m", 0),
        "macd": momentum_data.get("macd_signal", "NEUTRAL"),
        "atr_pct": volatility_pct
    },
    portfolio_state={
        "total_exposure": portfolio_state.total_exposure,
        "available_risk_ratio": portfolio_state.available_risk_ratio()
    },
    decision_flags={}  # Заполняется после MetaDecisionBrain и PositionSizer
)
```

---

## ✅ ПОДТВЕРЖДЕНИЕ ТРЕБОВАНИЙ

### 1. Архитектура
- ✅ Файл `signal_snapshot_store.py` создан
- ✅ Класс `SignalSnapshotStore` реализован
- ✅ Snapshot НЕ влияет на торговые решения
- ✅ Snapshot создаётся ДО MetaDecisionBrain и PositionSizer

### 2. API
- ✅ `save_snapshot()` реализован
- ✅ `get_snapshot_by_id()` реализован
- ✅ `get_recent_snapshots()` реализован

### 3. База данных
- ✅ Использует SQLite (общая БД)
- ✅ Хранит snapshot как JSON
- ✅ Минимум блокировок
- ✅ Автоматическое создание таблицы
- ✅ Лёгкая и быстрая запись

### 4. Дополнительно
- ✅ `SignalSnapshotRecord` dataclass создан
- ✅ Подробные docstring добавлены
- ✅ Структура для Replay Engine подготовлена
- ✅ Код максимально читаемый

### 5. Ограничения
- ✅ Нет singleton
- ✅ Лёгкая и быстрая запись
- ✅ Обработка ошибок (не влияет на торговую логику)

---

## 🎯 РЕЗУЛЬТАТ

### Достигнуто:
1. ✅ **SignalSnapshotStore создан** - хранилище для snapshot'ов
2. ✅ **SignalSnapshotRecord dataclass** - для типизированных записей
3. ✅ **API реализован** - save_snapshot, get_snapshot_by_id, get_recent_snapshots
4. ✅ **SQLite интеграция** - общая БД, автоматическое создание таблицы
5. ✅ **Replay Engine** - архитектура подготовлена

### Архитектура:
- ✅ Не влияет на торговую логику
- ✅ Легковесная запись
- ✅ Готов к расширению
- ✅ JSON для сериализации

---

*SignalSnapshotStore готов к использованию.*

