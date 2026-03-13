"""
Тесты для AdaptiveRiskManager — Фаза 3.

Параметры (ADR-005):
    FLOOR_RISK_PCT = 1.5%
    STEP_DOWN = 0.25%
    STEP_UP = 0.25%
    RECOVERY_HOURS = 24
"""
import pytest
from datetime import datetime, timedelta, UTC
from unittest.mock import MagicMock

from analytics.adaptive_risk_manager import (
    AdaptiveRiskManager,
    AdaptiveRiskDecision,
    RiskAction,
    FLOOR_RISK_PCT,
    STEP_DOWN,
    STEP_UP,
    RECOVERY_HOURS,
)


def make_drift_state(detected: bool, severity: str = "LOW", reason: str = "test"):
    """Хелпер: создаёт mock DriftState."""
    state = MagicMock()
    state.overall_drift_detected = detected
    state.overall_severity = MagicMock()
    state.overall_severity.value = severity
    state.overall_reason = reason
    return state


# ---------------------------------------------------------------------------
# Базовые свойства решения
# ---------------------------------------------------------------------------

class TestAdaptiveRiskDecision:
    def test_changed_when_risk_differs(self):
        d = AdaptiveRiskDecision(
            action=RiskAction.DECREASE,
            new_risk_pct=1.75,
            previous_risk_pct=2.0,
            position_size_multiplier=0.875,
            block_trading=False,
            reason="test",
            drift_severity="LOW",
        )
        assert d.changed is True

    def test_not_changed_when_same(self):
        d = AdaptiveRiskDecision(
            action=RiskAction.NO_CHANGE,
            new_risk_pct=2.0,
            previous_risk_pct=2.0,
            position_size_multiplier=1.0,
            block_trading=False,
            reason="test",
            drift_severity="NONE",
        )
        assert d.changed is False

    def test_no_telegram_for_no_change(self):
        d = AdaptiveRiskDecision(
            action=RiskAction.NO_CHANGE,
            new_risk_pct=2.0,
            previous_risk_pct=2.0,
            position_size_multiplier=1.0,
            block_trading=False,
            reason="stable",
            drift_severity="NONE",
        )
        assert d.telegram_message() is None


# ---------------------------------------------------------------------------
# Логика evaluate()
# ---------------------------------------------------------------------------

class TestAdaptiveRiskManager:
    def setup_method(self):
        self.manager = AdaptiveRiskManager(base_risk_pct=2.0)

    def test_no_change_when_drift_state_is_none(self):
        decision = self.manager.evaluate(None, current_risk_pct=2.0)
        assert decision.action == RiskAction.NO_CHANGE
        assert decision.new_risk_pct == 2.0
        assert decision.block_trading is False

    def test_decrease_on_low_drift(self):
        drift = make_drift_state(detected=True, severity="LOW")
        decision = self.manager.evaluate(drift, current_risk_pct=2.0)
        assert decision.action == RiskAction.DECREASE
        # LOW drift снижает на STEP_DOWN*0.5
        expected = max(FLOOR_RISK_PCT, 2.0 - STEP_DOWN * 0.5)
        assert decision.new_risk_pct == pytest.approx(expected, abs=0.001)
        assert decision.block_trading is False

    def test_decrease_on_medium_drift(self):
        drift = make_drift_state(detected=True, severity="MEDIUM")
        decision = self.manager.evaluate(drift, current_risk_pct=2.0)
        assert decision.action == RiskAction.DECREASE
        expected = max(FLOOR_RISK_PCT, 2.0 - STEP_DOWN)
        assert decision.new_risk_pct == pytest.approx(expected, abs=0.001)

    def test_decrease_on_high_drift(self):
        drift = make_drift_state(detected=True, severity="HIGH")
        decision = self.manager.evaluate(drift, current_risk_pct=2.0)
        assert decision.action == RiskAction.DECREASE
        # ещё не на floor → просто снижаем
        assert not decision.block_trading

    def test_block_on_high_drift_at_floor(self):
        """HIGH drift + уже на floor → блокировка."""
        drift = make_drift_state(detected=True, severity="HIGH")
        decision = self.manager.evaluate(drift, current_risk_pct=FLOOR_RISK_PCT)
        assert decision.action == RiskAction.BLOCK_TRADING
        assert decision.block_trading is True
        assert decision.new_risk_pct == FLOOR_RISK_PCT

    def test_floor_is_never_breached_on_medium_drift(self):
        """Многократное снижение не опускает риск ниже floor."""
        drift = make_drift_state(detected=True, severity="MEDIUM")
        current = 2.0
        for _ in range(20):
            decision = self.manager.evaluate(drift, current_risk_pct=current)
            current = decision.new_risk_pct
            if decision.block_trading:
                break
        assert current >= FLOOR_RISK_PCT

    def test_no_recovery_before_24h(self):
        """Риск не восстанавливается, пока не прошло 24ч."""
        # Сначала регистрируем drift
        drift = make_drift_state(detected=True, severity="LOW")
        self.manager.evaluate(drift, current_risk_pct=2.0)

        # Теперь drift исчез, но 24ч ещё не прошло
        no_drift = make_drift_state(detected=False)
        decision = self.manager.evaluate(no_drift, current_risk_pct=1.75)
        assert decision.action == RiskAction.NO_CHANGE

    def test_recovery_after_24h(self):
        """После 24ч без drift риск восстанавливается."""
        # Регистрируем drift 25ч назад вручную
        self.manager._last_drift_time = datetime.now(UTC) - timedelta(hours=25)

        no_drift = make_drift_state(detected=False)
        decision = self.manager.evaluate(no_drift, current_risk_pct=1.75)
        assert decision.action == RiskAction.INCREASE
        expected = min(2.0, 1.75 + STEP_UP)
        assert decision.new_risk_pct == pytest.approx(expected, abs=0.001)

    def test_no_recovery_when_already_at_base(self):
        """Не восстанавливаем риск, если он уже на базовом уровне."""
        self.manager._last_drift_time = datetime.now(UTC) - timedelta(hours=48)
        no_drift = make_drift_state(detected=False)
        decision = self.manager.evaluate(no_drift, current_risk_pct=2.0)
        assert decision.action == RiskAction.NO_CHANGE

    def test_position_size_multiplier_is_ratio(self):
        """size_multiplier = new_risk / base_risk."""
        drift = make_drift_state(detected=True, severity="MEDIUM")
        decision = self.manager.evaluate(drift, current_risk_pct=2.0)
        expected_mult = decision.new_risk_pct / self.manager.base_risk_pct
        assert decision.position_size_multiplier == pytest.approx(expected_mult, abs=0.001)

    def test_reset_clears_last_drift_time(self):
        """reset() очищает время последнего drift."""
        self.manager._last_drift_time = datetime.now(UTC)
        self.manager.reset()
        assert self.manager._last_drift_time is None

    def test_telegram_message_not_none_on_decrease(self):
        drift = make_drift_state(detected=True, severity="MEDIUM")
        decision = self.manager.evaluate(drift, current_risk_pct=2.0)
        msg = decision.telegram_message()
        assert msg is not None
        assert "Drift" in msg

    def test_telegram_message_critical_on_block(self):
        drift = make_drift_state(detected=True, severity="HIGH")
        decision = self.manager.evaluate(drift, current_risk_pct=FLOOR_RISK_PCT)
        msg = decision.telegram_message()
        assert msg is not None
        assert "ЗАБЛОКИРОВАНА" in msg or "BLOCK" in msg
