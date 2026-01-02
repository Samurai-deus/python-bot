"""
Модуль для улучшенной фильтрации волатильности
"""
from indicators import atr
from typing import Dict, List, Tuple


def calculate_volatility_metrics(candles: List, period: int = 20) -> Dict:
    """
    Рассчитывает метрики волатильности.
    
    Returns:
        dict: {
            "atr": ATR значение,
            "atr_pct": ATR в процентах от цены,
            "volatility_level": "LOW"/"NORMAL"/"HIGH"/"EXTREME",
            "volatility_trend": "INCREASING"/"DECREASING"/"STABLE",
            "is_tradeable": bool
        }
    """
    if len(candles) < period:
        return {
            "atr": 0,
            "atr_pct": 0,
            "volatility_level": "UNKNOWN",
            "volatility_trend": "STABLE",
            "is_tradeable": False
        }
    
    current_price = float(candles[-1][4])
    atr_value = atr(candles, period=period)
    atr_pct = (atr_value / current_price) * 100 if current_price > 0 else 0
    
    # Уровень волатильности
    if atr_pct < 0.5:
        volatility_level = "LOW"
        is_tradeable = True  # Низкая волатильность - можно торговать
    elif atr_pct < 1.5:
        volatility_level = "NORMAL"
        is_tradeable = True
    elif atr_pct < 3.0:
        volatility_level = "HIGH"
        is_tradeable = True  # Высокая, но торгуемая
    elif atr_pct < 5.0:
        volatility_level = "HIGH"
        is_tradeable = False  # Слишком высокая - рискованно
    else:
        volatility_level = "EXTREME"
        is_tradeable = False  # Экстремальная - не торгуем
    
    # Тренд волатильности (сравниваем текущий ATR с предыдущим)
    if len(candles) >= period * 2:
        prev_atr = atr(candles[:-period], period=period)
        if atr_value > prev_atr * 1.2:
            volatility_trend = "INCREASING"
        elif atr_value < prev_atr * 0.8:
            volatility_trend = "DECREASING"
        else:
            volatility_trend = "STABLE"
    else:
        volatility_trend = "STABLE"
    
    return {
        "atr": atr_value,
        "atr_pct": atr_pct,
        "volatility_level": volatility_level,
        "volatility_trend": volatility_trend,
        "is_tradeable": is_tradeable
    }


def check_price_spike(candles: List, threshold_pct: float = 2.0) -> Dict:
    """
    Проверяет наличие резкого движения цены (спайка).
    
    Args:
        candles: Список свечей
        threshold_pct: Порог в процентах для определения спайка
    
    Returns:
        dict: {
            "has_spike": bool,
            "spike_direction": "UP"/"DOWN"/"NONE",
            "spike_pct": процент движения,
            "spike_reason": причина (если определена)
        }
    """
    if len(candles) < 3:
        return {
            "has_spike": False,
            "spike_direction": "NONE",
            "spike_pct": 0.0,
            "spike_reason": None
        }
    
    # Берем последние 3 свечи для анализа
    recent = candles[-3:]
    current = float(recent[-1][4])
    prev = float(recent[-2][4])
    prev_prev = float(recent[-3][4])
    
    # Изменение за последнюю свечу
    change_pct = abs((current - prev) / prev * 100) if prev > 0 else 0
    
    # Изменение за предыдущую свечу
    prev_change_pct = abs((prev - prev_prev) / prev_prev * 100) if prev_prev > 0 else 0
    
    has_spike = False
    spike_direction = "NONE"
    spike_pct = 0.0
    spike_reason = None
    
    # Проверяем резкое движение
    if change_pct >= threshold_pct:
        has_spike = True
        spike_pct = change_pct
        
        if current > prev:
            spike_direction = "UP"
        else:
            spike_direction = "DOWN"
        
        # Анализируем причину (если возможно)
        # Если предыдущее движение было в том же направлении - возможно тренд
        if prev_change_pct > threshold_pct * 0.5:
            if (spike_direction == "UP" and prev > prev_prev) or \
               (spike_direction == "DOWN" and prev < prev_prev):
                spike_reason = "TREND_CONTINUATION"
            else:
                spike_reason = "REVERSAL"
        else:
            spike_reason = "UNEXPECTED_MOVE"
    
    return {
        "has_spike": has_spike,
        "spike_direction": spike_direction,
        "spike_pct": spike_pct,
        "spike_reason": spike_reason
    }


def get_volatility_score(volatility_metrics: Dict) -> Tuple[int, List[str]]:
    """
    Возвращает score для волатильности (0-15 баллов).
    
    Returns:
        tuple: (score, reasons)
    """
    score = 0
    reasons = []
    
    volatility_level = volatility_metrics.get("volatility_level", "UNKNOWN")
    volatility_trend = volatility_metrics.get("volatility_trend", "STABLE")
    atr_pct = volatility_metrics.get("atr_pct", 0)
    
    # Оптимальная волатильность для торговли
    if volatility_level == "NORMAL":
        score += 10
        reasons.append(f"Оптимальная волатильность ({atr_pct:.2f}%)")
    elif volatility_level == "LOW":
        score += 5
        reasons.append(f"Низкая волатильность ({atr_pct:.2f}%) - меньше возможностей")
    elif volatility_level == "HIGH" and volatility_metrics.get("is_tradeable", False):
        score += 7
        reasons.append(f"Высокая волатильность ({atr_pct:.2f}%) - больше возможностей, но выше риск")
    elif volatility_level in ["HIGH", "EXTREME"]:
        score += 0
        reasons.append(f"⚠️ Слишком высокая волатильность ({atr_pct:.2f}%)")
    
    # Тренд волатильности
    if volatility_trend == "DECREASING":
        score += 3
        reasons.append("Волатильность снижается - стабилизация")
    elif volatility_trend == "INCREASING":
        score += 2
        reasons.append("Волатильность растет - осторожность")
    
    return min(15, score), reasons

