# Анализ публичного API SignalSnapshotStore

## 1. Публичный API для записи snapshot'ов

### Класс SignalSnapshotStore
- ✅ **Класс существует**: `SignalSnapshotStore`
- ❌ **Singleton/Get-функция**: НЕТ - нужно создавать экземпляр напрямую
- ✅ **Метод сохранения**: `save_snapshot()` - принимает отдельные параметры

### Сигнатура метода сохранения:
```python
def save_snapshot(
    self,
    timestamp: datetime,
    symbol: str,
    states: Dict[str, str],  # ⚠️ Строки, не MarketState enum!
    confidence: float,
    entropy: float,
    score: float,
    risk_level: str,  # ⚠️ Строка, не RiskLevel enum!
    indicators: Optional[Dict[str, Any]] = None,
    portfolio_state: Optional[Dict[str, Any]] = None,
    decision_flags: Optional[Dict[str, Any]] = None
) -> int  # Возвращает snapshot_id или -1 при ошибке
```

### Важно:
- `states` должны быть **строками** (используйте `state_to_string()`)
- `risk_level` должен быть **строкой** (используйте `.value`)
- Нет прямого метода для сохранения `SignalSnapshot` - нужно конвертировать вручную

## 2. Минимальный manual-test

```python
python - <<EOF
from datetime import datetime, UTC
from core.signal_snapshot import SignalSnapshot, SignalDecision, RiskLevel, VolatilityLevel
from core.market_state import MarketState, state_to_string
from core.decision_core import MarketRegime
from core.signal_snapshot_store import SignalSnapshotStore, load_last_snapshots

# 1. Создаём SignalSnapshot
snapshot = SignalSnapshot(
    timestamp=datetime.now(UTC),
    symbol="BTCUSDT",
    timeframe_anchor="15m",
    states={"15m": MarketState.D, "30m": MarketState.D},
    confidence=0.8,
    entropy=0.3,
    score=85,
    risk_level=RiskLevel.MEDIUM,
    decision=SignalDecision.ENTER,
    decision_reason="Test snapshot"
)

# 2. Конвертируем для сохранения
states_str = {k: state_to_string(v) for k, v in snapshot.states.items()}
indicators = {
    "entry": snapshot.entry,
    "tp": snapshot.tp,
    "sl": snapshot.sl,
    "directions": snapshot.directions,
    "score_details": snapshot.score_details
}
decision_flags = {
    "decision": snapshot.decision.value,
    "reason": snapshot.decision_reason,
    "reasons": snapshot.reasons
}

# 3. Сохраняем
store = SignalSnapshotStore()
snapshot_id = store.save_snapshot(
    timestamp=snapshot.timestamp,
    symbol=snapshot.symbol,
    states=states_str,
    confidence=snapshot.confidence,
    entropy=snapshot.entropy,
    score=float(snapshot.score),
    risk_level=snapshot.risk_level.value,
    indicators=indicators,
    portfolio_state={},
    decision_flags=decision_flags
)

print(f"✅ Saved snapshot_id: {snapshot_id}")

# 4. Загружаем
loaded = load_last_snapshots(limit=1)
if loaded:
    print(f"✅ Loaded {len(loaded)} snapshot(s)")
    latest = loaded[-1]
    print(f"   Symbol: {latest.symbol}")
    print(f"   Confidence: {latest.confidence}")
    print(f"   Entropy: {latest.entropy}")
    assert latest.symbol == snapshot.symbol, "Symbol mismatch"
    assert abs(latest.confidence - snapshot.confidence) < 0.01, "Confidence mismatch"
    print("✅ All checks passed!")
else:
    print("❌ No snapshots loaded")
EOF
```

## 3. Проверка использования в runtime

### ✅ SignalSnapshotStore НЕ импортируется в runtime-логику:
- `execution/gatekeeper.py` - НЕТ импорта
- `signal_generator.py` - НЕТ импорта
- `runner.py` - НЕТ импорта

### ✅ Запись snapshot происходит только после принятого решения:
- Snapshot должен сохраняться ПОСЛЕ всех проверок (MetaDecisionBrain, DecisionCore, PortfolioBrain, PositionSizer)
- В текущей реализации сохранение snapshot'ов не интегрировано в gatekeeper

### ✅ Replay / Drift читают через load_last_snapshots():
- `core/replay_engine.py` - НЕ использует `load_last_snapshots` (принимает список SignalSnapshot напрямую)
- `core/drift_detector.py` - использует `SignalSnapshotRecord` (старый формат)

## Рекомендации

1. **Для сохранения snapshot в runtime:**
   - Создать helper-функцию `save_signal_snapshot(snapshot: SignalSnapshot) -> int`
   - Или добавить метод `SignalSnapshotStore.save_from_snapshot(snapshot: SignalSnapshot)`

2. **Для использования в Replay/Drift:**
   - Обновить `drift_detector.py` для использования `load_last_snapshots()`
   - ReplayEngine уже работает с `SignalSnapshot` напрямую (корректно)

3. **Архитектурная чистота:**
   - ✅ SnapshotStore не используется в runtime-логике
   - ✅ Запись происходит отдельно от принятия решений
   - ✅ Чтение через единый API `load_last_snapshots()`

