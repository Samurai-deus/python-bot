"""
Анализ микроструктуры рынка: Open Interest + Funding Rate.

Сигналы:
  - Rising OI + rising price  → new longs (bullish continuation)
  - Rising OI + falling price → new shorts (bearish continuation)
  - Falling OI               → positions closing, тренд может выдохнуться
  - Extreme funding           → crowded trade, реверсия вероятнее
"""
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Порог экстремального funding rate (1% за 8 часов — очень высоко)
FUNDING_EXTREME_THRESHOLD = 0.01
# Порог значимого изменения OI (%)
OI_CHANGE_THRESHOLD = 3.0
# Порог значимого изменения цены (%)
PRICE_CHANGE_THRESHOLD = 0.5


def analyze_microstructure(
    symbol: str,
    candles_15m: List,
    oi_data: List[Dict],
    funding_data: List[Dict],
) -> Dict:
    """
    Анализирует микроструктуру и возвращает:
      - oi_trend: "RISING" | "FALLING" | "FLAT"
      - oi_price_divergence: bool
      - funding_extreme: "LONG_CROWDED" | "SHORT_CROWDED" | None
      - signal_bias: "BULLISH" | "BEARISH" | "NEUTRAL"
      - confidence_adjustment: float (-0.15 .. +0.1)
      - block_long: bool
      - block_short: bool
    """
    result = {
        "oi_trend": "FLAT",
        "oi_price_divergence": False,
        "funding_extreme": None,
        "signal_bias": "NEUTRAL",
        "confidence_adjustment": 0.0,
        "block_long": False,
        "block_short": False,
    }

    # ── Open Interest анализ ──
    if len(oi_data) >= 10 and len(candles_15m) >= 10:
        try:
            oi_values = [float(d.get("openInterest", 0)) for d in oi_data[:10]]
            oi_now = oi_values[0]   # Bybit возвращает newest first
            oi_prev = oi_values[-1]

            if oi_prev > 0:
                oi_change_pct = (oi_now - oi_prev) / oi_prev * 100
            else:
                oi_change_pct = 0

            price_now = float(candles_15m[-1][4])
            price_prev = float(candles_15m[-10][4])
            if price_prev > 0:
                price_change_pct = (price_now - price_prev) / price_prev * 100
            else:
                price_change_pct = 0

            # Определяем OI тренд
            if oi_change_pct > OI_CHANGE_THRESHOLD:
                result["oi_trend"] = "RISING"
            elif oi_change_pct < -OI_CHANGE_THRESHOLD:
                result["oi_trend"] = "FALLING"

            # Rising OI + rising price = new longs (bullish)
            if oi_change_pct > OI_CHANGE_THRESHOLD and price_change_pct > PRICE_CHANGE_THRESHOLD:
                result["signal_bias"] = "BULLISH"
                result["confidence_adjustment"] += 0.05

            # Rising OI + falling price = new shorts (bearish)
            elif oi_change_pct > OI_CHANGE_THRESHOLD and price_change_pct < -PRICE_CHANGE_THRESHOLD:
                result["signal_bias"] = "BEARISH"
                result["confidence_adjustment"] += 0.05

            # Falling OI = positions closing, trend exhaustion
            elif oi_change_pct < -5:
                result["confidence_adjustment"] -= 0.05

            # OI divergence: OI rising but price flat/falling = potential squeeze
            if oi_change_pct > 5 and abs(price_change_pct) < 0.3:
                result["oi_price_divergence"] = True
                result["confidence_adjustment"] -= 0.03  # неопределённость

        except (ValueError, IndexError, TypeError) as e:
            logger.debug("OI analysis error for %s: %s", symbol, e)

    # ── Funding Rate анализ ──
    if funding_data:
        try:
            current_funding = float(funding_data[0].get("fundingRate", 0))

            if current_funding > FUNDING_EXTREME_THRESHOLD:
                result["funding_extreme"] = "LONG_CROWDED"
                result["block_long"] = True   # Не LONG когда все лонгуют
                result["confidence_adjustment"] -= 0.1
                logger.debug(
                    "%s: extreme positive funding %.4f%% — LONG crowded",
                    symbol, current_funding * 100,
                )

            elif current_funding < -FUNDING_EXTREME_THRESHOLD:
                result["funding_extreme"] = "SHORT_CROWDED"
                result["block_short"] = True  # Не SHORT когда все шортят
                result["confidence_adjustment"] -= 0.1
                logger.debug(
                    "%s: extreme negative funding %.4f%% — SHORT crowded",
                    symbol, current_funding * 100,
                )

        except (ValueError, IndexError, TypeError) as e:
            logger.debug("Funding analysis error for %s: %s", symbol, e)

    return result
