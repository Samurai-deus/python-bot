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

def market_direction(candles):
    highs = [float(c[2]) for c in candles[-10:]]
    lows = [float(c[3]) for c in candles[-10:]]

    if highs[-1] > highs[0] and lows[-1] > lows[0]:
        return "UP"
    if highs[-1] < highs[0] and lows[-1] < lows[0]:
        return "DOWN"
    return "FLAT"

def is_flat(candles, atr_val):
    ranges = [float(c[2]) - float(c[3]) for c in candles[-10:]]
    return max(ranges) < 0.6 * atr_val
