"""
Генерация отчетов по закрытым сделкам
"""
from telegram_bot import send_message
from capital import get_current_balance


def generate_trade_report(closed_trade: dict):
    """
    Генерирует и отправляет отчет по закрытой сделке.
    
    Args:
        closed_trade: Словарь с данными закрытой сделки
    """
    symbol = closed_trade["symbol"]
    side = closed_trade["side"]
    entry = closed_trade["entry"]
    stop = closed_trade["stop"]
    target = closed_trade["target"]
    close_price = closed_trade["close_price"]
    close_reason = closed_trade["close_reason"]
    pnl = closed_trade["pnl"]
    
    # Рассчитываем R (риск/награда)
    if side == "LONG":
        risk = entry - stop
        reward = target - entry
    else:  # SHORT
        risk = stop - entry
        reward = entry - target
    
    r_ratio = abs(reward / risk) if risk != 0 else 0
    
    # Рассчитываем процент прибыли/убытка от размера позиции
    # PnL уже в USDT, поэтому процент = (PnL / размер_позиции) * 100
    position_size = closed_trade.get("position_size")
    if position_size and position_size > 0:
        pnl_pct = (pnl / position_size) * 100
    else:
        # Если размер позиции не указан, используем процент от цены входа
        # Это менее точно, но лучше чем ничего
        if side == "LONG":
            price_change_pct = ((close_price - entry) / entry) * 100
        else:  # SHORT
            price_change_pct = ((entry - close_price) / entry) * 100
        pnl_pct = price_change_pct
    
    # Текущий баланс
    balance = get_current_balance()
    
    # Формируем отчет
    emoji = "✅" if pnl > 0 else "❌"
    
    report = f"""
{emoji} **СДЕЛКА ЗАКРЫТА**

📊 Символ: {symbol}
📈 Направление: {side}
💰 Вход: {entry:.4f}
🛑 Стоп: {stop:.4f}
🎯 Цель: {target:.4f}
💵 Закрытие: {close_price:.4f}

📉 Причина: {close_reason}
💵 PnL: {pnl:.2f} USDT ({pnl_pct:+.2f}%)
📊 R-ratio: {r_ratio:.2f}

💼 Текущий баланс: {balance:.2f} USDT
"""
    
    send_message(report)

