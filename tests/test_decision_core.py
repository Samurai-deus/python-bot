"""
Тесты для DecisionCore.
"""
import os
import pytest
from core.decision_core import (
    DecisionCore,
    TradingDecision,
    CognitiveState,
    RiskExposure,
    MarketRegime,
    Opportunity,
)
from tests.conftest import MockSystemState


class TestDecisionCoreDefaults:
    def test_no_system_state_returns_can_trade_true(self):
        dc = DecisionCore()
        result = dc.should_i_trade()
        assert result.can_trade is True

    def test_returns_trading_decision_type(self):
        dc = DecisionCore()
        result = dc.should_i_trade()
        assert isinstance(result, TradingDecision)


class TestDecisionCoreSafeMode:
    def test_safe_mode_blocks_trading(self, system_state):
        system_state.system_health.safe_mode = True
        dc = DecisionCore()
        result = dc.should_i_trade(system_state=system_state)
        assert result.can_trade is False
        assert result.risk_level == "HIGH"


class TestDecisionCoreCognitiveFilter:
    def test_should_pause_blocks_trading(self, system_state):
        system_state.cognitive_state = CognitiveState(
            overtrading_score=0.0,
            emotional_entries=0,
            fomo_patterns=0,
            recent_trades_count=0,
            should_pause=True,
        )
        dc = DecisionCore()
        result = dc.should_i_trade(system_state=system_state)
        assert result.can_trade is False

    def test_overtrading_score_high_blocks(self, system_state):
        system_state.cognitive_state = CognitiveState(
            overtrading_score=0.8,
            emotional_entries=0,
            fomo_patterns=0,
            recent_trades_count=0,
        )
        dc = DecisionCore()
        result = dc.should_i_trade(system_state=system_state)
        assert result.can_trade is False

    def test_overtrading_score_low_allows(self, system_state):
        system_state.cognitive_state = CognitiveState(
            overtrading_score=0.5,
            emotional_entries=0,
            fomo_patterns=0,
            recent_trades_count=0,
        )
        dc = DecisionCore()
        result = dc.should_i_trade(system_state=system_state)
        assert result.can_trade is True


class TestDecisionCoreRiskExposure:
    def test_overloaded_blocks_trading(self, system_state):
        system_state.risk_state = RiskExposure(
            total_risk_pct=5.0,
            max_correlation=0.0,
            total_leverage=1.0,
            active_positions=1,
            exposure_pct=50.0,
            is_overloaded=True,
        )
        dc = DecisionCore()
        result = dc.should_i_trade(system_state=system_state)
        assert result.can_trade is False

    def test_high_risk_pct_sets_risk_level_high(self, system_state):
        system_state.risk_state = RiskExposure(
            total_risk_pct=12.0,
            max_correlation=0.0,
            total_leverage=1.0,
            active_positions=1,
            exposure_pct=30.0,
            is_overloaded=False,
        )
        dc = DecisionCore()
        result = dc.should_i_trade(system_state=system_state)
        assert result.risk_level == "HIGH"


class TestDecisionCoreOpportunity:
    def test_low_readiness_score_blocks(self, system_state):
        symbol = "BTCUSDT"
        system_state.opportunities = {
            symbol: Opportunity(
                volatility_squeeze=False,
                accumulation=False,
                divergence=False,
                suspicious_silence=False,
                readiness_score=0.1,
            )
        }
        dc = DecisionCore()
        result = dc.should_i_trade(symbol=symbol, system_state=system_state)
        assert result.can_trade is False


class TestDecisionCoreFaultInjection:
    def test_fault_injection_returns_safe_decision(self, monkeypatch):
        monkeypatch.setenv("FAULT_INJECT_DECISION_EXCEPTION", "true")
        # Reload the module-level flag inside decision_core
        import core.decision_core as dc_module
        monkeypatch.setattr(dc_module, "FAULT_INJECT_DECISION_EXCEPTION", True)

        dc = DecisionCore()
        result = dc.should_i_trade()
        assert result.can_trade is False
        assert result.risk_level == "HIGH"


class TestDecisionCoreMarketRegime:
    def test_medium_risk_pct_sets_medium_level(self, system_state):
        system_state.risk_state = RiskExposure(
            total_risk_pct=7.0,
            max_correlation=0.0,
            total_leverage=1.0,
            active_positions=1,
            exposure_pct=30.0,
            is_overloaded=False,
        )
        dc = DecisionCore()
        result = dc.should_i_trade(system_state=system_state)
        assert result.risk_level == "MEDIUM"

    def test_market_regime_risk_off_reason(self, system_state):
        system_state.market_regime = MarketRegime(
            trend_type="RANGE",
            volatility_level="LOW",
            risk_sentiment="RISK_OFF",
        )
        dc = DecisionCore()
        result = dc.should_i_trade(system_state=system_state)
        assert "RISK-OFF" in result.reason

    def test_market_regime_high_volatility_reason(self, system_state):
        system_state.market_regime = MarketRegime(
            trend_type="RANGE",
            volatility_level="HIGH",
            risk_sentiment="NEUTRAL",
        )
        dc = DecisionCore()
        result = dc.should_i_trade(system_state=system_state)
        assert "волатильность" in result.reason

    def test_market_regime_range_adds_recommendation(self, system_state):
        system_state.market_regime = MarketRegime(
            trend_type="RANGE",
            volatility_level="LOW",
            risk_sentiment="NEUTRAL",
        )
        dc = DecisionCore()
        result = dc.should_i_trade(system_state=system_state)
        assert result.recommendations

    def test_market_regime_trend_adds_recommendation(self, system_state):
        system_state.market_regime = MarketRegime(
            trend_type="TREND",
            volatility_level="LOW",
            risk_sentiment="NEUTRAL",
        )
        dc = DecisionCore()
        result = dc.should_i_trade(system_state=system_state)
        assert result.recommendations

    def test_high_correlation_adds_recommendation(self, system_state):
        system_state.risk_state = RiskExposure(
            total_risk_pct=5.0,
            max_correlation=0.9,
            total_leverage=1.0,
            active_positions=1,
            exposure_pct=30.0,
            is_overloaded=False,
        )
        dc = DecisionCore()
        result = dc.should_i_trade(system_state=system_state)
        assert any("диверсифицируйте" in r for r in result.recommendations)

    def test_max_leverage_high_risk_level(self, system_state):
        system_state.risk_state = RiskExposure(
            total_risk_pct=12.0,
            max_correlation=0.0,
            total_leverage=1.0,
            active_positions=1,
            exposure_pct=30.0,
            is_overloaded=False,
        )
        dc = DecisionCore()
        result = dc.should_i_trade(system_state=system_state)
        assert result.max_leverage == 2.0

    def test_max_leverage_low_risk_level(self, system_state):
        system_state.risk_state = RiskExposure(
            total_risk_pct=1.0,
            max_correlation=0.0,
            total_leverage=1.0,
            active_positions=1,
            exposure_pct=5.0,
            is_overloaded=False,
        )
        dc = DecisionCore()
        result = dc.should_i_trade(system_state=system_state)
        assert result.max_leverage == 10.0


class TestDecisionCoreMisc:
    def test_reset_does_nothing(self):
        dc = DecisionCore()
        dc.reset()  # must not raise

    def test_trading_decision_default_recommendations(self):
        decision = TradingDecision(can_trade=True, reason="x", risk_level="LOW")
        assert decision.recommendations == []
