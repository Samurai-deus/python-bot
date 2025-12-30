from typing import Optional, Dict
from indicators import rsi, macd, momentum, trend_strength
from core.market_state import MarketState, normalize_states_dict

def calculate_score(states: Dict[str, Optional[MarketState]], directions, is_flat, good_time, candles_map=None, momentum_data=None):
    """
    Система оценки торговых возможностей с весами и проверкой силы тренда.
    
    Args:
        states: Словарь состояний по таймфреймам (значения: MarketState enum или None)
        directions: Словарь направлений
        is_flat: Флаг флэта
        good_time: Торговое время
        candles_map: Словарь свечей по таймфреймам (опционально)
        momentum_data: Данные momentum индикаторов (опционально)
    
    Returns:
        tuple: (score, reasons, details)
    """
    score = 0
    reasons = []
    details = {}
    
    # ========== БАЗОВЫЕ КРИТЕРИИ (макс 50 баллов) ==========
    
    # 1. Согласованность таймфреймов (20 баллов)
    # Проверяем согласованность 4h, 1h и 30m
    direction_4h = directions.get("4h", "FLAT")
    direction_1h = directions.get("1h", "FLAT")
    direction_30m = directions.get("30m", "FLAT")
    
    alignment_score = 0
    alignment_reasons = []
    
    # Согласованность 1h и 30m (10 баллов)
    if direction_1h == direction_30m and direction_30m in ["UP", "DOWN"]:
        alignment_score += 10
        alignment_reasons.append("1H и 30m согласованы")
    
    # Согласованность с 4h (10 баллов) - важнее для фильтрации
    if direction_4h != "FLAT" and direction_4h == direction_30m:
        alignment_score += 10
        alignment_reasons.append("4H подтверждает тренд")
    elif direction_4h != "FLAT" and direction_4h != direction_30m:
        # Конфликт с 4h - уменьшаем score
        alignment_score -= 5
        alignment_reasons.append("⚠️ Конфликт с 4H трендом")
    
    score += max(0, alignment_score)  # Не даем отрицательных баллов
    if alignment_reasons:
        reasons.extend(alignment_reasons)
    details["timeframe_alignment"] = alignment_score > 0
    details["4h_alignment"] = direction_4h == direction_30m if direction_4h != "FLAT" else None
    
    # 2. Состояние 15m (15 баллов)
    states = normalize_states_dict(states)
    
    # Проверка инварианта: все значения должны быть MarketState enum или None
    import logging
    logger = logging.getLogger(__name__)
    for key, state in states.items():
        if state is not None and not isinstance(state, MarketState):
            logger.error(
                f"INVARIANT VIOLATION in calculate_score: state[{key}] = {state} (type: {type(state).__name__}), "
                f"expected MarketState or None. This is an architectural error."
            )
            states[key] = None
    
    state_15m = states.get("15m")
    if state_15m == MarketState.D:  # Rejection
        score += 15
        reasons.append("Чёткий отказ на 15m")
        details["state_15m"] = "D"
    elif state_15m == MarketState.A:  # Impulse
        score += 10
        reasons.append("Импульс на 15m")
        details["state_15m"] = "A"
    elif state_15m == MarketState.C:  # Loss of Control
        score += 8
        reasons.append("Потеря контроля на 15m")
        details["state_15m"] = "C"
    else:
        # Сохраняем строковое представление для деталей (может быть None)
        details["state_15m"] = state_15m.value if state_15m else None
    
    # 3. Не флэт (10 баллов)
    if not is_flat:
        score += 10
        reasons.append("Рынок не во флэте")
        details["is_flat"] = False
    else:
        details["is_flat"] = True
    
    # ========== MOMENTUM И СИЛА ТРЕНДА (макс 35 баллов) ==========
    
    if candles_map and momentum_data:
        # 4. RSI анализ (10 баллов)
        rsi_15m = momentum_data.get("rsi_15m", 50)
        if 30 < rsi_15m < 70:  # Не перекуплен/перепродан
            score += 5
            reasons.append(f"RSI в нормальной зоне ({rsi_15m:.1f})")
        elif (directions.get("30m") == "UP" and 40 < rsi_15m < 60) or \
             (directions.get("30m") == "DOWN" and 40 < rsi_15m < 60):
            score += 10
            reasons.append(f"RSI оптимален для входа ({rsi_15m:.1f})")
        details["rsi_15m"] = rsi_15m
        
        # 5. MACD тренд (10 баллов)
        macd_15m = momentum_data.get("macd_15m", {})
        macd_trend = macd_15m.get("trend", "NEUTRAL")
        direction_30m = directions.get("30m", "FLAT")
        
        if (macd_trend == "BULLISH" and direction_30m == "UP") or \
           (macd_trend == "BEARISH" and direction_30m == "DOWN"):
            score += 10
            reasons.append(f"MACD подтверждает тренд ({macd_trend})")
        elif macd_trend != "NEUTRAL":
            score += 5
            reasons.append(f"MACD показывает {macd_trend}")
        details["macd_trend"] = macd_trend
        
        # 6. Сила тренда (10 баллов)
        trend_strength_30m = momentum_data.get("trend_strength_30m", 50)
        if trend_strength_30m >= 70:
            score += 10
            reasons.append(f"Сильный тренд ({trend_strength_30m:.1f}%)")
        elif trend_strength_30m >= 60:
            score += 7
            reasons.append(f"Умеренный тренд ({trend_strength_30m:.1f}%)")
        elif trend_strength_30m >= 50:
            score += 4
            reasons.append(f"Слабый тренд ({trend_strength_30m:.1f}%)")
        details["trend_strength_30m"] = trend_strength_30m
        
        # 7. Bollinger Bands (8 баллов)
        bb_15m = momentum_data.get("bb_15m", {})
        bb_position = bb_15m.get("position", "MIDDLE")
        direction_30m = directions.get("30m", "FLAT")
        
        if (bb_position == "BELOW_LOWER" and direction_30m == "UP") or \
           (bb_position == "ABOVE_UPPER" and direction_30m == "DOWN"):
            score += 8
            reasons.append(f"BB: цена в экстремальной зоне ({bb_position})")
        elif bb_position in ["LOWER", "UPPER"]:
            score += 4
            reasons.append(f"BB: цена у границы ({bb_position})")
        details["bb_position"] = bb_position
        
        # 8. Stochastic (7 баллов)
        stoch_15m = momentum_data.get("stoch_15m", {})
        stoch_signal = stoch_15m.get("signal", "NEUTRAL")
        stoch_k = stoch_15m.get("k", 50)
        
        if (stoch_signal == "OVERSOLD" and direction_30m == "UP") or \
           (stoch_signal == "OVERBOUGHT" and direction_30m == "DOWN"):
            score += 7
            reasons.append(f"Stochastic: {stoch_signal} (K={stoch_k:.1f})")
        elif stoch_signal == "NEUTRAL" and 30 < stoch_k < 70:
            score += 3
            reasons.append(f"Stochastic: нейтрально (K={stoch_k:.1f})")
        details["stoch_signal"] = stoch_signal
        
        # 9. ADX - сила тренда (10 баллов)
        adx_15m = momentum_data.get("adx_15m", {})
        adx_strength = adx_15m.get("strength", "WEAK")
        adx_value = adx_15m.get("adx", 0)
        
        if adx_strength == "STRONG" and adx_value >= 25:
            score += 10
            reasons.append(f"ADX: сильный тренд ({adx_value:.1f})")
        elif adx_strength == "MODERATE" and adx_value >= 20:
            score += 6
            reasons.append(f"ADX: умеренный тренд ({adx_value:.1f})")
        elif adx_strength == "WEAK":
            score += 0
            reasons.append(f"⚠️ ADX: слабый тренд ({adx_value:.1f})")
        details["adx_strength"] = adx_strength
        details["adx_value"] = adx_value
        
        # 10. EMA Crossover (8 баллов)
        ema_cross_15m = momentum_data.get("ema_cross_15m", {})
        ema_signal = ema_cross_15m.get("signal", "NONE")
        
        if (ema_signal == "BULLISH" and direction_30m == "UP") or \
           (ema_signal == "BEARISH" and direction_30m == "DOWN"):
            score += 8
            reasons.append(f"EMA кроссовер: {ema_signal}")
        elif ema_signal != "NONE":
            score += 4
            reasons.append(f"EMA кроссовер: {ema_signal}")
        details["ema_signal"] = ema_signal
    else:
        details["momentum_available"] = False
    
    # ========== ОБЪЕМЫ И ЛИКВИДНОСТЬ (макс 15 баллов) ==========
    
    if candles_map:
        candle_analysis_15m = None
        if "15m" in candles_map and candles_map["15m"]:
            from candle_analysis import get_candle_analysis
            candle_analysis_15m = get_candle_analysis(candles_map["15m"])
        
        if candle_analysis_15m:
            volume_profile = candle_analysis_15m.get("volume_profile", {})
            volume_trend = volume_profile.get("volume_trend", "NORMAL")
            volume_ratio = volume_profile.get("volume_ratio", 1.0)
            
            # 11. Проверка объемов (12 баллов)
            if volume_trend == "HIGH" and volume_ratio > 1.3:
                score += 12
                reasons.append(f"Высокая активность (объем {volume_ratio:.2f}x)")
            elif volume_trend == "NORMAL" and 0.8 <= volume_ratio <= 1.2:
                score += 8
                reasons.append(f"Нормальная активность (объем {volume_ratio:.2f}x)")
            elif volume_trend == "LOW":
                score += 0  # Низкая ликвидность - не торгуем
                reasons.append(f"⚠️ Низкая активность (объем {volume_ratio:.2f}x)")
            details["volume_trend"] = volume_trend
            details["volume_ratio"] = volume_ratio
    else:
        details["volume_available"] = False
    
    # ========== ДОПОЛНИТЕЛЬНЫЕ БОНУСЫ (макс 10 баллов) ==========
    
    # 8. Согласованность состояний (5 баллов)
    if states.get("15m") == states.get("30m") and states.get("30m") is not None:
        score += 5
        reasons.append("Состояния 15m и 30m согласованы")
        details["state_alignment"] = True
    else:
        details["state_alignment"] = False
    
    # 9. Торговое время (5 баллов) - всегда True сейчас
    if good_time:
        score += 5
        reasons.append("Торговое время")
        details["good_time"] = True
    
    details["total_score"] = score
    return score, reasons, details


def market_mode(score):
    """
    Определяет режим рынка на основе score.
    Теперь максимум 125 баллов (100 базовых + 15 волатильность + 10 корреляции).
    """
    if score >= 90:  # ~72% от максимума
        return "TRADE"  # Высокое качество сигнала
    elif score >= 70:  # ~56% от максимума
        return "OBSERVE"  # Среднее качество - наблюдение
    elif score >= 50:  # ~40% от максимума
        return "CAUTION"  # Низкое качество - осторожность
    else:
        return "STOP"  # Не торговать


def get_entry_conditions(states: Dict[str, Optional[MarketState]], directions, score_details):
    """
    Определяет, какие условия входа выполнены.
    
    Args:
        states: Словарь состояний (значения: MarketState enum или None)
        directions: Словарь направлений
        score_details: Детали скоринга
    
    Returns:
        list: Список условий входа
    """
    entry_conditions = []
    
    direction_4h = directions.get("4h", "FLAT")
    direction_30m = directions.get("30m", "FLAT")
    
    # Фильтр: не торгуем против сильного 4h тренда
    if direction_4h != "FLAT" and direction_4h != direction_30m and direction_30m != "FLAT":
        return []  # Конфликт с 4h - не торгуем
    
    states = normalize_states_dict(states)
    
    # Проверка инварианта: все значения должны быть MarketState enum или None
    import logging
    logger = logging.getLogger(__name__)
    for key, state in states.items():
        if state is not None and not isinstance(state, MarketState):
            logger.error(
                f"INVARIANT VIOLATION in get_entry_conditions: state[{key}] = {state} (type: {type(state).__name__}), "
                f"expected MarketState or None. This is an architectural error."
            )
            states[key] = None
    
    state_15m = states.get("15m")
    
    # Основное условие: Rejection (D)
    if state_15m == MarketState.D:
        entry_conditions.append("REJECTION")
    
    # Дополнительные условия при высоком score
    if score_details.get("total_score", 0) >= 70:
        # Сильные импульсы тоже могут быть точками входа
        if state_15m == MarketState.A:
            if score_details.get("trend_strength_30m", 0) >= 70:
                entry_conditions.append("STRONG_IMPULSE")
        
        # Loss of Control при сильном тренде
        if state_15m == MarketState.C:
            if score_details.get("trend_strength_30m", 0) >= 65:
                entry_conditions.append("CONTROL_LOSS")
    
    return entry_conditions
