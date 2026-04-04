def impulse(candles, atr_val):
    bodies = []
    for c in candles[-4:]:
        bodies.append(abs(float(c[4]) - float(c[1])))
    return sum(b > 1.3 * atr_val for b in bodies) >= 2


def acceptance(candles, atr_val):
    ranges = []
    for c in candles[-7:]:
        ranges.append(float(c[2]) - float(c[3]))
    avg_range = sum(ranges) / len(ranges) if ranges else atr_val
    return avg_range < 0.55 * atr_val


def loss_of_control(candles):
    wicks = []
    bodies = []

    for c in candles[-5:]:
        open_ = float(c[1])
        close = float(c[4])
        high = float(c[2])
        low = float(c[3])

        bodies.append(abs(close - open_))
        upper_wick = high - max(open_, close)
        lower_wick = min(open_, close) - low
        wicks.append(max(upper_wick, lower_wick))

    return (
        max(wicks) > 1.5 * (sum(wicks) / len(wicks))
        and max(wicks) > max(bodies)
    )


def rejection(candles, atr_val):
    """Pin bar: small body, large wick rejected from a level."""
    last = candles[-1]
    o, h, l, c = float(last[1]), float(last[2]), float(last[3]), float(last[4])
    body = abs(c - o)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    total_range = h - l
    if total_range < 0.5 * atr_val:
        return False  # too small candle
    dominant_wick = max(upper_wick, lower_wick)
    return dominant_wick >= 2 * body and dominant_wick >= 0.55 * total_range

def entry_trigger_5m(candles):
    last = candles[-1]
    open_ = float(last[1])
    close = float(last[4])
    high = float(last[2])
    low = float(last[3])

    body_mid = (open_ + close) / 2

    upper_wick = high - max(open_, close)
    lower_wick = min(open_, close) - low

    # универсальный фильтр: фитиль больше тела
    if upper_wick > abs(close - open_) and close < body_mid:
        return "SHORT_TRIGGER"

    if lower_wick > abs(close - open_) and close > body_mid:
        return "LONG_TRIGGER"

    return None

def market_direction(candles, period=20, slope_threshold=0.1):
    """
    Определяет направление рынка через EMA(period) slope + позицию цены.
    Требует совпадения наклона EMA и положения цены относительно EMA,
    что устойчивее к шуму чем сравнение средних 5 свечей.
    """
    min_candles = period + 5
    if len(candles) < min_candles:
        return "FLAT"

    closes = [float(c[4]) for c in candles[-(min_candles):]]

    # EMA
    multiplier = 2.0 / (period + 1)
    ema = [closes[0]]
    for price in closes[1:]:
        ema.append(price * multiplier + ema[-1] * (1 - multiplier))

    ema_now = ema[-1]
    ema_prev = ema[-6]  # 5 свечей назад
    current_price = closes[-1]

    if ema_prev == 0:
        return "FLAT"

    slope_pct = (ema_now - ema_prev) / ema_prev * 100
    price_vs_ema = (current_price - ema_now) / ema_now * 100

    if slope_pct > slope_threshold and price_vs_ema > 0:
        return "UP"
    elif slope_pct < -slope_threshold and price_vs_ema < 0:
        return "DOWN"
    return "FLAT"

def is_flat(candles, atr_val):
    ranges = [float(c[2]) - float(c[3]) for c in candles[-10:]]
    avg_range = sum(ranges) / len(ranges) if ranges else atr_val
    return avg_range < 0.6 * atr_val
