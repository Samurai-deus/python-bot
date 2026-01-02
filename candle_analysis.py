"""
Продвинутый анализ свечных паттернов и объемов
"""
from typing import Optional, Dict, List, Any


def analyze_candlestick_pattern(candles: List) -> Dict[str, Any]:
    """
    Анализирует свечные паттерны на последних свечах.
    
    Args:
        candles: Список свечей (последние 10-20 свечей)
    
    Returns:
        dict: Результаты анализа с паттернами и сигналами
    """
    if len(candles) < 3:
        return {"pattern": None, "signal": "NEUTRAL", "strength": 0}
    
    last = candles[-1]
    prev = candles[-2] if len(candles) > 1 else None
    prev2 = candles[-3] if len(candles) > 2 else None
    
    open_price = float(last[1])
    close_price = float(last[4])
    high = float(last[2])
    low = float(last[3])
    
    body = abs(close_price - open_price)
    upper_wick = high - max(open_price, close_price)
    lower_wick = min(open_price, close_price) - low
    total_range = high - low
    
    # Определяем тип свечи
    is_bullish = close_price > open_price
    is_bearish = close_price < open_price
    is_doji = body < total_range * 0.1
    
    pattern = None
    signal = "NEUTRAL"
    strength = 0
    
    # Анализ паттернов
    if is_doji:
        pattern = "DOJI"
        signal = "NEUTRAL"
        strength = 1
    elif upper_wick > body * 2 and is_bearish:
        pattern = "SHOOTING_STAR"
        signal = "BEARISH"
        strength = 3
    elif lower_wick > body * 2 and is_bullish:
        pattern = "HAMMER"
        signal = "BULLISH"
        strength = 3
    elif body > total_range * 0.7:
        if is_bullish:
            pattern = "MARUBOZU_BULLISH"
            signal = "BULLISH"
            strength = 4
        else:
            pattern = "MARUBOZU_BEARISH"
            signal = "BEARISH"
            strength = 4
    
    # Анализ комбинаций свечей
    if prev and prev2:
        prev_close = float(prev[4])
        prev_open = float(prev[1])
        prev2_close = float(prev2[4])
        
        # Engulfing patterns
        if (is_bullish and prev_close < prev_open and
            open_price < prev_close and close_price > prev_open):
            pattern = "BULLISH_ENGULFING"
            signal = "BULLISH"
            strength = 4
        elif (is_bearish and prev_close > prev_open and
              open_price > prev_close and close_price < prev_open):
            pattern = "BEARISH_ENGULFING"
            signal = "BEARISH"
            strength = 4
        
        # Three line patterns
        if (is_bullish and prev_close > prev_open and prev2_close > float(prev2[1]) and
            close_price > prev_close and prev_close > prev2_close):
            pattern = "THREE_WHITE_SOLDIERS"
            signal = "BULLISH"
            strength = 5
        elif (is_bearish and prev_close < prev_open and prev2_close < float(prev2[1]) and
              close_price < prev_close and prev_close < prev2_close):
            pattern = "THREE_BLACK_CROWS"
            signal = "BEARISH"
            strength = 5
    
    return {
        "pattern": pattern,
        "signal": signal,
        "strength": strength,
        "body_ratio": body / total_range if total_range > 0 else 0,
        "upper_wick_ratio": upper_wick / total_range if total_range > 0 else 0,
        "lower_wick_ratio": lower_wick / total_range if total_range > 0 else 0,
    }


def analyze_volume_profile(candles: List) -> Dict[str, any]:
    """
    Анализирует профиль объема (если доступен) или ценовой профиль.
    
    Args:
        candles: Список свечей
    
    Returns:
        dict: Анализ объемов
    """
    if len(candles) < 5:
        return {"volume_trend": "NEUTRAL", "volume_ratio": 1.0}
    
    # Анализируем диапазоны цен (ценовой профиль)
    ranges = []
    for candle in candles[-10:]:
        high = float(candle[2])
        low = float(candle[3])
        ranges.append(high - low)
    
    avg_range = sum(ranges) / len(ranges)
    current_range = ranges[-1]
    
    volume_ratio = current_range / avg_range if avg_range > 0 else 1.0
    
    if volume_ratio > 1.5:
        volume_trend = "HIGH"
    elif volume_ratio < 0.7:
        volume_trend = "LOW"
    else:
        volume_trend = "NORMAL"
    
    return {
        "volume_trend": volume_trend,
        "volume_ratio": volume_ratio,
        "avg_range": avg_range,
        "current_range": current_range,
    }


def get_candle_analysis(candles: List) -> Dict[str, any]:
    """
    Полный анализ свечей: паттерны + объемы.
    
    Args:
        candles: Список свечей
    
    Returns:
        dict: Полный анализ свечей
    """
    pattern_analysis = analyze_candlestick_pattern(candles)
    volume_analysis = analyze_volume_profile(candles)
    
    return {
        **pattern_analysis,
        **volume_analysis,
    }

