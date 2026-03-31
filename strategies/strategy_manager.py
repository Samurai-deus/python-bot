"""
StrategyManager: оркестрирует стратегии, выбирает лучший сигнал.
"""
import logging
from typing import Optional, Dict

from strategies.base_strategy import BaseStrategy, StrategySignal
from strategies.trend_following import TrendFollowingStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum_breakout import MomentumBreakoutStrategy

logger = logging.getLogger(__name__)

# Минимальный confidence для того чтобы сигнал считался валидным
MIN_CONFIDENCE = 0.5


class StrategyManager:
    def __init__(self):
        self.strategies = [
            TrendFollowingStrategy(),
            MeanReversionStrategy(),
            MomentumBreakoutStrategy(),
        ]

    def get_best_signal(
        self,
        symbol: str,
        candles_map: Dict,
        directions: Dict,
        momentum_data: Dict,
        states: Dict,
        market_regime: str = "RANGE",
        volatility_level: str = "MEDIUM",
    ) -> Optional[StrategySignal]:
        """
        Оценивает все стратегии и возвращает сигнал с наивысшим confidence.
        Возвращает None если ни одна стратегия не даёт сигнал.
        """
        signals = []

        for strategy in self.strategies:
            if not strategy.is_applicable(market_regime, volatility_level):
                continue
            try:
                signal = strategy.evaluate(
                    symbol, candles_map, directions, momentum_data, states
                )
                if signal and signal.confidence >= MIN_CONFIDENCE:
                    signals.append(signal)
                    logger.debug(
                        "%s: strategy %s → %s conf=%.2f R:R=%.1f",
                        symbol, signal.strategy_name, signal.side,
                        signal.confidence, signal.rr_ratio,
                    )
            except Exception as e:
                logger.warning(
                    "%s: strategy %s error: %s", symbol, strategy.name(), e
                )

        if not signals:
            return None

        # Выбираем сигнал с максимальным confidence
        best = max(signals, key=lambda s: s.confidence)
        logger.info(
            "%s: best signal from %s — %s conf=%.2f R:R=%.1f (%s)",
            symbol, best.strategy_name, best.side,
            best.confidence, best.rr_ratio, best.reason,
        )
        return best
