"""
Общие fixtures для тест-сьюта market_bot.
"""
import pytest
from dataclasses import dataclass
from typing import Optional, Dict
from core.decision_core import MarketRegime, RiskExposure, CognitiveState, Opportunity


# ========== MockSystemState для DecisionCore ==========

@dataclass
class _SystemHealth:
    safe_mode: bool = False
    consecutive_errors: int = 0


class MockSystemState:
    def __init__(self):
        self.system_health = _SystemHealth()
        self.cognitive_state: Optional[CognitiveState] = None
        self.market_regime: Optional[MarketRegime] = None
        self.risk_state: Optional[RiskExposure] = None
        self.opportunities: Dict = {}
        self._can_trade_val = True

    def update_trading_decision(self, val: bool):
        self._can_trade_val = val


@pytest.fixture
def system_state():
    return MockSystemState()


# ========== MockPortfolioState для PositionSizer ==========

class MockPortfolioState:
    def __init__(self, exposure=0.0, available_ratio=1.0):
        self._exposure = exposure
        self._ratio = available_ratio

    def total_exposure(self) -> float:
        return self._exposure

    def available_risk_ratio(self) -> float:
        return self._ratio


@pytest.fixture
def empty_portfolio():
    return MockPortfolioState(exposure=0.0, available_ratio=1.0)


@pytest.fixture
def full_portfolio():
    return MockPortfolioState(exposure=1.0, available_ratio=0.0)
