"""
Strategy 3: Momentum Breakout — пробой с всплеском объёма.

Условия:
  - Volume ratio > 2.0x на 15m (сильный всплеск)
  - Цена пробивает 20-свечной high (LONG) или low (SHORT) на 15m
  - ADX > 25 — подтверждение направленного движения
  - MACD histogram расширяется в сторону пробоя
  - Stop: за противоположную сторону тела свечи пробоя
  - Target: 2.0R
"""
import logging
from typing import Optional, Dict

from indicators import atr, volume_analysis
from strategies.base_strategy import BaseStrategy, StrategySignal

logger = logging.getLogger(__name__)

TARGET_RR = 2.0
MIN_VOLUME_RATIO = 1.8  # Немного мягче чем 2.0, чтобы не отсеивать хорошие пробои
MIN_ADX = 25
LOOKBACK = 20  # Для определения high/low


class MomentumBreakoutStrategy(BaseStrategy):

    def name(self) -> str:
        return "momentum_breakout"

    def is_applicable(self, market_regime: str, volatility_level: str) -> bool:
        # Breakout работает лучше при средней/высокой волатильности
        return volatility_level in ("MEDIUM", "HIGH")

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
        if len(candles_15m) < LOOKBACK + 5 or not candles_5m:
            return None

        # Условие 1: Volume spike
        vol_data = momentum_data.get("volume_15m", {})
        vol_ratio = vol_data.get("volume_ratio", 1.0) if isinstance(vol_data, dict) else 1.0
        if vol_ratio < MIN_VOLUME_RATIO:
            return None

        # Условие 2: ADX > 25
        adx_data = momentum_data.get("adx_15m", {})
        adx_val = adx_data.get("adx", 0) if isinstance(adx_data, dict) else 0
        if adx_val < MIN_ADX:
            return None

        # Рассчитываем 20-свечной high/low (исключая текущую свечу)
        range_candles = candles_15m[-(LOOKBACK + 1):-1]
        range_high = max(float(c[2]) for c in range_candles)
        range_low = min(float(c[3]) for c in range_candles)

        current_close = float(candles_15m[-1][4])
        current_high = float(candles_15m[-1][2])
        current_low = float(candles_15m[-1][3])
        current_open = float(candles_15m[-1][1])

        entry = float(candles_5m[-1][4])
        atr_val = atr(candles_15m)
        if atr_val == 0:
            return None

        # Условие 3: MACD histogram
        macd_data = momentum_data.get("macd_15m", {})
        macd_trend = macd_data.get("trend", "NEUTRAL") if isinstance(macd_data, dict) else "NEUTRAL"
        macd_hist = macd_data.get("histogram", 0) if isinstance(macd_data, dict) else 0

        # Определяем пробой
        side = None
        if current_close > range_high:
            # LONG breakout
            if macd_trend != "BEARISH" and macd_hist > 0:
                side = "LONG"
        elif current_close < range_low:
            # SHORT breakout
            if macd_trend != "BULLISH" and macd_hist < 0:
                side = "SHORT"

        if side is None:
            return None

        # Macro trend alignment (бонус, не блокировка)
        dir_4h = directions.get("4h", "FLAT")
        dir_1h = directions.get("1h", "FLAT")
        macro_aligned = (side == "LONG" and dir_1h != "DOWN") or \
                        (side == "SHORT" and dir_1h != "UP")

        # H-6: Stop behind the range level being broken (not candle body)
        if side == "LONG":
            stop = range_low - atr_val * 0.2  # below the range
            stop = max(stop, entry - atr_val * 2.5)  # cap risk
        else:
            stop = range_high + atr_val * 0.2  # above the range
            stop = min(stop, entry + atr_val * 2.5)  # cap risk

        risk_distance = abs(entry - stop)
        if risk_distance == 0:
            return None

        target = entry + risk_distance * TARGET_RR if side == "LONG" else entry - risk_distance * TARGET_RR

        # Confidence
        confidence = 0.5
        if vol_ratio > 2.5:
            confidence += 0.1
        if adx_val > 35:
            confidence += 0.1
        if macro_aligned:
            confidence += 0.15
        if dir_4h == ("UP" if side == "LONG" else "DOWN"):
            confidence += 0.1
        confidence = min(confidence, 1.0)

        return StrategySignal(
            strategy_name=self.name(),
            side=side,
            confidence=confidence,
            entry=entry,
            stop=stop,
            target=target,
            reason=f"Momentum breakout, vol={vol_ratio:.1f}x, ADX={adx_val:.0f}",
            rr_ratio=TARGET_RR,
        )
