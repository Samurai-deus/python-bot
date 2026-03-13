"""
Тесты для PositionSizer.
"""
import pytest
from core.position_sizer import PositionSizer, PositionSizingResult, PositionSizingConfig
from tests.conftest import MockPortfolioState


class TestPositionSizerBasic:
    def test_high_confidence_full_portfolio_available(self, empty_portfolio):
        sizer = PositionSizer()
        result = sizer.calculate(
            confidence=0.9, entropy=0.1,
            portfolio_state=empty_portfolio, symbol="BTCUSDT"
        )
        assert result.position_allowed is True

    def test_returns_position_sizing_result_type(self, empty_portfolio):
        sizer = PositionSizer()
        result = sizer.calculate(
            confidence=0.8, entropy=0.2,
            portfolio_state=empty_portfolio, symbol="BTCUSDT"
        )
        assert isinstance(result, PositionSizingResult)

    def test_position_size_usd_calculated_when_balance_provided(self, empty_portfolio):
        sizer = PositionSizer()
        result = sizer.calculate(
            confidence=0.9, entropy=0.1,
            portfolio_state=empty_portfolio, symbol="BTCUSDT",
            balance=10000.0
        )
        assert result.position_size_usd is not None
        assert result.position_size_usd > 0


class TestPositionSizerConfidenceFactor:
    def test_low_confidence_reduces_size(self, empty_portfolio):
        sizer = PositionSizer()
        low = sizer.calculate(confidence=0.2, entropy=0.1, portfolio_state=empty_portfolio, symbol="X")
        high = sizer.calculate(confidence=0.9, entropy=0.1, portfolio_state=empty_portfolio, symbol="X")
        assert low.final_risk < high.final_risk

    def test_confidence_below_min_clamps(self, empty_portfolio):
        config = PositionSizingConfig()
        sizer = PositionSizer(config=config)
        result = sizer.calculate(
            confidence=0.0, entropy=0.1,
            portfolio_state=empty_portfolio, symbol="X"
        )
        assert result.confidence_factor >= config.confidence_min


class TestPositionSizerEntropyFactor:
    def test_high_entropy_reduces_size(self, empty_portfolio):
        sizer = PositionSizer()
        low_e = sizer.calculate(confidence=0.8, entropy=0.1, portfolio_state=empty_portfolio, symbol="X")
        high_e = sizer.calculate(confidence=0.8, entropy=0.9, portfolio_state=empty_portfolio, symbol="X")
        assert high_e.final_risk < low_e.final_risk

    def test_high_entropy_with_low_portfolio_may_disallow(self):
        # With full portfolio (ratio=0), position should not be allowed
        portfolio = MockPortfolioState(available_ratio=0.0)
        sizer = PositionSizer()
        result = sizer.calculate(confidence=0.5, entropy=0.9, portfolio_state=portfolio, symbol="X")
        assert result.position_allowed is False


class TestPositionSizerPortfolioFactor:
    def test_full_portfolio_disallows_position(self, full_portfolio):
        sizer = PositionSizer()
        result = sizer.calculate(
            confidence=0.9, entropy=0.1,
            portfolio_state=full_portfolio, symbol="X"
        )
        assert result.position_allowed is False

    def test_empty_portfolio_gives_full_factor(self, empty_portfolio):
        sizer = PositionSizer()
        result = sizer.calculate(
            confidence=0.9, entropy=0.1,
            portfolio_state=empty_portfolio, symbol="X"
        )
        assert result.portfolio_factor == 1.0


class TestPositionSizingResultInvariants:
    def test_result_dataclass_invariants_valid(self):
        result = PositionSizingResult(
            position_allowed=True,
            final_risk=1.0,
            base_risk=2.0,
            confidence_factor=0.8,
            entropy_factor=0.9,
            portfolio_factor=1.0,
            reason="test",
        )
        assert result.position_allowed is True


class TestPositionSizerInvariants:
    def test_factors_always_in_0_1_range(self, empty_portfolio):
        sizer = PositionSizer()
        for confidence in [0.0, 0.3, 0.7, 1.0]:
            for entropy in [0.0, 0.5, 1.0]:
                result = sizer.calculate(
                    confidence=confidence, entropy=entropy,
                    portfolio_state=empty_portfolio, symbol="X"
                )
                assert 0.0 <= result.confidence_factor <= 1.0
                assert 0.0 <= result.entropy_factor <= 1.0
                assert 0.0 <= result.portfolio_factor <= 1.0

    def test_final_risk_never_negative(self):
        sizer = PositionSizer()
        for ratio in [0.0, 0.5, 1.0]:
            portfolio = MockPortfolioState(available_ratio=ratio)
            for confidence in [0.0, 0.5, 1.0]:
                result = sizer.calculate(
                    confidence=confidence, entropy=0.5,
                    portfolio_state=portfolio, symbol="X"
                )
                assert result.final_risk >= 0
