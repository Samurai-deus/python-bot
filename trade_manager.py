"""
Управление демо-сделками: открытие, отслеживание, закрытие
"""
from datetime import datetime, UTC
from typing import Optional, Dict, List
from database import get_open_trades as db_get_open_trades, close_trade as db_close_trade


def get_open_trades() -> List[Dict]:
    """
    Получает список всех открытых сделок из SQLite.
    
    Returns:
        list: Список словарей с данными открытых сделок
    """
    return db_get_open_trades()


def check_trades(symbol: str, current_price: float) -> List[Dict]:
    """
    Проверяет открытые сделки для символа и закрывает их при достижении TP/SL.
    
    Args:
        symbol: Торговая пара
        current_price: Текущая цена
    
    Returns:
        list: Список закрытых сделок с результатами
    """
    open_trades = get_open_trades()
    closed_trades = []
    
    for trade in open_trades:
        if trade["symbol"] != symbol:
            continue
        
        should_close = False
        close_reason = ""
        pnl = 0.0
        
        # Получаем размер позиции для расчета PnL
        position_size = trade.get("position_size")
        if not position_size:
            # Если размер позиции не указан, используем минимальный размер для расчета
            from capital import MIN_POSITION_SIZE
            position_size = MIN_POSITION_SIZE
        
        if trade["side"] == "LONG":
            # Проверяем стоп-лосс
            if current_price <= trade["stop"]:
                should_close = True
                close_reason = "STOP_LOSS"
                # PnL в USDT = (цена_закрытия - цена_входа) / цена_входа * размер_позиции
                price_change_pct = (current_price - trade["entry"]) / trade["entry"]
                pnl = price_change_pct * position_size
            # Проверяем тейк-профит
            elif current_price >= trade["target"]:
                should_close = True
                close_reason = "TAKE_PROFIT"
                # PnL в USDT = (цена_закрытия - цена_входа) / цена_входа * размер_позиции
                price_change_pct = (current_price - trade["entry"]) / trade["entry"]
                pnl = price_change_pct * position_size
        else:  # SHORT
            # Проверяем стоп-лосс
            if current_price >= trade["stop"]:
                should_close = True
                close_reason = "STOP_LOSS"
                # PnL в USDT = (цена_входа - цена_закрытия) / цена_входа * размер_позиции
                price_change_pct = (trade["entry"] - current_price) / trade["entry"]
                pnl = price_change_pct * position_size
            # Проверяем тейк-профит
            elif current_price <= trade["target"]:
                should_close = True
                close_reason = "TAKE_PROFIT"
                # PnL в USDT = (цена_входа - цена_закрытия) / цена_входа * размер_позиции
                price_change_pct = (trade["entry"] - current_price) / trade["entry"]
                pnl = price_change_pct * position_size
        
        if should_close:
            # Закрываем сделку в SQLite
            db_close_trade(
                trade["id"],
                current_price,
                close_reason,
                pnl
            )
            closed_trades.append({
                **trade,
                "close_price": current_price,
                "close_reason": close_reason,
                "pnl": pnl,
            })
    
    return closed_trades

