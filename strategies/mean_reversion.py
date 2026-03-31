"""
Strategy 2: Mean Reversion — вход при отклонении от среднего (состояние D — Rejection).

Улучшенная версия текущей D-state логики:
  - State D (rejection) на 15m
  - RSI экстремальный: < 30 для LONG reversal, > 70 для SHORT
  - Bollinger: цена у/за нижней (LONG) или верхней (SHORT) границей
  - НЕ берём reversal против сильного макро-тренда (ADX > 30 на 4h)
  - Target: 1.5R (реверсии короче трендов)
"""
import logging
from typing import Optional, Dict

from indicators import atr, bollinger_bands
from states import rejection
from strategies.base_strategy import BaseStrategy, StrategySignal

logger = logging.getLogger(__name__)

TARGET_RR = 1.5
# ADX порог на старшем TF: если тренд слишком силён, reversal опасен
MAX_MACRO_ADX = 30


class MeanReversionStrategy(BaseStrategy):

    def name(self) -> str:
        return "mean_reversion"

    def is_applicable(self, market_regime: str, volatility_level: str) -> bool:
        # Работаем в любом режиме, но не при очень низкой волатильности
        return volatility_level != "LOW"

    def evaluate(
        self,
        symbol: str,
        candles_map: Dict,
        directions: Dict,
        momentum_data: Dict,
        states: Dict,
    ) -> Optional[StrategySignal]:

        candles_15m = candles_map.get("15m", [])
        candles_5m = candles_map.get("5m", [])
        if len(candles_15m) < 25 or not candles_5m:
            return None

        # Условие 1: State D (rejection) на 15m
        from core.market_state import MarketState
        state_15m = states.get("15m")
        if state_15m != MarketState.D:
            return None

        # Условие 2: RSI экстремальный
        rsi_val = momentum_data.get("rsi_15m", 50)

        # Условие 3: Bollinger Bands позиция
        bb_data = momentum_data.get("bb_15m", {})
        bb_position = bb_data.get("position", "MIDDLE") if isinstance(bb_data, dict) else "MIDDLE"

        # Условие 4: Определяем направление reversal
        # Rejection D + RSI < 30 + BB lower → LONG reversal
        # Rejection D + RSI > 70 + BB upper → SHORT reversal
        entry = float(candles_5m[-1][4])
        atr_val = atr(candles_15m)
        if atr_val == 0:
            return None

        dir_4h = directions.get("4h", "FLAT")
        dir_1h = directions.get("1h", "FLAT")

        # Определяем направление по RSI
        if rsi_val < 30 and bb_position in ("BELOW_LOWER", "LOWER"):
            # LONG reversal — цена перепродана
            side = "LONG"
            # Блокируем если макро-тренд сильно DOWN
            adx_data_30m = momentum_data.get("adx_30m", {})
            adx_30m = adx_data_30m.get("adx", 0) if isinstance(adx_data_30m, dict) else 0
            if dir_4h == "DOWN" and adx_30m > MAX_MACRO_ADX:
                return None  # слишком сильный нисходящий тренд для реверсии

            stop = entry - atr_val * 1.0
            high_5m = float(candles_5m[-1][2])
            # Стоп под low последних 5 свечей
            recent_low = min(float(c[3]) for c in candles_15m[-5:])
            stop = min(stop, recent_low - atr_val * 0.1)

        elif rsi_val > 70 and bb_position in ("ABOVE_UPPER", "UPPER"):
            # SHORT reversal — цена перекуплена
            side = "SHORT"
            adx_data_30m = momentum_data.get("adx_30m", {})
            adx_30m = adx_data_30m.get("adx", 0) if isinstance(adx_data_30m, dict) else 0
            if dir_4h == "UP" and adx_30m > MAX_MACRO_ADX:
                return None

            stop = entry + atr_val * 1.0
            recent_high = max(float(c[2]) for c in candles_15m[-5:])
            stop = max(stop, recent_high + atr_val * 0.1)
        else:
            return None  # RSI/BB не подтверждают reversal

        risk_distance = abs(entry - stop)
        if risk_distance == 0:
            return None

        target = entry + risk_distance * TARGET_RR if side == "LONG" else entry - risk_distance * TARGET_RR
        rr = TARGET_RR

        # Stochastic подтверждение (бонус к confidence)
        stoch_data = momentum_data.get("stoch_15m", {})
        stoch_signal = stoch_data.get("signal", "NEUTRAL") if isinstance(stoch_data, dict) else "NEUTRAL"

        confidence = 0.55
        if (side == "LONG" and stoch_signal == "OVERSOLD") or \
           (side == "SHORT" and stoch_signal == "OVERBOUGHT"):
            confidence += 0.15
        if rsi_val < 25 or rsi_val > 75:
            confidence += 0.1  # Сильный экстремум
        confidence = min(confidence, 1.0)

        return StrategySignal(
            strategy_name=self.name(),
            side=side,
            confidence=confidence,
            entry=entry,
            stop=stop,
            target=target,
            reason=f"Mean reversion (D-state), RSI={rsi_val:.0f}, BB={bb_position}",
            rr_ratio=rr,
        )
