from typing import Optional, Dict
from core.market_state import MarketState, normalize_states_dict

def risk_level(states: Dict[str, Optional[MarketState]], directions=None) -> str:
    """
    Базовая оценка риска на основе состояний таймфреймов.
    
    Args:
        states: Словарь состояний по таймфреймам (значения: MarketState enum или None)
        directions: Словарь направлений (опционально, для проверки конфликтов с 4h)
    
    Returns:
        str: "LOW", "MEDIUM" или "HIGH"
    """
    # Нормализуем и проверяем инварианты
    states = normalize_states_dict(states)
    
    # Проверка инварианта: все значения должны быть MarketState enum или None
    import logging
    logger = logging.getLogger(__name__)
    for key, state in states.items():
        if state is not None and not isinstance(state, MarketState):
            logger.error(
                f"INVARIANT VIOLATION in risk_level: state[{key}] = {state} (type: {type(state).__name__}), "
                f"expected MarketState or None. This is an architectural error."
            )
            states[key] = None
    
    # Абсолютный запрет
    if states.get("1h") is None:
        return "HIGH"

    risk = 0

    # конфликт таймфреймов
    if states.get("30m") != states.get("15m"):
        risk += 1

    # Отказ против импульса
    state_15m = states.get("15m")
    state_30m = states.get("30m")
    if state_15m == MarketState.D:
        if state_30m == MarketState.A:
            risk += 1
    
    # Конфликт с 4h трендом - критично
    if directions:
        direction_4h = directions.get("4h", "FLAT")
        direction_30m = directions.get("30m", "FLAT")
        if direction_4h != "FLAT" and direction_4h != direction_30m and direction_30m != "FLAT":
            risk += 2  # Сильный конфликт с дневным трендом

    if risk == 0:
        return "LOW"
    elif risk == 1:
        return "MEDIUM"
    elif risk >= 3:
        return "HIGH"  # Критический конфликт
    else:
        return "MEDIUM"


def calculate_stop_distance(entry, stop, atr_15m, entry_price):
    """
    Рассчитывает расстояние до стопа и проверяет его адекватность.
    
    Returns:
        dict: {
            "stop_distance_pct": процент расстояния,
            "stop_distance_atr": расстояние в ATR,
            "is_valid": bool,
            "risk_level": "LOW"/"MEDIUM"/"HIGH"
        }
    """
    if entry == 0 or stop == 0:
        return {
            "stop_distance_pct": 0,
            "stop_distance_atr": 0,
            "is_valid": False,
            "risk_level": "HIGH"
        }
    
    stop_distance = abs(entry - stop)
    stop_distance_pct = (stop_distance / entry) * 100
    
    # Расстояние в ATR
    stop_distance_atr = stop_distance / atr_15m if atr_15m > 0 else 0
    
    # Проверка адекватности стопа
    is_valid = True
    risk_level_stop = "LOW"
    
    # Слишком маленький стоп (< 0.3% или < 0.5 ATR) - рисковано
    if stop_distance_pct < 0.3 or stop_distance_atr < 0.5:
        is_valid = False
        risk_level_stop = "HIGH"
    # Очень маленький стоп (0.3-0.5% или 0.5-1 ATR) - средний риск
    elif stop_distance_pct < 0.5 or stop_distance_atr < 1.0:
        risk_level_stop = "MEDIUM"
    # Нормальный стоп (0.5-2% или 1-3 ATR) - низкий риск
    elif stop_distance_pct <= 2.0 and stop_distance_atr <= 3.0:
        risk_level_stop = "LOW"
    # Большой стоп (> 2% или > 3 ATR) - может быть слишком консервативным
    else:
        risk_level_stop = "MEDIUM"
    
    return {
        "stop_distance_pct": stop_distance_pct,
        "stop_distance_atr": stop_distance_atr,
        "is_valid": is_valid,
        "risk_level": risk_level_stop
    }


def enhanced_risk_level(states: Dict[str, Optional[MarketState]], stop_info=None, volume_info=None, momentum_data=None, candles_map=None, directions=None) -> str:
    """
    Улучшенная оценка риска с учетом размера стопа, объемов и индикаторов.
    
    Args:
        states: Словарь состояний (значения: MarketState enum или None)
        stop_info: Информация о стопе (из calculate_stop_distance)
        volume_info: Информация об объемах
        momentum_data: Данные индикаторов (опционально)
        candles_map: Словарь свечей (опционально)
        directions: Словарь направлений (опционально)
    
    Returns:
        str: "LOW", "MEDIUM", "HIGH"
    """
    base_risk = risk_level(states, directions=directions)
    
    if base_risk == "HIGH":
        return "HIGH"
    
    # Дополнительные проверки
    risk_score = 0
    risk_reasons = []
    
    # Проверка стопа
    if stop_info:
        if not stop_info.get("is_valid", True):
            return "HIGH"  # Недопустимый стоп
        
        stop_risk = stop_info.get("risk_level", "LOW")
        if stop_risk == "HIGH":
            risk_score += 2
            risk_reasons.append("Стоп-лосс слишком рискованный")
        elif stop_risk == "MEDIUM":
            risk_score += 1
    
    # Проверка объемов
    if volume_info:
        volume_trend = volume_info.get("volume_trend", "NORMAL")
        volume_ratio = volume_info.get("volume_ratio", 1.0)
        if volume_trend == "LOW" or volume_ratio < 0.5:
            risk_score += 2
            risk_reasons.append("Критически низкая ликвидность")
        elif volume_trend == "LOW":
            risk_score += 1
            risk_reasons.append("Низкая ликвидность")
    
    # Проверка индикаторов
    if momentum_data:
        # ADX - слабый тренд увеличивает риск
        adx_15m = momentum_data.get("adx_15m", {})
        adx_strength = adx_15m.get("strength", "WEAK")
        if adx_strength == "WEAK":
            risk_score += 1
            risk_reasons.append("Слабый тренд (ADX)")
        
        # RSI - экстремальные значения
        rsi_15m = momentum_data.get("rsi_15m", 50)
        if rsi_15m > 80 or rsi_15m < 20:
            risk_score += 1
            risk_reasons.append(f"RSI в экстремальной зоне ({rsi_15m:.1f})")
        
        # Bollinger Bands - цена за пределами полос
        bb_15m = momentum_data.get("bb_15m", {})
        bb_position = bb_15m.get("position", "MIDDLE")
        if bb_position in ["ABOVE_UPPER", "BELOW_LOWER"]:
            risk_score += 1
            risk_reasons.append(f"Цена в экстремальной зоне BB ({bb_position})")
        
        # Stochastic - перекупленность/перепроданность
        stoch_15m = momentum_data.get("stoch_15m", {})
        stoch_signal = stoch_15m.get("signal", "NEUTRAL")
        if stoch_signal in ["OVERBOUGHT", "OVERSOLD"]:
            # Это может быть как хорошо, так и плохо, в зависимости от направления
            # Не добавляем риск, но учитываем в контексте
            pass
    
    # Проверка волатильности (ATR)
    if candles_map and candles_map.get("15m"):
        try:
            from indicators import atr
            atr_15m = atr(candles_map["15m"])
            current_price = float(candles_map["15m"][-1][4])
            atr_pct = (atr_15m / current_price) * 100 if current_price > 0 else 0
            
            # Очень высокая волатильность (>5%) - рискованно
            if atr_pct > 5.0:
                risk_score += 2
                risk_reasons.append(f"Очень высокая волатильность ({atr_pct:.2f}%)")
            # Высокая волатильность (3-5%) - средний риск
            elif atr_pct > 3.0:
                risk_score += 1
                risk_reasons.append(f"Высокая волатильность ({atr_pct:.2f}%)")
        except Exception:
            pass
    
    # Итоговая оценка
    if base_risk == "LOW" and risk_score == 0:
        return "LOW"
    elif base_risk == "LOW" and risk_score <= 1:
        return "MEDIUM"
    elif base_risk == "MEDIUM" or (risk_score >= 2 and risk_score < 4):
        return "MEDIUM"
    elif risk_score >= 4:
        return "HIGH"
    else:
        return "MEDIUM"
