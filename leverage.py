"""
Расчет рекомендуемого плеча на основе волатильности и риска
"""
from indicators import atr
from risk import risk_level


def calculate_leverage(states, atr_15m, entry_price, stop_price, side="LONG"):
    """
    Рассчитывает рекомендуемое плечо на основе:
    - Уровня риска
    - Волатильности (ATR)
    - Расстояния до стоп-лосса
    
    Args:
        states: Словарь состояний по таймфреймам
        atr_15m: Значение ATR на 15m таймфрейме
        entry_price: Цена входа
        stop_price: Цена стоп-лосса
        side: "LONG" или "SHORT"
    
    Returns:
        int: Рекомендуемое плечо (1-10)
    """
    risk = risk_level(states)
    
    # Базовое плечо в зависимости от риска
    if risk == "HIGH":
        base_leverage = 1
    elif risk == "MEDIUM":
        base_leverage = 2
    else:  # LOW
        base_leverage = 3
    
    # Рассчитываем расстояние до стоп-лосса в процентах
    if side == "LONG":
        stop_distance_pct = abs((entry_price - stop_price) / entry_price) * 100
    else:  # SHORT
        stop_distance_pct = abs((stop_price - entry_price) / entry_price) * 100
    
    # Корректируем плечо в зависимости от расстояния до стопа
    # Чем ближе стоп, тем выше может быть плечо (но не более 10x)
    if stop_distance_pct < 0.5:
        leverage_multiplier = 2.0
    elif stop_distance_pct < 1.0:
        leverage_multiplier = 1.5
    elif stop_distance_pct < 2.0:
        leverage_multiplier = 1.0
    else:
        leverage_multiplier = 0.7
    
    # Корректируем в зависимости от волатильности (ATR)
    # Высокая волатильность = меньше плечо
    atr_pct = (atr_15m / entry_price) * 100 if entry_price > 0 else 0
    
    if atr_pct > 3.0:
        volatility_multiplier = 0.7
    elif atr_pct > 2.0:
        volatility_multiplier = 0.85
    elif atr_pct > 1.0:
        volatility_multiplier = 1.0
    else:
        volatility_multiplier = 1.1
    
    recommended_leverage = int(base_leverage * leverage_multiplier * volatility_multiplier)
    
    # Ограничиваем плечо от 1x до 10x
    return max(1, min(10, recommended_leverage))

