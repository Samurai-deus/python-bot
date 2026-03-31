from datetime import datetime, UTC
from telegram_bot import send_message
from capital import get_current_balance
from database import add_trade

def log_demo_trade(symbol, side, entry, stop, target, position_size=None, leverage=None, strategy_name=None):
    """
    Логирует демо-сделку в базу данных и отправляет уведомление.

    Args:
        symbol: Торговая пара
        side: "LONG" или "SHORT"
        entry: Цена входа
        stop: Цена стоп-лосса
        target: Цена тейк-профита
        position_size: Размер позиции в USDT (опционально)
        leverage: Плечо (опционально)
        strategy_name: Название стратегии (опционально)
    """
    # Добавляем сделку в базу данных
    trade_id = add_trade(symbol, side, entry, stop, target, position_size, leverage, strategy_name)
    
    # Уведомление не отправляем — сигнал уже отправлен через Gatekeeper
