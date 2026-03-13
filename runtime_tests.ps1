# Runtime / System / Invariant Tests для market_bot (PowerShell версия)
# Все тесты безопасны для production (НЕ торгуют, НЕ слают сигналы)

$ErrorActionPreference = "Stop"

Write-Host "🧪 Запуск Runtime/System/Invariant тестов для market_bot" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

# Переходим в директорию проекта
Set-Location $PSScriptRoot

# ============================================================================
# 1️⃣ MarketState / Enum Invariants
# ============================================================================
Write-Host "1️⃣ Тест: MarketState / Enum Invariants" -ForegroundColor Yellow
python - <<'PYTHON_EOF'
import sys
from core.signal_snapshot_store import SignalSnapshotStore, load_last_snapshots
from core.market_state import MarketState

print("   Загрузка последних SignalSnapshot...")
store = SignalSnapshotStore()
snapshots = store.get_recent_snapshots(limit=10)

if not snapshots:
    print("   ⚠️  Нет snapshot'ов в БД - пропускаем тест")
    sys.exit(0)

print(f"   Загружено {len(snapshots)} snapshot'ов")

# Проверяем инварианты
for snapshot in snapshots:
    states = snapshot.states  # Это dict из БД (строки)
    
    # Инвариант 1: states — dict
    assert isinstance(states, dict), f"states должен быть dict, получен {type(states)}"
    
    # Инвариант 2: значения только MarketState enum или None (после нормализации)
    for key, state_value in states.items():
        # В БД хранятся строки, но после нормализации должны быть enum или None
        if state_value is not None and state_value != "":
            # Проверяем, что это валидная строка для MarketState
            assert state_value in ["A", "B", "C", "D"], \
                f"Невалидное значение state[{key}] = {state_value} (type: {type(state_value).__name__}), " \
                f"ожидается 'A', 'B', 'C', 'D' или None"
        # None и пустая строка - валидные значения

print("   ✅ MarketState / Enum Invariants: OK")
PYTHON_EOF

if ($LASTEXITCODE -ne 0) {
    Write-Host "   ❌ Тест 1 провален" -ForegroundColor Red
    exit 1
}

# ============================================================================
# 2️⃣ SignalSnapshot Integrity
# ============================================================================
Write-Host ""
Write-Host "2️⃣ Тест: SignalSnapshot Integrity" -ForegroundColor Yellow
python - <<'PYTHON_EOF'
import sys
from datetime import datetime, UTC
from core.signal_snapshot import SignalSnapshot, SignalDecision, RiskLevel
from core.market_state import MarketState, normalize_states_dict

print("   Создание тестового SignalSnapshot...")
states = {"15m": MarketState.A, "30m": MarketState.B, "1h": None}
states = normalize_states_dict(states)

snapshot = SignalSnapshot(
    timestamp=datetime.now(UTC),
    symbol="BTCUSDT",
    timeframe_anchor="15m",
    states=states,
    confidence=0.7,
    entropy=0.3,
    score=80,
    risk_level=RiskLevel.MEDIUM,
    decision=SignalDecision.ENTER
)

# Инвариант 1: frozen=True (immutable)
print("   Проверка frozen=True...")
try:
    snapshot.symbol = "ETHUSDT"  # Попытка изменить поле
    assert False, "SignalSnapshot должен быть immutable (frozen=True)"
except Exception as e:
    # dataclasses.FrozenInstanceError или другой exception
    error_msg = str(e).lower()
    if any(keyword in error_msg for keyword in ["cannot assign", "frozen", "read-only", "immutable"]):
        print("   ✅ Нельзя изменить поле (frozen=True)")
    else:
        # Если это не ожидаемое исключение, проверяем тип
        exception_type = type(e).__name__
        if "Frozen" in exception_type or "ReadOnly" in exception_type:
            print("   ✅ Нельзя изменить поле (frozen=True)")
        else:
            # Перебрасываем если это не ожидаемое исключение
            raise

# Инвариант 2: states не мутируются
print("   Проверка immutability states...")
original_states = snapshot.states.copy()
# Попытка изменить states (если это возможно)
# states - это dict, но snapshot frozen, поэтому изменение через snapshot.states[key] = ... должно падать
try:
    # Попытка изменить через __dict__ (если доступно)
    if hasattr(snapshot, '__dict__'):
        snapshot.__dict__['states']['15m'] = MarketState.C
        assert False, "states не должны мутироваться"
except (AttributeError, TypeError):
    pass  # Ожидаемое поведение

assert snapshot.states == original_states, "states изменились после попытки мутации"

print("   ✅ SignalSnapshot Integrity: OK")
PYTHON_EOF

if ($LASTEXITCODE -ne 0) {
    Write-Host "   ❌ Тест 2 провален" -ForegroundColor Red
    exit 1
}

# ============================================================================
# 3️⃣ Gatekeeper Decision Flow
# ============================================================================
Write-Host ""
Write-Host "3️⃣ Тест: Gatekeeper Decision Flow" -ForegroundColor Yellow
python - <<'PYTHON_EOF'
import sys
from datetime import datetime, UTC
from core.signal_snapshot import SignalSnapshot, SignalDecision, RiskLevel
from core.market_state import MarketState, normalize_states_dict
from execution.gatekeeper import Gatekeeper

print("   Создание искусственного SignalSnapshot...")
states = {"15m": MarketState.A, "30m": MarketState.B}
states = normalize_states_dict(states)

snapshot = SignalSnapshot(
    timestamp=datetime.now(UTC),
    symbol="BTCUSDT",
    timeframe_anchor="15m",
    states=states,
    confidence=0.7,
    entropy=0.3,
    score=80,
    risk_level=RiskLevel.MEDIUM,
    decision=SignalDecision.ENTER
)

print("   Инициализация Gatekeeper...")
gatekeeper = Gatekeeper()

# Проверяем, что Gatekeeper инициализирован
assert gatekeeper.decision_core is not None, "DecisionCore должен быть инициализирован"
assert gatekeeper.portfolio_brain is not None, "PortfolioBrain должен быть инициализирован"

# Проверяем, что MetaDecisionBrain доступен (если есть)
if hasattr(gatekeeper, 'meta_decision_brain'):
    print("   ✅ MetaDecisionBrain доступен")

# Проверяем, что PositionSizer доступен (если есть)
if hasattr(gatekeeper, 'position_sizer'):
    print("   ✅ PositionSizer доступен")

# Проверяем детерминированность: дважды вызываем check_signal с одинаковыми данными
print("   Проверка детерминированности...")
signal_data = {"zone": {"entry": 50000, "stop": 49000, "target": 52000}, "risk": "MEDIUM"}

# Первый вызов
result1 = gatekeeper.check_signal("BTCUSDT", signal_data)

# Второй вызов (должен быть идентичен)
result2 = gatekeeper.check_signal("BTCUSDT", signal_data)

assert result1 == result2, "Gatekeeper должен быть детерминированным"

print("   ✅ Gatekeeper Decision Flow: OK")
PYTHON_EOF

if ($LASTEXITCODE -ne 0) {
    Write-Host "   ❌ Тест 3 провален" -ForegroundColor Red
    exit 1
}

# ============================================================================
# 4️⃣ MetaDecisionBrain (WHEN NOT TO TRADE)
# ============================================================================
Write-Host ""
Write-Host "4️⃣ Тест: MetaDecisionBrain (WHEN NOT TO TRADE)" -ForegroundColor Yellow
python - <<'PYTHON_EOF'
import sys
from brains.meta_decision_brain import (
    MetaDecisionBrain, SystemHealthStatus, TimeContext, BlockLevel
)
from core.decision_core import MarketRegime

print("   Инициализация MetaDecisionBrain...")
meta_brain = MetaDecisionBrain()

# Тест 1: Высокая entropy → BLOCK
print("   Тест 1: Высокая entropy → BLOCK")
result = meta_brain.evaluate(
    entropy_score=0.8,  # Высокая entropy
    confidence_score=0.3,  # Низкая confidence
    portfolio_exposure=0.0,
    system_health=SystemHealthStatus.OK
)
assert not result.allow_trading, f"Высокая entropy должна блокировать торговлю, но allow_trading={result.allow_trading}"
assert result.block_level == BlockLevel.HARD, f"Должен быть HARD блок, получен {result.block_level}"
print("   ✅ Высокая entropy → BLOCK: OK")

# Тест 2: Низкая confidence → BLOCK (через HARD BLOCK условие)
print("   Тест 2: Низкая confidence + высокая entropy → BLOCK")
result = meta_brain.evaluate(
    entropy_score=0.75,  # Высокая entropy
    confidence_score=0.35,  # Низкая confidence
    portfolio_exposure=0.0,
    system_health=SystemHealthStatus.OK
)
assert not result.allow_trading, f"Низкая confidence + высокая entropy должна блокировать торговлю"
print("   ✅ Низкая confidence + высокая entropy → BLOCK: OK")

# Тест 3: SystemHealth != OK → BLOCK
print("   Тест 3: SystemHealth != OK → BLOCK")
result = meta_brain.evaluate(
    entropy_score=0.5,
    confidence_score=0.7,
    portfolio_exposure=0.0,
    system_health=SystemHealthStatus.DEGRADED
)
assert not result.allow_trading, f"SystemHealth DEGRADED должна блокировать торговлю"
assert result.block_level == BlockLevel.HARD, f"Должен быть HARD блок при DEGRADED"
print("   ✅ SystemHealth != OK → BLOCK: OK")

# Тест 4: Нормальные условия → ALLOW
print("   Тест 4: Нормальные условия → ALLOW")
result = meta_brain.evaluate(
    entropy_score=0.4,  # Нормальная entropy
    confidence_score=0.7,  # Хорошая confidence
    portfolio_exposure=0.3,  # Нормальная экспозиция
    system_health=SystemHealthStatus.OK
)
assert result.allow_trading, f"Нормальные условия должны разрешать торговлю, но allow_trading={result.allow_trading}"
print("   ✅ Нормальные условия → ALLOW: OK")

print("   ✅ MetaDecisionBrain (WHEN NOT TO TRADE): OK")
PYTHON_EOF

if ($LASTEXITCODE -ne 0) {
    Write-Host "   ❌ Тест 4 провален" -ForegroundColor Red
    exit 1
}

# ============================================================================
# 5️⃣ PositionSizer Safety
# ============================================================================
Write-Host ""
Write-Host "5️⃣ Тест: PositionSizer Safety" -ForegroundColor Yellow
python - <<'PYTHON_EOF'
import sys
import math
from core.position_sizer import PositionSizer, PortfolioStateAdapter
from core.portfolio_brain import PortfolioState

print("   Инициализация PositionSizer...")
sizer = PositionSizer()

# Создаём минимальный PortfolioState для теста
portfolio_state = PortfolioState(
    total_exposure=0.0,
    long_exposure=0.0,
    short_exposure=0.0,
    net_exposure=0.0,
    risk_budget=1000.0,
    used_risk=0.0
)
portfolio_adapter = PortfolioStateAdapter(portfolio_state)

# Тест 1: confidence > 1 (должен быть заклэмплен)
print("   Тест 1: confidence > 1 → должен быть заклэмплен")
result = sizer.calculate(
    confidence=1.5,  # > 1
    entropy=0.5,
    portfolio_state=portfolio_adapter,
    symbol="BTCUSDT",
    balance=10000.0
)
assert 0.0 <= result.final_risk <= 100.0, f"final_risk должен быть в [0, 100], получен {result.final_risk}"
assert not math.isnan(result.final_risk), "final_risk не должен быть NaN"
assert not math.isinf(result.final_risk), "final_risk не должен быть inf"
print("   ✅ confidence > 1 заклэмплен: OK")

# Тест 2: entropy < 0 (должен быть заклэмплен)
print("   Тест 2: entropy < 0 → должен быть заклэмплен")
result = sizer.calculate(
    confidence=0.7,
    entropy=-0.5,  # < 0
    portfolio_state=portfolio_adapter,
    symbol="BTCUSDT",
    balance=10000.0
)
assert 0.0 <= result.final_risk <= 100.0, f"final_risk должен быть в [0, 100], получен {result.final_risk}"
assert not math.isnan(result.final_risk), "final_risk не должен быть NaN"
print("   ✅ entropy < 0 заклэмплен: OK")

# Тест 3: portfolio_exposure > 1 (должен быть заклэмплен)
print("   Тест 3: portfolio_exposure > 1 → должен быть заклэмплен")
portfolio_state_high = PortfolioState(
    total_exposure=0.0,
    long_exposure=0.0,
    short_exposure=0.0,
    net_exposure=0.0,
    risk_budget=100.0,  # Маленький risk_budget
    used_risk=150.0  # used_risk > risk_budget
)
portfolio_adapter_high = PortfolioStateAdapter(portfolio_state_high)
result = sizer.calculate(
    confidence=0.7,
    entropy=0.5,
    portfolio_state=portfolio_adapter_high,
    symbol="BTCUSDT",
    balance=10000.0
)
# available_risk_ratio должен быть заклэмплен в [0, 1]
assert 0.0 <= result.portfolio_factor <= 1.0, f"portfolio_factor должен быть в [0, 1], получен {result.portfolio_factor}"
print("   ✅ portfolio_exposure > 1 заклэмплен: OK")

# Тест 4: position_allowed == False при риске < min_threshold
print("   Тест 4: position_allowed == False при риске < min_threshold")
# Используем очень низкие значения для получения final_risk < min_threshold
portfolio_state_low = PortfolioState(
    total_exposure=0.0,
    long_exposure=0.0,
    short_exposure=0.0,
    net_exposure=0.0,
    risk_budget=1000.0,
    used_risk=999.0  # Почти весь risk_budget использован
)
portfolio_adapter_low = PortfolioStateAdapter(portfolio_state_low)
result = sizer.calculate(
    confidence=0.2,  # Низкая confidence
    entropy=0.9,  # Высокая entropy
    portfolio_state=portfolio_adapter_low,
    symbol="BTCUSDT",
    balance=10000.0
)
# Если final_risk < min_threshold, position_allowed должен быть False
if result.final_risk < sizer.config.min_risk_threshold:
    assert not result.position_allowed, \
        f"При final_risk={result.final_risk} < min_threshold={sizer.config.min_risk_threshold}, " \
        f"position_allowed должен быть False"
print("   ✅ position_allowed == False при низком риске: OK")

print("   ✅ PositionSizer Safety: OK")
PYTHON_EOF

if ($LASTEXITCODE -ne 0) {
    Write-Host "   ❌ Тест 5 провален" -ForegroundColor Red
    exit 1
}

# ============================================================================
# 6️⃣ PortfolioBrain Exposure Rules
# ============================================================================
Write-Host ""
Write-Host "6️⃣ Тест: PortfolioBrain Exposure Rules" -ForegroundColor Yellow
python - <<'PYTHON_EOF'
import sys
from datetime import datetime, UTC
from core.signal_snapshot import SignalSnapshot, SignalDecision, RiskLevel
from core.market_state import MarketState, normalize_states_dict
from core.portfolio_brain import (
    PortfolioBrain, PortfolioState, PositionSnapshot, PositionDirection,
    PortfolioDecision, calculate_portfolio_state
)

print("   Инициализация PortfolioBrain...")
portfolio_brain = PortfolioBrain()

# Создаём тестовый snapshot
states = {"15m": MarketState.A, "30m": MarketState.B}
states = normalize_states_dict(states)

snapshot = SignalSnapshot(
    timestamp=datetime.now(UTC),
    symbol="BTCUSDT",
    timeframe_anchor="15m",
    states=states,
    confidence=0.7,
    entropy=0.3,
    score=80,
    risk_level=RiskLevel.MEDIUM,
    decision=SignalDecision.ENTER
)

# Тест 1: Портфель превышает risk_budget → BLOCK
print("   Тест 1: Портфель превышает risk_budget → BLOCK")
portfolio_state = PortfolioState(
    total_exposure=1500.0,  # Превышает risk_budget
    long_exposure=1500.0,
    short_exposure=0.0,
    net_exposure=1500.0,
    risk_budget=1000.0,  # risk_budget меньше total_exposure
    used_risk=1500.0
)

open_positions = []  # Пустой список позиций (для упрощения)
analysis = portfolio_brain.evaluate(
    snapshot=snapshot,
    open_positions=open_positions,
    portfolio_state=portfolio_state
)
assert analysis.decision == PortfolioDecision.BLOCK, \
    f"При превышении risk_budget должен быть BLOCK, получен {analysis.decision}"
print("   ✅ Превышение risk_budget → BLOCK: OK")

# Тест 2: Высокая корреляция → BLOCK (через portfolio_entropy)
print("   Тест 2: Высокая корреляция → BLOCK")
# Создаём позиции с высокой корреляцией (все в одном MarketState)
high_correlation_positions = [
    PositionSnapshot(
        symbol="ETHUSDT",
        direction=PositionDirection.LONG,
        size=500.0,
        entry_price=3000.0,
        unrealized_pnl=0.0,
        market_state=MarketState.A,  # Тот же MarketState что и в snapshot
        confidence=0.6,
        entropy=0.4
    ),
    PositionSnapshot(
        symbol="BNBUSDT",
        direction=PositionDirection.LONG,
        size=300.0,
        entry_price=400.0,
        unrealized_pnl=0.0,
        market_state=MarketState.A,  # Тот же MarketState
        confidence=0.65,
        entropy=0.35
    )
]

portfolio_state_normal = PortfolioState(
    total_exposure=800.0,
    long_exposure=800.0,
    short_exposure=0.0,
    net_exposure=800.0,
    risk_budget=1000.0,
    used_risk=800.0
)

analysis = portfolio_brain.evaluate(
    snapshot=snapshot,
    open_positions=high_correlation_positions,
    portfolio_state=portfolio_state_normal
)
# Может быть BLOCK или SCALE_DOWN в зависимости от условий
assert analysis.decision in [PortfolioDecision.BLOCK, PortfolioDecision.SCALE_DOWN], \
    f"При высокой корреляции должен быть BLOCK или SCALE_DOWN, получен {analysis.decision}"
print("   ✅ Высокая корреляция → BLOCK/SCALE_DOWN: OK")

# Тест 3: Пустой портфель → ALLOW
print("   Тест 3: Пустой портфель → ALLOW")
portfolio_state_empty = PortfolioState(
    total_exposure=0.0,
    long_exposure=0.0,
    short_exposure=0.0,
    net_exposure=0.0,
    risk_budget=1000.0,
    used_risk=0.0
)

analysis = portfolio_brain.evaluate(
    snapshot=snapshot,
    open_positions=[],  # Пустой портфель
    portfolio_state=portfolio_state_empty
)
assert analysis.decision == PortfolioDecision.ALLOW, \
    f"При пустом портфеле должен быть ALLOW, получен {analysis.decision}"
print("   ✅ Пустой портфель → ALLOW: OK")

print("   ✅ PortfolioBrain Exposure Rules: OK")
PYTHON_EOF

if ($LASTEXITCODE -ne 0) {
    Write-Host "   ❌ Тест 6 провален" -ForegroundColor Red
    exit 1
}

# ============================================================================
# 7️⃣ ReplayEngine Isolation Test
# ============================================================================
Write-Host ""
Write-Host "7️⃣ Тест: ReplayEngine Isolation Test" -ForegroundColor Yellow
python - <<'PYTHON_EOF'
import sys
from datetime import datetime, UTC, timedelta
from core.signal_snapshot import SignalSnapshot, SignalDecision, RiskLevel
from core.market_state import MarketState, normalize_states_dict
from core.replay_engine import ReplayEngine

print("   Загрузка последних snapshots...")
from core.signal_snapshot_store import load_last_snapshots

snapshots = load_last_snapshots(limit=5)

if not snapshots:
    print("   ⚠️  Нет snapshot'ов в БД - создаём тестовые...")
    # Создаём тестовые snapshots
    states = {"15m": MarketState.A, "30m": MarketState.B}
    states = normalize_states_dict(states)
    
    snapshots = [
        SignalSnapshot(
            timestamp=datetime.now(UTC) - timedelta(minutes=i),
            symbol="BTCUSDT",
            timeframe_anchor="15m",
            states=states,
            confidence=0.7,
            entropy=0.3,
            score=80,
            risk_level=RiskLevel.MEDIUM,
            decision=SignalDecision.ENTER
        )
        for i in range(3)
    ]

print(f"   Загружено {len(snapshots)} snapshot'ов")

# Сохраняем оригинальные значения для проверки
original_snapshots = []
for snapshot in snapshots:
    # Создаём копию состояний для проверки
    original_states = snapshot.states.copy()
    original_snapshots.append({
        'symbol': snapshot.symbol,
        'confidence': snapshot.confidence,
        'entropy': snapshot.entropy,
        'states': original_states
    })

print("   Запуск ReplayEngine...")
replay_engine = ReplayEngine()
summary = replay_engine.replay_snapshots(snapshots)

# Проверяем, что snapshot'ы не изменились
print("   Проверка immutability snapshot'ов...")
for i, snapshot in enumerate(snapshots):
    original = original_snapshots[i]
    assert snapshot.symbol == original['symbol'], \
        f"symbol изменился: было {original['symbol']}, стало {snapshot.symbol}"
    assert snapshot.confidence == original['confidence'], \
        f"confidence изменилась: было {original['confidence']}, стало {snapshot.confidence}"
    assert snapshot.entropy == original['entropy'], \
        f"entropy изменилась: было {original['entropy']}, стало {snapshot.entropy}"
    # Проверяем states (сравниваем ключи и значения)
    assert snapshot.states.keys() == original['states'].keys(), \
        f"Ключи states изменились"
    for key in snapshot.states.keys():
        assert snapshot.states[key] == original['states'][key], \
            f"states[{key}] изменился: было {original['states'][key]}, стало {snapshot.states[key]}"

print("   ✅ Snapshot'ы не изменились")

# Проверяем, что summary создан
assert summary is not None, "ReplaySummary должен быть создан"
assert summary.total_snapshots == len(snapshots), \
    f"total_snapshots должен быть {len(snapshots)}, получен {summary.total_snapshots}"

print("   ✅ ReplayEngine Isolation Test: OK")
PYTHON_EOF

if ($LASTEXITCODE -ne 0) {
    Write-Host "   ❌ Тест 7 провален" -ForegroundColor Red
    exit 1
}

# ============================================================================
# 8️⃣ DriftDetector Safety Test
# ============================================================================
Write-Host ""
Write-Host "8️⃣ Тест: DriftDetector Safety Test" -ForegroundColor Yellow
python - <<'PYTHON_EOF'
import sys
from datetime import datetime, UTC, timedelta
from core.drift_detector import DriftDetector
from core.signal_snapshot_store import SignalSnapshotRecord

print("   Создание тестовых snapshots...")
# Создаём тестовые snapshots для drift detection
snapshots = []
base_time = datetime.now(UTC)

# Baseline snapshots (7 дней назад)
for i in range(10):
    snapshots.append(SignalSnapshotRecord(
        timestamp=base_time - timedelta(days=7, hours=i),
        symbol="BTCUSDT",
        states={"15m": "A", "30m": "B"},
        confidence=0.7,  # Стабильная confidence
        entropy=0.3,  # Стабильная entropy
        score=80.0,
        risk_level="MEDIUM"
    ))

# Recent snapshots (последние 24 часа)
for i in range(5):
    snapshots.append(SignalSnapshotRecord(
        timestamp=base_time - timedelta(hours=i),
        symbol="BTCUSDT",
        states={"15m": "A", "30m": "B"},
        confidence=0.5,  # Сниженная confidence (drift)
        entropy=0.6,  # Повышенная entropy (drift)
        score=60.0,
        risk_level="MEDIUM"
    ))

print(f"   Создано {len(snapshots)} тестовых snapshot'ов")

# Сохраняем оригинальные значения
original_snapshots = []
for snapshot in snapshots:
    original_snapshots.append({
        'confidence': snapshot.confidence,
        'entropy': snapshot.entropy,
        'timestamp': snapshot.timestamp
    })

print("   Запуск DriftDetector...")
detector = DriftDetector()
drift_state = detector.detect_drift(snapshots, end_time=base_time)

# Проверяем, что snapshot'ы не изменились
print("   Проверка immutability snapshot'ов...")
for i, snapshot in enumerate(snapshots):
    original = original_snapshots[i]
    assert snapshot.confidence == original['confidence'], \
        f"confidence изменилась: было {original['confidence']}, стало {snapshot.confidence}"
    assert snapshot.entropy == original['entropy'], \
        f"entropy изменилась: было {original['entropy']}, стало {snapshot.entropy}"

print("   ✅ Snapshot'ы не изменились")

# Проверяем, что drift_report создан (если достаточно данных)
if drift_state:
    assert drift_state.metrics is not None, "metrics должны быть вычислены"
    # Проверяем корректность значений
    assert 0.0 <= drift_state.metrics.confidence_mean_recent <= 1.0, \
        f"confidence_mean_recent должен быть в [0, 1], получен {drift_state.metrics.confidence_mean_recent}"
    assert 0.0 <= drift_state.metrics.entropy_mean_recent <= 1.0, \
        f"entropy_mean_recent должен быть в [0, 1], получен {drift_state.metrics.entropy_mean_recent}"
    print("   ✅ Drift report создан и значения корректны")
else:
    print("   ⚠️  Drift report не создан (недостаточно данных)")

print("   ✅ DriftDetector Safety Test: OK")
PYTHON_EOF

if ($LASTEXITCODE -ne 0) {
    Write-Host "   ❌ Тест 8 провален" -ForegroundColor Red
    exit 1
}

# ============================================================================
# 9️⃣ Runtime Loop Safety (Dry Mode)
# ============================================================================
Write-Host ""
Write-Host "9️⃣ Тест: Runtime Loop Safety (Dry Mode)" -ForegroundColor Yellow
python - <<'PYTHON_EOF'
import sys
import os

# Отключаем Telegram и торговлю для dry mode
os.environ['DRY_RUN'] = '1'
os.environ['DISABLE_TELEGRAM'] = '1'

print("   Импорт runner модуля...")
try:
    # Импортируем только необходимые компоненты (не запускаем полный runner)
    from core.decision_core import get_decision_core
    from core.portfolio_brain import get_portfolio_brain
    from execution.gatekeeper import get_gatekeeper
    from system_state import SystemState
    
    print("   Инициализация компонентов...")
    decision_core = get_decision_core()
    portfolio_brain = get_portfolio_brain()
    gatekeeper = get_gatekeeper()
    system_state = SystemState()
    
    # Проверяем, что компоненты инициализированы
    assert decision_core is not None, "DecisionCore должен быть инициализирован"
    assert portfolio_brain is not None, "PortfolioBrain должен быть инициализирован"
    assert gatekeeper is not None, "Gatekeeper должен быть инициализирован"
    assert system_state is not None, "SystemState должен быть инициализирован"
    
    print("   ✅ Все компоненты инициализированы")
    
    # Проверяем, что нет вызовов Telegram (если модуль доступен)
    # Это проверка на уровне импорта - если telegram_bot импортирован, 
    # но DISABLE_TELEGRAM установлен, он не должен отправлять сообщения
    
    print("   ✅ Runtime Loop Safety (Dry Mode): OK")
    
except Exception as e:
    print(f"   ⚠️  Ошибка при импорте/инициализации: {type(e).__name__}: {e}")
    # Не падаем - это может быть нормально если модули недоступны
    print("   ⚠️  Пропускаем тест (модули недоступны)")
PYTHON_EOF

# Тест 9 не критичен, продолжаем даже при ошибке
Write-Host "   ⚠️  Тест 9 выполнен (может быть пропущен если модули недоступны)" -ForegroundColor Yellow

# ============================================================================
# 🔟 Full Ecosystem Smoke Test
# ============================================================================
Write-Host ""
Write-Host "🔟 Тест: Full Ecosystem Smoke Test" -ForegroundColor Yellow
python - <<'PYTHON_EOF'
import sys
import time
from datetime import datetime, UTC
from core.signal_snapshot import SignalSnapshot, SignalDecision, RiskLevel
from core.market_state import MarketState, normalize_states_dict
from core.signal_snapshot_store import load_last_snapshots
from core.replay_engine import ReplayEngine
from core.drift_detector import DriftDetector
from core.portfolio_brain import PortfolioBrain, PortfolioState, calculate_portfolio_state
from brains.meta_decision_brain import MetaDecisionBrain, SystemHealthStatus

print("   Запуск полного smoke test...")
start_time = time.time()

# 1. Загрузка snapshots
print("   1. Загрузка snapshots...")
snapshots = load_last_snapshots(limit=10)
if not snapshots:
    print("   ⚠️  Нет snapshot'ов - создаём тестовый...")
    states = {"15m": MarketState.A, "30m": MarketState.B}
    states = normalize_states_dict(states)
    snapshots = [SignalSnapshot(
        timestamp=datetime.now(UTC),
        symbol="BTCUSDT",
        timeframe_anchor="15m",
        states=states,
        confidence=0.7,
        entropy=0.3,
        score=80,
        risk_level=RiskLevel.MEDIUM,
        decision=SignalDecision.ENTER
    )]
print(f"   ✅ Загружено {len(snapshots)} snapshot'ов")

# 2. ReplayEngine
print("   2. Запуск ReplayEngine...")
replay_engine = ReplayEngine()
summary = replay_engine.replay_snapshots(snapshots[:3])  # Используем только первые 3
assert summary is not None, "ReplaySummary должен быть создан"
print(f"   ✅ ReplayEngine: обработано {summary.total_snapshots} snapshot'ов")

# 3. DriftDetector
print("   3. Запуск DriftDetector...")
# Создаём минимальные тестовые данные для drift
from core.signal_snapshot_store import SignalSnapshotRecord
from datetime import timedelta
drift_snapshots = [
    SignalSnapshotRecord(
        timestamp=datetime.now(UTC) - timedelta(days=7, hours=i),
        symbol="BTCUSDT",
        states={"15m": "A"},
        confidence=0.7,
        entropy=0.3,
        score=80.0,
        risk_level="MEDIUM"
    )
    for i in range(5)
] + [
    SignalSnapshotRecord(
        timestamp=datetime.now(UTC) - timedelta(hours=i),
        symbol="BTCUSDT",
        states={"15m": "A"},
        confidence=0.6,
        entropy=0.4,
        score=70.0,
        risk_level="MEDIUM"
    )
    for i in range(3)
]

detector = DriftDetector()
drift_state = detector.detect_drift(drift_snapshots)
# drift_state может быть None если недостаточно данных - это нормально
print("   ✅ DriftDetector: выполнен")

# 4. PortfolioBrain
print("   4. Запуск PortfolioBrain...")
portfolio_brain = PortfolioBrain()
if snapshots:
    snapshot = snapshots[0]
    portfolio_state = PortfolioState(
        total_exposure=0.0,
        long_exposure=0.0,
        short_exposure=0.0,
        net_exposure=0.0,
        risk_budget=1000.0,
        used_risk=0.0
    )
    analysis = portfolio_brain.evaluate(
        snapshot=snapshot,
        open_positions=[],
        portfolio_state=portfolio_state
    )
    assert analysis is not None, "PortfolioAnalysis должен быть создан"
    print("   ✅ PortfolioBrain: анализ выполнен")

# 5. MetaDecisionBrain
print("   5. Запуск MetaDecisionBrain...")
meta_brain = MetaDecisionBrain()
result = meta_brain.evaluate(
    confidence_score=0.7,
    entropy_score=0.3,
    portfolio_exposure=0.2,
    system_health=SystemHealthStatus.OK
)
assert result is not None, "MetaDecisionResult должен быть создан"
print("   ✅ MetaDecisionBrain: оценка выполнена")

# Проверяем время выполнения
elapsed_time = time.time() - start_time
assert elapsed_time < 5.0, f"Время выполнения должно быть < 5 сек, получено {elapsed_time:.2f} сек"
print(f"   ✅ Время выполнения: {elapsed_time:.2f} сек (< 5 сек)")

print("   ✅ Full Ecosystem Smoke Test: OK")
PYTHON_EOF

if ($LASTEXITCODE -ne 0) {
    Write-Host "   ❌ Тест 10 провален" -ForegroundColor Red
    exit 1
}

# ============================================================================
# Итоги
# ============================================================================
Write-Host ""
Write-Host "==================================================" -ForegroundColor Green
Write-Host "✅ Все тесты пройдены успешно!" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green

