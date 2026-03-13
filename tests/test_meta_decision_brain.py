"""
Тесты для MetaDecisionBrain.
"""
import pytest
from brains.meta_decision_brain import (
    MetaDecisionBrain,
    MetaDecisionResult,
    BlockLevel,
    SystemHealthStatus,
    TimeContext,
)


class TestMetaDecisionBrainDefaults:
    def test_default_inputs_allow_trading(self):
        brain = MetaDecisionBrain()
        result = brain.evaluate()
        assert result.allow_trading is True

    def test_returns_meta_decision_result_type(self):
        brain = MetaDecisionBrain()
        result = brain.evaluate()
        assert isinstance(result, MetaDecisionResult)

    def test_allow_result_has_no_block_level(self):
        brain = MetaDecisionBrain()
        result = brain.evaluate()
        assert result.block_level is None


class TestMetaDecisionBrainHardBlock:
    def test_system_degraded_hard_blocks(self):
        brain = MetaDecisionBrain()
        result = brain.evaluate(system_health=SystemHealthStatus.DEGRADED)
        assert result.allow_trading is False
        assert result.block_level == BlockLevel.HARD

    def test_very_high_entropy_with_low_confidence_hard_blocks(self):
        brain = MetaDecisionBrain()
        # entropy > 0.7 AND confidence < 0.4 → HARD_BLOCK
        result = brain.evaluate(entropy_score=0.95, confidence_score=0.2)
        assert result.allow_trading is False
        assert result.block_level == BlockLevel.HARD

    def test_high_portfolio_exposure_hard_blocks(self):
        brain = MetaDecisionBrain()
        # portfolio_exposure > 0.8 → HARD_BLOCK
        result = brain.evaluate(portfolio_exposure=0.9)
        assert result.allow_trading is False
        assert result.block_level == BlockLevel.HARD


class TestMetaDecisionBrainSoftBlock:
    def test_high_signals_count_soft_blocks(self):
        brain = MetaDecisionBrain()
        result = brain.evaluate(signals_count_recent=20)
        assert result.allow_trading is False
        assert result.block_level == BlockLevel.SOFT

    def test_bad_recent_outcomes_soft_blocks(self):
        brain = MetaDecisionBrain()
        # More than 60% negative outcomes (4 out of 5)
        result = brain.evaluate(recent_outcomes=[-1.0, -2.0, -0.5, -1.5, 0.5])
        assert result.allow_trading is False
        assert result.block_level == BlockLevel.SOFT


class TestMetaDecisionBrainPriority:
    def test_hard_block_takes_priority_over_soft(self):
        brain = MetaDecisionBrain()
        # Both HARD and SOFT conditions present: HARD must win
        result = brain.evaluate(
            system_health=SystemHealthStatus.DEGRADED,  # HARD
            signals_count_recent=20,                    # SOFT
        )
        assert result.allow_trading is False
        assert result.block_level == BlockLevel.HARD


class TestMetaDecisionBrainCooldown:
    def test_hard_block_has_cooldown(self):
        brain = MetaDecisionBrain()
        result = brain.evaluate(system_health=SystemHealthStatus.DEGRADED)
        assert result.cooldown_minutes >= MetaDecisionBrain.HARD_BLOCK_COOLDOWN_MINUTES

    def test_allow_has_zero_cooldown(self):
        brain = MetaDecisionBrain()
        result = brain.evaluate()
        assert result.cooldown_minutes == 0


class TestMetaDecisionBrainSoftBlockConditions:
    def test_medium_uncertainty_with_high_exposure_soft_blocks(self):
        brain = MetaDecisionBrain()
        # confidence=0.5 in [0.4,0.6], entropy=0.5 in [0.4,0.6], exposure=0.6 > 0.5 → SOFT_BLOCK
        result = brain.evaluate(confidence_score=0.5, entropy_score=0.5, portfolio_exposure=0.6)
        assert result.allow_trading is False
        assert result.block_level == BlockLevel.SOFT

    def test_high_exposure_low_confidence_soft_blocks(self):
        brain = MetaDecisionBrain()
        # entropy=0.2 keeps condition 2 inactive; exposure=0.7 > 0.6, confidence=0.4 < 0.5 → cond 4
        result = brain.evaluate(portfolio_exposure=0.7, confidence_score=0.4, entropy_score=0.2)
        assert result.allow_trading is False
        assert result.block_level == BlockLevel.SOFT

    def test_session_end_high_entropy_soft_blocks(self):
        brain = MetaDecisionBrain()
        # SESSION_END + entropy=0.7 > 0.6 → SOFT_BLOCK (cond 5)
        result = brain.evaluate(time_context=TimeContext.SESSION_END, entropy_score=0.7)
        assert result.allow_trading is False
        assert result.block_level == BlockLevel.SOFT


class TestMetaDecisionBrainExplain:
    def test_explain_decision_allow(self):
        brain = MetaDecisionBrain()
        result = brain.evaluate()
        explanation = brain.explain_decision(result)
        assert "ALLOWED" in explanation

    def test_explain_decision_hard_block(self):
        brain = MetaDecisionBrain()
        result = brain.evaluate(system_health=SystemHealthStatus.DEGRADED)
        explanation = brain.explain_decision(result)
        assert "HARD" in explanation
