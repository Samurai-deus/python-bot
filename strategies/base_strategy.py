"""
Базовый класс стратегии и DTO для сигнала.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Dict


@dataclass
class StrategySignal:
    """Результат оценки стратегии — один торговый сигнал."""
    strategy_name: str
    side: str               # "LONG" | "SHORT"
    confidence: float       # 0.0–1.0
    entry: float
    stop: float
    target: float
    reason: str
    rr_ratio: float = 0.0
    metadata: Dict = field(default_factory=dict)


class BaseStrategy(ABC):
    """Абстрактная стратегия: evaluate() → Optional[StrategySignal]."""

    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def evaluate(
        self,
        symbol: str,
        candles_map: Dict,
        directions: Dict,
        momentum_data: Dict,
        states: Dict,
    ) -> Optional[StrategySignal]:
        ...

    @abstractmethod
    def is_applicable(self, market_regime: str, volatility_level: str) -> bool:
        """Должна ли стратегия вообще работать при данном режиме рынка."""
        ...
