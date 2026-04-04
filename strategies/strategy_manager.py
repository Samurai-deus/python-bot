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

        # H-8: Conflict detection — if strategies disagree on direction, skip
        sides = set(s.side for s in signals)
        if len(sides) > 1:
            logger.info(
                "%s: conflicting signals (%s), skipping",
                symbol, ", ".join(f"{s.strategy_name}={s.side}" for s in signals),
            )
            return None

        # H-7: Weight confidence by regime fitness
        REGIME_FITNESS = {
            ("trend_following", "TREND"): 1.3,
            ("mean_reversion", "RANGE"): 1.3,
            ("momentum_breakout", "TREND"): 1.2,
        }

        def weighted_confidence(s: StrategySignal) -> float:
            multiplier = REGIME_FITNESS.get((s.strategy_name, market_regime), 1.0)
            return s.confidence * multiplier

        best = max(signals, key=weighted_confidence)
        logger.info(
            "%s: best signal from %s — %s conf=%.2f (w=%.2f) R:R=%.1f (%s)",
            symbol, best.strategy_name, best.side,
            best.confidence, weighted_confidence(best),
            best.rr_ratio, best.reason,
        )
        return best
