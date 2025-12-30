from datetime import datetime, UTC
from telegram_bot import send_message
from capital import get_current_balance
from database import add_trade

def log_demo_trade(symbol, side, entry, stop, target, position_size=None, leverage=None):
    """
    Логирует демо-сделку в SQLite базу данных и отправляет уведомление.
    
    Args:
        symbol: Торговая пара
        side: "LONG" или "SHORT"
        entry: Цена входа
        stop: Цена стоп-лосса
        target: Цена тейк-профита
        position_size: Размер позиции в USDT (опционально)
        leverage: Плечо (опционально)
    """
    # Добавляем сделку в SQLite
    trade_id = add_trade(symbol, side, entry, stop, target, position_size, leverage)
    
    # Отправляем уведомление об открытии сделки
    try:
        # Рассчитываем R (риск/награда)
        if side == "LONG":
            risk = entry - stop
            reward = target - entry
        else:  # SHORT
            risk = stop - entry
            reward = entry - target
        
        r_ratio = abs(reward / risk) if risk != 0 else 0
        
        # Текущий баланс
        balance = get_current_balance()
        
        # Формируем отчет
        side_emoji = "🟢" if side == "LONG" else "🔴"
        
        report = f"""
✅ **СДЕЛКА ОТКРЫТА**

📊 Символ: {symbol}
{side_emoji} Направление: {side}
💰 Вход: {entry:.4f}
🛑 Стоп: {stop:.4f}
🎯 Цель: {target:.4f}
📈 R:R: {r_ratio:.2f}
"""
        if position_size:
            report += f"💼 Размер: {position_size:.2f} USDT\n"
        if leverage:
            report += f"⚡ Плечо: {leverage:.1f}x\n"
        
        report += f"\n💼 Текущий баланс: {balance:.2f} USDT"""
        
        send_message(report)
    except Exception as e:
        # Не блокируем процесс, если не удалось отправить уведомление
        import logging
        logging.warning("Не удалось отправить уведомление об открытии сделки %s: %s", symbol, e)
