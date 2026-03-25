"""
Модуль для алертов о резких движениях без видимой причины
"""
import logging
from datetime import datetime, UTC
from typing import Dict, List, Optional
from volatility_filter import check_price_spike
from indicators import atr, rsi, macd
from telegram_bot import send_message

logger = logging.getLogger(__name__)


# Кэш последних алертов (чтобы не спамить)
_last_alerts = {}


def analyze_spike_with_context(symbol: str, candles: List, timeframe: str = "15m") -> Optional[Dict]:
    """
    Анализирует резкое движение и определяет, есть ли видимая причина.
    
    Returns:
        dict: {
            "has_spike": bool,
            "spike_direction": "UP"/"DOWN",
            "spike_pct": float,
            "has_visible_reason": bool,
            "possible_reasons": List[str],
            "should_alert": bool
        }
    """
    if len(candles) < 5:
        return None
    
    # Проверяем спайк
    spike_info = check_price_spike(candles, threshold_pct=1.5)
    
    if not spike_info.get("has_spike", False):
        return None
    
    # Анализируем контекст для определения причины
    possible_reasons = []
    has_visible_reason = False
    
    try:
        # 1. Проверяем RSI - перекупленность/перепроданность
        rsi_value = rsi(candles, period=14)
        if spike_info["spike_direction"] == "UP" and rsi_value > 70:
            possible_reasons.append("RSI перекупленность")
            has_visible_reason = True
        elif spike_info["spike_direction"] == "DOWN" and rsi_value < 30:
            possible_reasons.append("RSI перепроданность")
            has_visible_reason = True
        
        # 2. Проверяем MACD - дивергенция или сильный сигнал
        macd_data = macd(candles)
        macd_trend = macd_data.get("trend", "NEUTRAL")
        if macd_trend != "NEUTRAL":
            possible_reasons.append(f"MACD {macd_trend}")
            has_visible_reason = True
        
        # 3. Проверяем волатильность (ATR)
        atr_value = atr(candles)
        current_price = float(candles[-1][4])
        atr_pct = (atr_value / current_price) * 100 if current_price > 0 else 0
        
        # Если волатильность была низкой, а потом резкий скачок - подозрительно
        if atr_pct < 1.0:
            possible_reasons.append("Низкая волатильность перед движением")
            # Это может быть признаком неожиданного движения
        
        # 4. Проверяем объем (используем диапазон как прокси)
        recent_ranges = []
        for i in range(max(0, len(candles) - 5), len(candles)):
            high = float(candles[i][2])
            low = float(candles[i][3])
            recent_ranges.append(high - low)
        
        if len(recent_ranges) >= 2:
            current_range = recent_ranges[-1]
            avg_range = sum(recent_ranges[:-1]) / len(recent_ranges[:-1])
            
            # Если движение без увеличения диапазона - подозрительно
            if current_range < avg_range * 1.2:
                possible_reasons.append("Движение без увеличения объема")
                has_visible_reason = False  # Это признак неожиданного движения
        
    except Exception as e:
        pass
    
    # Определяем, нужно ли отправлять алерт
    should_alert = False
    if not has_visible_reason and spike_info.get("spike_pct", 0) > 2.0:
        # Резкое движение без видимой причины - отправляем алерт
        should_alert = True
    
    return {
        "has_spike": True,
        "spike_direction": spike_info["spike_direction"],
        "spike_pct": spike_info["spike_pct"],
        "has_visible_reason": has_visible_reason,
        "possible_reasons": possible_reasons,
        "should_alert": should_alert,
        "timeframe": timeframe
    }


def send_spike_alert(symbol: str, spike_analysis: Dict):
    """
    Отправляет алерт о резком движении в Telegram.
    """
    global _last_alerts
    
    # Проверяем, не отправляли ли мы недавно алерт для этого символа
    alert_key = f"{symbol}_{spike_analysis['timeframe']}"
    last_alert_time = _last_alerts.get(alert_key, 0)
    current_time = datetime.now(UTC).timestamp()
    
    # Не отправляем алерт чаще чем раз в 30 минут
    if current_time - last_alert_time < 1800:
        return
    
    spike_pct = spike_analysis.get("spike_pct", 0)
    direction = spike_analysis.get("spike_direction", "NONE")
    reasons = spike_analysis.get("possible_reasons", [])
    has_reason = spike_analysis.get("has_visible_reason", False)
    
    emoji = "📈" if direction == "UP" else "📉"
    
    message = f"{emoji} **РЕЗКОЕ ДВИЖЕНИЕ**\n\n"
    message += f"📊 Символ: {symbol}\n"
    message += f"📈 Направление: {direction}\n"
    message += f"💹 Движение: {spike_pct:.2f}%\n"
    message += f"⏱ Таймфрейм: {spike_analysis.get('timeframe', '15m')}\n\n"
    
    if has_reason and reasons:
        message += f"✅ Возможные причины:\n"
        for reason in reasons:
            message += f"• {reason}\n"
    else:
        message += f"⚠️ **БЕЗ ВИДИМОЙ ПРИЧИНЫ**\n"
        message += f"Возможные причины:\n"
        if reasons:
            for reason in reasons:
                message += f"• {reason}\n"
        else:
            message += "• Неожиданное движение\n"
        message += "\n🔍 Рекомендуется проверить новости или крупные ордера"
    
    message += f"\n⏰ {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}"
    
    try:
        send_message(message)
        _last_alerts[alert_key] = current_time
    except Exception as e:
        logger.error("Alert send error: %s", e)


def check_all_symbols_for_spikes(symbols: List[str], candles_map: Dict[str, Dict[str, List]]):
    """
    Проверяет все символы на резкие движения и отправляет алерты.
    """
    for symbol in symbols:
        if symbol not in candles_map:
            continue
        
        # Проверяем на разных таймфреймах
        for timeframe in ["5m", "15m", "30m"]:
            if timeframe not in candles_map[symbol]:
                continue
            
            candles = candles_map[symbol][timeframe]
            if len(candles) < 5:
                continue
            
            spike_analysis = analyze_spike_with_context(symbol, candles, timeframe)
            
            if spike_analysis and spike_analysis.get("should_alert", False):
                send_spike_alert(symbol, spike_analysis)

