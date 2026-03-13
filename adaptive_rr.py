"""
Модуль для адаптивного расчета R:R (Risk:Reward) на основе волатильности и условий рынка.
"""
from indicators import atr


def calculate_adaptive_rr(entry, stop, atr_15m, atr_5m, volatility_pct, trend_strength, risk_level):
    """
    Рассчитывает адаптивный R:R на основе волатильности и условий рынка.
    
    Args:
        entry: Цена входа
        stop: Цена стоп-лосса
        atr_15m: ATR на 15m
        atr_5m: ATR на 5m
        volatility_pct: Волатильность в процентах
        trend_strength: Сила тренда (0-100)
        risk_level: Уровень риска ("LOW", "MEDIUM", "HIGH")
    
    Returns:
        dict: {
            "target": цена цели,
            "rr_ratio": коэффициент R:R,
            "risk_pct": риск в процентах,
            "reward_pct": награда в процентах,
            "reason": объяснение расчета
        }
    """
    if entry == 0 or stop == 0:
        return {
            "target": entry,
            "rr_ratio": 1.0,
            "risk_pct": 0,
            "reward_pct": 0,
            "reason": "Некорректные данные"
        }
    
    # Базовое расстояние до стопа
    risk_distance = abs(entry - stop)
    risk_pct = (risk_distance / entry) * 100
    
    # Определяем базовый R:R
    base_rr = 2.0  # Стандартный R:R = 1:2
    
    # Корректировка на основе волатильности
    volatility_multiplier = 1.0
    if volatility_pct > 3.0:  # Высокая волатильность
        volatility_multiplier = 1.5  # Увеличиваем цель
        reason_vol = "высокая волатильность"
    elif volatility_pct > 2.0:  # Средняя волатильность
        volatility_multiplier = 1.2
        reason_vol = "средняя волатильность"
    elif volatility_pct < 1.0:  # Низкая волатильность
        volatility_multiplier = 0.8  # Уменьшаем цель
        reason_vol = "низкая волатильность"
    else:
        reason_vol = "нормальная волатильность"
    
    # Корректировка на основе силы тренда
    trend_multiplier = 1.0
    if trend_strength >= 70:  # Сильный тренд
        trend_multiplier = 1.3  # Увеличиваем цель
        reason_trend = "сильный тренд"
    elif trend_strength >= 60:  # Умеренный тренд
        trend_multiplier = 1.1
        reason_trend = "умеренный тренд"
    elif trend_strength < 50:  # Слабый тренд
        trend_multiplier = 0.9  # Уменьшаем цель
        reason_trend = "слабый тренд"
    else:
        reason_trend = "нормальный тренд"
    
    # Корректировка на основе уровня риска
    risk_multiplier = 1.0
    if risk_level == "LOW":
        risk_multiplier = 1.2  # Можно увеличить цель
        reason_risk = "низкий риск"
    elif risk_level == "MEDIUM":
        risk_multiplier = 1.0
        reason_risk = "средний риск"
    else:  # HIGH
        risk_multiplier = 0.8  # Уменьшаем цель при высоком риске
        reason_risk = "высокий риск"
    
    # Итоговый R:R
    final_rr = base_rr * volatility_multiplier * trend_multiplier * risk_multiplier
    
    # Ограничиваем R:R разумными пределами
    final_rr = max(1.0, min(3.5, final_rr))  # От 1:1 до 1:3.5
    
    # Рассчитываем цель
    if entry > stop:  # LONG
        target = entry + (risk_distance * final_rr)
    else:  # SHORT
        target = entry - (risk_distance * final_rr)
    
    reward_pct = (abs(target - entry) / entry) * 100
    
    reason = f"R:R {final_rr:.2f} (базовый {base_rr:.1f}, {reason_vol}, {reason_trend}, {reason_risk})"
    
    return {
        "target": target,
        "rr_ratio": final_rr,
        "risk_pct": risk_pct,
        "reward_pct": reward_pct,
        "reason": reason
    }


def calculate_volatility_pct(candles, period=20):
    """
    Рассчитывает волатильность в процентах на основе ATR.
    """
    if len(candles) < period:
        return 1.0
    
    atr_val = atr(candles, period)
    current_price = float(candles[-1][4])
    
    if current_price == 0:
        return 1.0
    
    volatility_pct = (atr_val / current_price) * 100
    return volatility_pct

