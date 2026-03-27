"""
Управление капиталом и расчет размера позиции
"""
from database import get_current_balance_from_db, get_total_open_positions_size

# Параметры управления капиталом
INITIAL_BALANCE = 10000.0  # Начальный баланс в USDT
RISK_PERCENT = 2.0  # Риск на сделку (% от баланса) — используется для SL-расчётов
MIN_POSITION_SIZE = 10.0  # Минимальный размер позиции в USDT
MAX_POSITION_SIZE = 1000.0  # Максимальный размер позиции в USDT


def get_current_balance():
    """
    Полный капитал = начальный баланс + PnL закрытых сделок.
    Не учитывает замороженный в открытых позициях капитал.
    Используется для display и PositionSizer (total equity).

    Returns:
        float: Текущий капитал в USDT
    """
    return get_current_balance_from_db(INITIAL_BALANCE)


def get_available_capital() -> float:
    """
    Доступный капитал = полный капитал − заморожен в открытых позициях.
    Уменьшается по мере открытия сделок, растёт при их закрытии.

    Returns:
        float: Доступный капитал в USDT (>= 0)
    """
    equity = get_current_balance_from_db(INITIAL_BALANCE)
    locked = get_total_open_positions_size()
    return max(equity - locked, 0.0)


def position_size(entry_price, stop_price, side="LONG"):
    """
    Рассчитывает размер позиции на основе риска и доступного капитала.

    Args:
        entry_price: Цена входа
        stop_price: Цена стоп-лосса
        side: "LONG" или "SHORT"

    Returns:
        float: Размер позиции в USDT (0.0 если доступного капитала нет)
    """
    balance = get_current_balance()
    available = get_available_capital()

    if available < MIN_POSITION_SIZE:
        return 0.0  # Нет доступного капитала

    risk_amount = balance * (RISK_PERCENT / 100.0)

    if side == "LONG":
        risk_per_unit = abs(entry_price - stop_price)
    else:  # SHORT
        risk_per_unit = abs(stop_price - entry_price)

    if risk_per_unit == 0:
        return min(MIN_POSITION_SIZE, available)

    # Размер позиции = риск / риск на единицу
    position_usd = risk_amount / risk_per_unit * entry_price

    # Ограничиваем: не больше MAX_POSITION_SIZE и не больше доступного капитала
    max_allowed = min(MAX_POSITION_SIZE, available)
    position_usd = max(MIN_POSITION_SIZE, min(position_usd, max_allowed))

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
