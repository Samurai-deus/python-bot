"""
Управление капиталом и расчет размера позиции
"""
from database import get_current_balance_from_db

# Параметры управления капиталом
INITIAL_BALANCE = 10000.0  # Начальный баланс в USDT
RISK_PERCENT = 2.0  # Риск на сделку (% от баланса)
MIN_POSITION_SIZE = 10.0  # Минимальный размер позиции в USDT
MAX_POSITION_SIZE = 1000.0  # Максимальный размер позиции в USDT


def get_current_balance():
    """
    Рассчитывает текущий баланс на основе закрытых сделок из SQLite.
    
    Returns:
        float: Текущий баланс в USDT
    """
    return get_current_balance_from_db(INITIAL_BALANCE)


def position_size(entry_price, stop_price, side="LONG"):
    """
    Рассчитывает размер позиции на основе риска.
    
    Args:
        entry_price: Цена входа
        stop_price: Цена стоп-лосса
        side: "LONG" или "SHORT"
    
    Returns:
        float: Размер позиции в USDT
    """
    balance = get_current_balance()
    risk_amount = balance * (RISK_PERCENT / 100.0)
    
    if side == "LONG":
        risk_per_unit = abs(entry_price - stop_price)
    else:  # SHORT
        risk_per_unit = abs(stop_price - entry_price)
    
    if risk_per_unit == 0:
        return MIN_POSITION_SIZE
    
    # Размер позиции = риск / риск на единицу
    position_usd = risk_amount / risk_per_unit * entry_price
    
    # Ограничиваем размер позиции
    position_usd = max(MIN_POSITION_SIZE, min(position_usd, MAX_POSITION_SIZE))
    
    return round(position_usd, 2)


def calculate_quantity(position_usd, entry_price):
    """
    Рассчитывает количество контрактов/монет для позиции.
    
    Args:
        position_usd: Размер позиции в USDT
        entry_price: Цена входа
    
    Returns:
        float: Количество контрактов/монет
    """
    if entry_price == 0:
        return 0.0
    return round(position_usd / entry_price, 8)

