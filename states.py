def impulse(candles, atr_val):
    bodies = []
    for c in candles[-4:]:
        bodies.append(abs(float(c[4]) - float(c[1])))
    return sum(b > 1.3 * atr_val for b in bodies) >= 2


def acceptance(candles, atr_val):
    ranges = []
    for c in candles[-7:]:
        ranges.append(float(c[2]) - float(c[3]))
    return max(ranges) < 0.55 * atr_val


def loss_of_control(candles):
    wicks = []
    bodies = []

    for c in candles[-5:]:
        open_ = float(c[1])
        close = float(c[4])
        high = float(c[2])

        bodies.append(abs(close - open_))
        wicks.append(high - max(open_, close))

    return (
        max(wicks) > 1.5 * (sum(wicks) / len(wicks))
        and max(wicks) > max(bodies)
    )


def rejection(candles, atr_val):
    last = candles[-1]
    body = abs(float(last[4]) - float(last[1]))
    return body > 1.1 * atr_val

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

def market_direction(candles, period=20):
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

    SLOPE_THRESHOLD = 0.1  # минимальный наклон 0.1%

    if slope_pct > SLOPE_THRESHOLD and price_vs_ema > 0:
        return "UP"
    elif slope_pct < -SLOPE_THRESHOLD and price_vs_ema < 0:
        return "DOWN"
    return "FLAT"

def is_flat(candles, atr_val):
    ranges = [float(c[2]) - float(c[3]) for c in candles[-10:]]
    return max(ranges) < 0.6 * atr_val
