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
    
    # Уведомление не отправляем — сигнал уже отправлен через Gatekeeper
