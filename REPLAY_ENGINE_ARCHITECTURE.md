# REPLAY ENGINE - АРХИТЕКТУРА

**Дата:** 2024-12-19  
**Задача:** Реализовать Replay Engine для повторного прогона решений

---

## ✅ ВЫПОЛНЕНО

### 1. Создан Replay Engine

**Файлы:**
- `core/replay_models.py` - модели данных
- `core/replay_engine.py` - основная логика
- `core/replay_report.py` - генерация отчётов

**Класс:** `ReplayEngine`

**Принцип:**
- Replay НЕ взаимодействует с live-рынком
- Replay НЕ меняет SystemState
- Replay работает ТОЛЬКО на данных snapshot
- Replay НЕ торгует
- Replay НЕ пишет в production-логи

---

## 📋 ЧЕМ REPLAY ОТЛИЧАЕТСЯ ОТ BACKTEST

### Backtest:
- Симулирует торговлю на исторических данных
- Использует реальные цены и свечи
- Проверяет прибыльность стратегии
- Может открывать/закрывать позиции

### Replay:
- Повторяет принятие решений на сохранённых snapshot'ах системы
- Использует только данные из snapshot (confidence, entropy, states)
- Выявляет расхождения в решениях (drift detection)
- НЕ торгует, только анализирует решения
- Оффлайн-инструмент аудита

---

## 📋 СТРУКТУРА

### ReplayModels (replay_models.py)

1. **DecisionType** (enum) - ALLOW, BLOCK, REDUCE, SCALE_DOWN, SKIP, OBSERVE, ENTER, NONE
2. **OriginalDecision** (dataclass) - оригинальное решение из snapshot
3. **ReplayedDecision** (dataclass) - повторное решение
4. **DecisionDiff** (dataclass) - разница между решениями
5. **ReplayResult** (dataclass) - результат replay для одного snapshot
6. **ReplayReport** (dataclass) - агрегированный отчёт

### ReplayEngine (replay_engine.py)

**Методы:**
- `replay_snapshot(snapshot_id)` - replay одного snapshot
- `replay_snapshots(snapshot_ids)` - replay списка snapshot'ов
- `replay_recent_snapshots(symbol, limit)` - replay последних snapshot'ов

**Внутренние методы:**
- `_replay_snapshot_record()` - основной метод replay
- `_extract_original_decision()` - извлечение оригинального решения
- `_restore_context()` - восстановление контекста
- `_replay_through_logic()` - повторный прогон через логику
- `_replay_meta_decision()` - replay через MetaDecisionBrain
- `_replay_portfolio()` - replay через PortfolioBrain
- `_replay_position_sizing()` - replay через PositionSizer
- `_compare_decisions()` - сравнение решений

### ReplayReporter (replay_report.py)

**Методы:**
- `generate_report(results)` - генерация отчёта
- `format_report(report)` - форматирование в текст
- `export_to_dict(report)` - экспорт в словарь

---

## 🔧 ПРОЦЕСС REPLAY

### Шаг 1: Получение snapshot

```python
snapshot = snapshot_store.get_snapshot_by_id(snapshot_id)
```

### Шаг 2: Извлечение оригинального решения

```python
original_decision = _extract_original_decision(snapshot)
# Из decision_flags извлекаем:
# - meta_decision
# - portfolio_decision
# - position_allowed
# - position_size_usd
```

### Шаг 3: Восстановление контекста

```python
context = _restore_context(snapshot)
# Восстанавливаем:
# - confidence, entropy, score
# - states, indicators
# - portfolio_state
```

### Шаг 4: Повторный прогон через логику

```python
# 1. MetaDecisionBrain
meta_result = meta_brain.evaluate(...)

# 2. PortfolioBrain (если meta_result.allow_trading)
portfolio_result = portfolio_brain.evaluate(...)

# 3. PositionSizer (если portfolio_result.decision == ALLOW)
sizing_result = position_sizer.calculate(...)
```

### Шаг 5: Сравнение решений

```python
diff = _compare_decisions(original_decision, replayed_decision)
# Сравниваем:
# - decision_type
# - block_level
# - position_allowed
# - position_size_usd
```

### Шаг 6: Формирование результата

```python
result = ReplayResult(
    snapshot_id=snapshot.snapshot_id,
    original_decision=original_decision,
    replayed_decision=replayed_decision,
    diff=diff
)
```

---

## 📊 ПРИМЕРЫ ИСПОЛЬЗОВАНИЯ

### Пример 1: Replay одного snapshot

```python
from core.replay_engine import ReplayEngine
from core.signal_snapshot_store import SignalSnapshotStore

store = SignalSnapshotStore()
engine = ReplayEngine(store)

# Replay одного snapshot
result = engine.replay_snapshot(snapshot_id=123)

if result.is_changed():
    print(f"Decision changed: {result.diff.diff_summary}")
```

### Пример 2: Replay последних snapshot'ов

```python
# Replay последних 50 snapshot'ов
results = engine.replay_recent_snapshots(symbol="BTCUSDT", limit=50)

# Генерируем отчёт
from core.replay_report import ReplayReporter
reporter = ReplayReporter()
report = reporter.generate_report(results)

print(reporter.format_report(report))
```

### Пример 3: Replay списка snapshot'ов

```python
# Replay конкретных snapshot'ов
snapshot_ids = [1, 2, 3, 4, 5]
results = engine.replay_snapshots(snapshot_ids)

# Анализируем изменения
changed = [r for r in results if r.is_changed()]
print(f"Changed: {len(changed)}/{len(results)}")
```

### Пример 4: Экспорт отчёта

```python
report = reporter.generate_report(results)

# Экспорт в JSON
import json
report_dict = reporter.export_to_dict(report)
with open("replay_report.json", "w") as f:
    json.dump(report_dict, f, indent=2)
```

---

## 📋 REPLAYRESULT

### Структура:

```python
@dataclass
class ReplayResult:
    snapshot_id: int
    symbol: str
    timestamp: datetime
    original_decision: OriginalDecision
    replayed_decision: ReplayedDecision
    diff: DecisionDiff
    replay_timestamp: datetime
```

### DecisionDiff:

```python
@dataclass
class DecisionDiff:
    decision_changed: bool
    decision_type_changed: bool
    reason_changed: bool
    block_level_changed: bool
    position_allowed_changed: bool
    position_size_changed: bool
    original_decision_type: DecisionType
    replayed_decision_type: DecisionType
    original_reason: str
    replayed_reason: str
    size_diff_pct: Optional[float]
    diff_summary: str
```

---

## 📊 REPLAYREPORT

### Структура:

```python
@dataclass
class ReplayReport:
    total_snapshots: int
    changed_decisions: int
    unchanged_decisions: int
    change_rate: float  # Процент изменённых решений
    
    # Breakdown по причинам
    meta_decision_changes: int
    portfolio_changes: int
    position_sizing_changes: int
    risk_changes: int
    size_changes: int
    
    # Breakdown по типам решений
    decision_type_changes: Dict[str, int]
    
    # Детали изменений
    changed_results: List[ReplayResult]
```

---

## 🔄 ИНТЕГРАЦИЯ С DRIFT DETECTOR

Replay Engine готов к использованию в DriftDetector:

```python
# DriftDetector может использовать ReplayEngine для обнаружения дрейфа
results = engine.replay_recent_snapshots(limit=100)
report = reporter.generate_report(results)

# Если change_rate > threshold → обнаружен дрейф
if report.change_rate > 0.2:
    print("Drift detected: {:.1f}% decisions changed".format(report.change_rate * 100))
```

---

## ✅ ПОДТВЕРЖДЕНИЕ ТРЕБОВАНИЙ

### 1. Архитектура
- ✅ Файлы созданы (replay_models.py, replay_engine.py, replay_report.py)
- ✅ Класс `ReplayEngine` реализован
- ✅ Replay НЕ взаимодействует с live-рынком
- ✅ Replay НЕ меняет SystemState
- ✅ Replay работает ТОЛЬКО на данных snapshot
- ✅ Replay НЕ торгует
- ✅ Replay НЕ пишет в production-логи

### 2. Функциональность
- ✅ Принимает snapshot_id или список snapshot'ов
- ✅ Восстанавливает контекст
- ✅ Передаёт данные в MetaDecisionBrain, PositionSizer, DecisionCore
- ✅ Получает новое решение
- ✅ Сравнивает с original_decision
- ✅ Зафиксирует diff

### 3. Модели данных
- ✅ ReplayResult содержит все требуемые поля
- ✅ DecisionDiff содержит детальное сравнение
- ✅ ReplayReport агрегирует статистику

### 4. Дополнительно
- ✅ Использует dataclass
- ✅ Нет singleton
- ✅ Максимально детерминированно
- ✅ Код читаемый
- ✅ Подготовлена база для DriftDetector

### 5. Документация
- ✅ Docstring объясняет отличие Replay от Backtest
- ✅ Явно указано, что Replay — оффлайн-инструмент аудита

---

## 🎯 РЕЗУЛЬТАТ

### Достигнуто:
1. ✅ **ReplayEngine создан** - повторный прогон snapshot'ов
2. ✅ **ReplayModels созданы** - все необходимые dataclass'ы
3. ✅ **ReplayReporter создан** - генерация отчётов
4. ✅ **Интеграция с логикой** - MetaDecisionBrain, PositionSizer, PortfolioBrain
5. ✅ **Сравнение решений** - детальный diff

### Архитектура:
- ✅ Оффлайн-инструмент аудита
- ✅ Не влияет на торговую логику
- ✅ Детерминированный
- ✅ Готов к DriftDetector

---

*Replay Engine готов к использованию.*

