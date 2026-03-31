"""
Strategy 1: Trend Following — вход на пуллбэках в подтверждённом тренде.

Условия:
  - 4h и 1h направление совпадают (оба UP или оба DOWN)
  - Цена на 15m откатывается к EMA(20)
  - RSI не в экстремуме (LONG: RSI > 40, SHORT: RSI < 60)
  - ADX > 20 на 15m — тренд активен
  - Entry: close последней 15m свечи
  - Stop: за swing low/high, минимум 1.0 ATR
  - Target: 2.5R
"""
import logging
from typing import Optional, Dict

from indicators import atr, rsi, adx, ema_crossover
from strategies.base_strategy import BaseStrategy, StrategySignal

logger = logging.getLogger(__name__)

# EMA для определения пуллбэка
EMA_PERIOD = 20
# Порог ADX
MIN_ADX = 20
# R:R для трендовых сделок (тренд = бОльший ход)
TARGET_RR = 2.5
# Максимальное расстояние цены от EMA для пуллбэка (в ATR)
MAX_PULLBACK_DIST_ATR = 0.5


def _calc_ema(closes: list, period: int = EMA_PERIOD) -> list:
    """Вычисляет EMA по списку closes."""
    if len(closes) < period:
        return []
    multiplier = 2.0 / (period + 1)
    ema = [sum(closes[:period]) / period]  # seed = SMA
    for price in closes[period:]:
        ema.append(price * multiplier + ema[-1] * (1 - multiplier))
    return ema


def _swing_low(candles, lookback: int = 10) -> float:
    """Минимальный low за lookback свечей."""
    return min(float(c[3]) for c in candles[-lookback:])


def _swing_high(candles, lookback: int = 10) -> float:
    """Максимальный high за lookback свечей."""
    return max(float(c[2]) for c in candles[-lookback:])


class TrendFollowingStrategy(BaseStrategy):

    def name(self) -> str:
        return "trend_following"

    def is_applicable(self, market_regime: str, volatility_level: str) -> bool:
        # Работаем в TREND и даже в RANGE с умеренной волатильностью
        return market_regime in ("TREND", "RANGE") and volatility_level != "LOW"

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
        if len(candles_15m) < EMA_PERIOD + 10 or not candles_5m:
            return None

        dir_4h = directions.get("4h", "FLAT")
        dir_1h = directions.get("1h", "FLAT")

        # Условие 1: 4h и 1h совпадают
        if dir_4h == "FLAT" or dir_1h == "FLAT":
            return None
        if dir_4h != dir_1h:
            return None

        trend_dir = dir_4h  # UP или DOWN

        # Условие 2: цена откатывается к EMA(20) на 15m
        closes_15m = [float(c[4]) for c in candles_15m]
        ema_values = _calc_ema(closes_15m, EMA_PERIOD)
        if not ema_values:
            return None

        current_price = closes_15m[-1]
        ema_now = ema_values[-1]
        atr_val = atr(candles_15m)
        if atr_val == 0:
            return None

        dist_to_ema = abs(current_price - ema_now)

        # Пуллбэк = цена близко к EMA (в пределах 0.5 ATR)
        if dist_to_ema > MAX_PULLBACK_DIST_ATR * atr_val:
            return None

        # Условие 3: RSI не в экстремуме
        rsi_val = momentum_data.get("rsi_15m", 50)

        # Условие 4: ADX > 20
        adx_data = momentum_data.get("adx_15m", {})
        adx_val = adx_data.get("adx", 0) if isinstance(adx_data, dict) else 0

        if adx_val < MIN_ADX:
            return None

        # Entry
        entry = float(candles_5m[-1][4])

        if trend_dir == "UP":
            if rsi_val < 40:
                return None  # пуллбэк слишком глубокий
            if current_price < ema_now:
                return None  # цена должна быть у/над EMA для LONG pullback
            side = "LONG"
            stop = max(_swing_low(candles_15m, 10), entry - atr_val * 1.0)
            # Защита: стоп не дальше 2 ATR
            if entry - stop > atr_val * 2.0:
                stop = entry - atr_val * 2.0
        else:  # DOWN
            if rsi_val > 60:
                return None
            if current_price > ema_now:
                return None
            side = "SHORT"
            stop = min(_swing_high(candles_15m, 10), entry + atr_val * 1.0)
            if stop - entry > atr_val * 2.0:
                stop = entry + atr_val * 2.0

        risk_distance = abs(entry - stop)
        if risk_distance == 0:
            return None

        target = entry + risk_distance * TARGET_RR if side == "LONG" else entry - risk_distance * TARGET_RR
        rr = TARGET_RR

        # Confidence: ADX strength + trend alignment
        confidence = 0.5
        if adx_val > 30:
            confidence += 0.15
        if adx_val > 40:
            confidence += 0.1
        # EMA slope подтверждает
        if len(ema_values) >= 6:
            slope = (ema_values[-1] - ema_values[-6]) / ema_values[-6] * 100
            if (trend_dir == "UP" and slope > 0.2) or (trend_dir == "DOWN" and slope < -0.2):
                confidence += 0.15
        confidence = min(confidence, 1.0)

        return StrategySignal(
            strategy_name=self.name(),
            side=side,
            confidence=confidence,
            entry=entry,
            stop=stop,
            target=target,
            reason=f"Trend pullback to EMA20 ({trend_dir}), ADX={adx_val:.0f}",
            rr_ratio=rr,
        )
