def atr(candles, period=14):
    trs = []

    for i in range(1, len(candles)):
        high = float(candles[i][2])
        low = float(candles[i][3])
        prev_close = float(candles[i-1][4])

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        trs.append(tr)

    return sum(trs[-period:]) / period


def rsi(candles, period=14):
    """
    Рассчитывает RSI (Relative Strength Index).
    Возвращает значение от 0 до 100.
    """
    if len(candles) < period + 1:
        return 50.0  # Нейтральное значение
    
    closes = [float(c[4]) for c in candles]
    gains = []
    losses = []
    
    for i in range(1, len(closes)):
        change = closes[i] - closes[i-1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
    
    if len(gains) < period:
        return 50.0
    
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    
    if avg_loss == 0:
        return 100.0
    
    rs = avg_gain / avg_loss
    rsi_value = 100 - (100 / (1 + rs))
    
    return rsi_value


def macd(candles, fast_period=12, slow_period=26, signal_period=9):
    """
    Рассчитывает MACD (Moving Average Convergence Divergence).
    Возвращает словарь с macd, signal, histogram.
    """
    if len(candles) < slow_period + signal_period:
        return {"macd": 0, "signal": 0, "histogram": 0, "trend": "NEUTRAL"}
    
    closes = [float(c[4]) for c in candles]
    
    # EMA быстрая
    def ema(data, period):
        multiplier = 2 / (period + 1)
        ema_values = [data[0]]
        for price in data[1:]:
            ema_values.append((price * multiplier) + (ema_values[-1] * (1 - multiplier)))
        return ema_values
    
    fast_ema = ema(closes, fast_period)
    slow_ema = ema(closes, slow_period)
    
    # MACD линия
    macd_line = [fast_ema[i] - slow_ema[i] for i in range(len(slow_ema))]
    
    # Signal линия (EMA от MACD)
    signal_line = ema(macd_line[-signal_period:], signal_period)
    
    # Histogram
    histogram = macd_line[-1] - signal_line[-1] if signal_line else 0
    
    # Определение тренда
    if histogram > 0 and macd_line[-1] > signal_line[-1]:
        trend = "BULLISH"
    elif histogram < 0 and macd_line[-1] < signal_line[-1]:
        trend = "BEARISH"
    else:
        trend = "NEUTRAL"
    
    return {
        "macd": macd_line[-1] if macd_line else 0,
        "signal": signal_line[-1] if signal_line else 0,
        "histogram": histogram,
        "trend": trend
    }


def momentum(candles, period=10):
    """
    Рассчитывает momentum (скорость изменения цены).
    """
    if len(candles) < period + 1:
        return 0.0
    
    current_close = float(candles[-1][4])
    past_close = float(candles[-period-1][4])
    
    return ((current_close - past_close) / past_close) * 100


def trend_strength(candles, period=20):
    """
    Оценивает силу тренда на основе последовательности максимумов и минимумов.
    Возвращает значение от 0 до 100.
    """
    if len(candles) < period:
        return 50.0
    
    highs = [float(c[2]) for c in candles[-period:]]
    lows = [float(c[3]) for c in candles[-period:]]
    
    # Подсчет восходящих и нисходящих свечей
    up_count = 0
    down_count = 0
    
    for i in range(1, len(highs)):
        if highs[i] > highs[i-1] and lows[i] > lows[i-1]:
            up_count += 1
        elif highs[i] < highs[i-1] and lows[i] < lows[i-1]:
            down_count += 1
    
    total = up_count + down_count
    if total == 0:
        return 50.0
    
    # Сила тренда: чем больше последовательных движений, тем сильнее тренд
    strength = (max(up_count, down_count) / total) * 100
    
    return strength


def bollinger_bands(candles, period=20, std_dev=2):
    """
    Рассчитывает Bollinger Bands.
    Возвращает словарь с upper, middle (SMA), lower, и позицией цены.
    """
    if len(candles) < period:
        return {
            "upper": 0,
            "middle": 0,
            "lower": 0,
            "position": "MIDDLE",  # MIDDLE, UPPER, LOWER, ABOVE_UPPER, BELOW_LOWER
            "width_pct": 0
        }
    
    closes = [float(c[4]) for c in candles[-period:]]
    
    # SMA (средняя линия)
    sma = sum(closes) / len(closes)
    
    # Стандартное отклонение
    variance = sum((x - sma) ** 2 for x in closes) / len(closes)
    std = variance ** 0.5
    
    # Верхняя и нижняя полосы
    upper = sma + (std_dev * std)
    lower = sma - (std_dev * std)
    
    # Текущая цена
    current_price = closes[-1]
    
    # Позиция цены относительно полос
    width = upper - lower
    width_pct = (width / sma) * 100 if sma > 0 else 0
    
    if current_price > upper:
        position = "ABOVE_UPPER"
    elif current_price < lower:
        position = "BELOW_LOWER"
    elif current_price > sma:
        position = "UPPER"
    elif current_price < sma:
        position = "LOWER"
    else:
        position = "MIDDLE"
    
    return {
        "upper": upper,
        "middle": sma,
        "lower": lower,
        "position": position,
        "width_pct": width_pct
    }


def stochastic(candles, k_period=14, d_period=3):
    """
    Рассчитывает Stochastic Oscillator (%K и %D).
    Возвращает значения от 0 до 100.
    """
    if len(candles) < k_period + d_period:
        return {
            "k": 50.0,
            "d": 50.0,
            "signal": "NEUTRAL"  # OVERBOUGHT, OVERSOLD, NEUTRAL
        }
    
    # Берем последние k_period свечей
    recent_candles = candles[-k_period:]
    highs = [float(c[2]) for c in recent_candles]
    lows = [float(c[3]) for c in recent_candles]
    closes = [float(c[4]) for c in recent_candles]
    
    highest_high = max(highs)
    lowest_low = min(lows)
    current_close = closes[-1]
    
    # %K
    if highest_high == lowest_low:
        k = 50.0
    else:
        k = ((current_close - lowest_low) / (highest_high - lowest_low)) * 100
    
    # %D (SMA от %K за d_period периодов)
    # Для упрощения используем последние значения
    k_values = []
    for i in range(len(candles) - k_period, len(candles)):
        if i >= 0:
            period_highs = [float(c[2]) for c in candles[max(0, i-k_period+1):i+1]]
            period_lows = [float(c[3]) for c in candles[max(0, i-k_period+1):i+1]]
            period_close = float(candles[i][4])
            
            period_highest = max(period_highs) if period_highs else period_close
            period_lowest = min(period_lows) if period_lows else period_close
            
            if period_highest == period_lowest:
                k_val = 50.0
            else:
                k_val = ((period_close - period_lowest) / (period_highest - period_lowest)) * 100
            k_values.append(k_val)
    
    d = sum(k_values[-d_period:]) / len(k_values[-d_period:]) if k_values else 50.0
    
    # Сигнал
    if k > 80:
        signal = "OVERBOUGHT"
    elif k < 20:
        signal = "OVERSOLD"
    else:
        signal = "NEUTRAL"
    
    return {
        "k": k,
        "d": d,
        "signal": signal
    }


def adx(candles, period=14):
    """
    Рассчитывает ADX (Average Directional Index) - индикатор силы тренда.
    Возвращает значение от 0 до 100. >25 = сильный тренд.
    """
    if len(candles) < period * 2:
        return {
            "adx": 0,
            "strength": "WEAK"  # WEAK, MODERATE, STRONG
        }
    
    # Расчет +DM и -DM
    plus_dm = []
    minus_dm = []
    
    for i in range(1, len(candles)):
        high_diff = float(candles[i][2]) - float(candles[i-1][2])
        low_diff = float(candles[i-1][3]) - float(candles[i][3])
        
        if high_diff > low_diff and high_diff > 0:
            plus_dm.append(high_diff)
        else:
            plus_dm.append(0)
        
        if low_diff > high_diff and low_diff > 0:
            minus_dm.append(low_diff)
        else:
            minus_dm.append(0)
    
    # Расчет True Range
    trs = []
    for i in range(1, len(candles)):
        high = float(candles[i][2])
        low = float(candles[i][3])
        prev_close = float(candles[i-1][4])
        
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        trs.append(tr)
    
    # Сглаживание (упрощенная версия)
    if len(plus_dm) < period or len(trs) < period:
        return {"adx": 0, "strength": "WEAK"}
    
    avg_plus_dm = sum(plus_dm[-period:]) / period
    avg_minus_dm = sum(minus_dm[-period:]) / period
    avg_tr = sum(trs[-period:]) / period
    
    if avg_tr == 0:
        return {"adx": 0, "strength": "WEAK"}
    
    # +DI и -DI
    plus_di = (avg_plus_dm / avg_tr) * 100
    minus_di = (avg_minus_dm / avg_tr) * 100
    
    # DX
    di_sum = plus_di + minus_di
    if di_sum == 0:
        return {"adx": 0, "strength": "WEAK"}
    
    dx = (abs(plus_di - minus_di) / di_sum) * 100
    
    # ADX (упрощенная версия - просто DX)
    adx_value = dx
    
    # Сила тренда
    if adx_value >= 25:
        strength = "STRONG"
    elif adx_value >= 20:
        strength = "MODERATE"
    else:
        strength = "WEAK"
    
    return {
        "adx": adx_value,
        "strength": strength,
        "plus_di": plus_di,
        "minus_di": minus_di
    }


def ema_crossover(candles, fast_period=12, slow_period=26):
    """
    Определяет пересечение EMA (быстрая и медленная).
    Возвращает сигнал о кроссовере.
    """
    if len(candles) < slow_period + 1:
        return {
            "signal": "NONE",  # BULLISH, BEARISH, NONE
            "fast_ema": 0,
            "slow_ema": 0
        }
    
    closes = [float(c[4]) for c in candles]
    
    def ema(data, period):
        multiplier = 2 / (period + 1)
        ema_values = [data[0]]
        for price in data[1:]:
            ema_values.append((price * multiplier) + (ema_values[-1] * (1 - multiplier)))
        return ema_values
    
    fast_ema_vals = ema(closes, fast_period)
    slow_ema_vals = ema(closes, slow_period)
    
    # Берем последние значения
    fast_current = fast_ema_vals[-1]
    fast_prev = fast_ema_vals[-2] if len(fast_ema_vals) > 1 else fast_current
    slow_current = slow_ema_vals[-1]
    slow_prev = slow_ema_vals[-2] if len(slow_ema_vals) > 1 else slow_current
    
    # Определение сигнала
    signal = "NONE"
    if fast_prev <= slow_prev and fast_current > slow_current:
        signal = "BULLISH"  # Быстрая пересекла медленную снизу вверх
    elif fast_prev >= slow_prev and fast_current < slow_current:
        signal = "BEARISH"  # Быстрая пересекла медленную сверху вниз
    
    return {
        "signal": signal,
        "fast_ema": fast_current,
        "slow_ema": slow_current
    }


def volume_analysis(candles, period=20):
    """
    Анализирует объемы (используя диапазон как прокси для объема).
    Возвращает информацию о тренде объемов.
    """
    if len(candles) < period:
        return {
            "volume_trend": "NORMAL",
            "volume_ratio": 1.0,
            "is_high_volume": False
        }
    
    # Используем диапазон (high - low) как прокси для объема
    ranges = []
    for c in candles[-period:]:
        high = float(c[2])
        low = float(c[3])
        ranges.append(high - low)
    
    current_range = ranges[-1]
    avg_range = sum(ranges[:-1]) / len(ranges[:-1]) if len(ranges) > 1 else current_range
    
    volume_ratio = current_range / avg_range if avg_range > 0 else 1.0
    
    # Определение тренда
    if volume_ratio > 1.5:
        volume_trend = "HIGH"
        is_high_volume = True
    elif volume_ratio < 0.7:
        volume_trend = "LOW"
        is_high_volume = False
    else:
        volume_trend = "NORMAL"
        is_high_volume = False
    
    return {
        "volume_trend": volume_trend,
        "volume_ratio": volume_ratio,
        "is_high_volume": is_high_volume
    }
