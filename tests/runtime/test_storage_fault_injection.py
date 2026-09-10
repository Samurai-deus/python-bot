"""
Внедрённый сбой хранилища снимков (FAULT_INJECT_STORAGE_FAILURE).

Перенесено из корневого test_storage_fault_injection.py (задача 1.6). Флаг
ставится monkeypatch'ем на модуль, а не в os.environ при импорте: иначе он
протекал бы во все тесты сессии. Проверки safe_mode из старого скрипта не
перенесены — он выставлял safe_mode вручную и проверял сам себя; блокировку
торговли в safe_mode проверяет tests/test_decision_core.py.
"""
import asyncio
import copy
from datetime import datetime, UTC

import pytest

import core.signal_snapshot_store as store
from core.decision_core import MarketRegime
from core.market_state import MarketState
from core.signal_snapshot import SignalSnapshot, SignalDecision, RiskLevel, VolatilityLevel

FAULT = "FAULT_INJECTION: storage_failure"


@pytest.fixture
def storage_failure(monkeypatch):
    monkeypatch.setattr(store, "FAULT_INJECT_STORAGE_FAILURE", True)


def _signal_snapshot():
    return SignalSnapshot(
        timestamp=datetime.now(UTC),
        symbol="BTCUSDT",
        timeframe_anchor="15m",
        states={"5m": MarketState.A, "15m": MarketState.D, "30m": MarketState.A,
                "1h": MarketState.B, "4h": MarketState.A},
        market_regime=MarketRegime(trend_type="TREND", volatility_level="MEDIUM",
                                   risk_sentiment="RISK_ON", confidence=0.7),
        volatility_level=VolatilityLevel.NORMAL,
        correlation_level=0.5,
        score=75,
        score_max=125,
        confidence=0.65,
        entropy=0.35,
        risk_level=RiskLevel.MEDIUM,
        recommended_leverage=5.0,
        entry=50000.0,
        tp=51000.0,
        sl=49500.0,
        decision=SignalDecision.ENTER,
        decision_reason="Test signal",
        directions={"15m": "UP", "30m": "UP", "1h": "UP", "4h": "UP"},
        score_details={},
        reasons=["Test"],
    )


def test_system_state_store_fails_before_touching_data(storage_failure):
    data = {"timestamp": "2026-09-10T00:00:00+00:00", "system_health": {"safe_mode": False}}
    before = copy.deepcopy(data)
    with pytest.raises(IOError, match=FAULT):
        store.SystemStateSnapshotStore.save(data)
    with pytest.raises(IOError, match=FAULT):
        store.SystemStateSnapshotStore.load_latest()
    assert data == before, "сбой срабатывает до любых изменений входных данных"


def test_signal_snapshot_store_fails(storage_failure):
    snapshot = _signal_snapshot()
    with pytest.raises(IOError, match=FAULT):
        store.SignalSnapshotStore.save(snapshot)
    with pytest.raises(IOError, match=FAULT):
        store.SignalSnapshotStore.load_latest("BTCUSDT")
    assert snapshot.symbol == "BTCUSDT" and snapshot.confidence == 0.65


async def test_failure_comes_before_a_timeout(storage_failure):
    """Сбой срабатывает сразу, а не оборачивается таймаутом вызова в потоке."""
    with pytest.raises(IOError, match=FAULT):
        await asyncio.wait_for(asyncio.to_thread(store.SystemStateSnapshotStore.save, {}), timeout=1.0)


def test_no_failure_without_the_flag(monkeypatch):
    monkeypatch.setattr(store, "FAULT_INJECT_STORAGE_FAILURE", False)
    store._check_fault_injection("probe")
