from core.market_state import MarketState, get_state_text, normalize_states_dict

def build_signal(symbol, states: dict[str, MarketState | None], risk, directions, zone=None, 
                 position_size=None, leverage=None, candle_analysis=None):
    """
    Формирует торговый сигнал с полной информацией.
    
    Args:
        symbol: Торговая пара
        states: Словарь состояний по таймфреймам (значения: MarketState enum или None)
        risk: Уровень риска
        directions: Словарь направлений
        zone: Словарь с entry, stop, target (опционально)
        position_size: Размер позиции в USDT (опционально)
        leverage: Рекомендуемое плечо (опционально)
        candle_analysis: Результаты анализа свечей (опционально)
    """
    bias = directions.get("30m", "FLAT")

    states = normalize_states_dict(states)
    
    # Проверка инварианта: все значения должны быть MarketState enum или None
    import logging
    logger = logging.getLogger(__name__)
    for key, state in states.items():
        if state is not None and not isinstance(state, MarketState):
            logger.error(
                f"INVARIANT VIOLATION in build_signal: state[{key}] = {state} (type: {type(state).__name__}), "
                f"expected MarketState or None. This is an architectural error."
            )
            states[key] = None
    
    action = "НЕ ТОРГОВАТЬ"
    state_15m = states.get("15m")
    if state_15m == MarketState.D and bias in ["UP", "DOWN"] and risk != "HIGH":
        action = f"Приоритет: {'LONG' if bias=='UP' else 'SHORT'}"

    zone_text = ""
    if zone:
        # Рассчитываем R-ratio
        if bias == "UP":
            risk_amount = zone['entry'] - zone['stop']
            reward_amount = zone['target'] - zone['entry']
        else:
            risk_amount = zone['stop'] - zone['entry']
            reward_amount = zone['entry'] - zone['target']
        
        r_ratio = abs(reward_amount / risk_amount) if risk_amount != 0 else 0
        
        zone_text = "\n💰 **ПАРАМЕТРЫ ВХОДА:**\n"
        zone_text += f"• Вход: `{zone['entry']:.4f}`\n"
        zone_text += f"• Стоп: `{zone['stop']:.4f}`\n"
        zone_text += f"• Цель: `{zone['target']:.4f}`\n"
        zone_text += f"• R:R: `{r_ratio:.2f}`\n"

    # Информация о позиции
    position_text = ""
    if position_size or leverage:
        position_text = "\n💼 **ПОЗИЦИЯ:**\n"
        if position_size:
            position_text += f"• Размер: `{position_size:.2f}` USDT\n"
        if leverage:
            position_text += f"• Плечо: `{leverage:.1f}x`\n"

    # Аналитика свечей
    candle_text = ""
    if candle_analysis:
        pattern = candle_analysis.get("pattern")
        signal = candle_analysis.get("signal")
        strength = candle_analysis.get("strength", 0)
        
        if pattern:
            signal_emoji = "🟢" if signal == "BULLISH" else "🔴" if signal == "BEARISH" else "⚪"
            candle_text = "\n🕯 **АНАЛИТИКА СВЕЧЕЙ:**\n"
            candle_text += f"• Паттерн: `{pattern}`\n"
            candle_text += f"• Сигнал: {signal_emoji} `{signal}`\n"
            candle_text += f"• Сила: `{strength}/5`\n"

    # Эмодзи для риска
    if risk == "HIGH":
        risk_emoji = "🔴"
    elif risk == "MEDIUM":
        risk_emoji = "🟡"
    else:
        risk_emoji = "🟢"
    
    # Эмодзи для направления
    direction_emoji = "🟢" if action.startswith("LONG") else "🔴" if action.startswith("SHORT") else "⚪"
    
    signal_msg = f"📊 **{symbol}**\n\n"
    signal_msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    signal_msg += "📈 **КОНТЕКСТ:**\n"
    signal_msg += f"• 1H: `{get_state_text(states.get('1h'))}` ({directions.get('1h', 'FLAT')})\n"
    signal_msg += f"• 30m: `{get_state_text(states.get('30m'))}` ({directions.get('30m', 'FLAT')})\n"
    signal_msg += f"• 15m: `{get_state_text(states.get('15m'))}`\n\n"
    
    signal_msg += f"{risk_emoji} **Риск:** `{risk}`\n\n"
    
    signal_msg += f"{direction_emoji} **{action}**\n"
    
    if zone_text:
        signal_msg += zone_text
    
    if position_text:
        signal_msg += position_text
    
    if candle_text:
        signal_msg += candle_text
    
    signal_msg += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    return signal_msg
