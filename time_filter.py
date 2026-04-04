"""
Trading time filter — session awareness for Bybit perpetuals.

Avoids trading around funding settlement times (00:00, 08:00, 16:00 UTC)
when liquidation cascades cause erratic price action.
Provides session-based confidence multiplier.
"""
from datetime import datetime, timezone


def is_good_time(symbol: str = None) -> bool:
    """Check if current time is good for trading.

    Avoids 30 min around Bybit funding settlements (00:00, 08:00, 16:00 UTC).
    These cause volatility spikes from liquidations.

    Args:
        symbol: Optional, reserved for future per-symbol logic.

    Returns:
        True if safe to trade, False around funding settlements.
    """
    now = datetime.now(timezone.utc)
    hour = now.hour
    minute = now.minute

    # 45 min before settlement hour
    if hour in (23, 7, 15) and minute >= 45:
        return False
    # 15 min after settlement hour
    if hour in (0, 8, 16) and minute <= 15:
        return False

    return True


# Backward-compatible alias (old code uses is_good_time without args)
is_good_trading_time = is_good_time


def get_session_multiplier() -> float:
    """Return a confidence multiplier based on trading session.

    London/NY overlap (13:00-16:00 UTC) = peak liquidity = 1.1
    Asian low-volume (01:00-07:00 UTC) = 0.9
    Default = 1.0
    """
    hour = datetime.now(timezone.utc).hour
    if 13 <= hour <= 16:
        return 1.1  # London/NY overlap — peak crypto liquidity
    elif 1 <= hour <= 7:
        return 0.9  # Asian session, lower volume
    return 1.0
