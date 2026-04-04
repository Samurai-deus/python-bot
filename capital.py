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
        return 0.0  # zero stop distance = infinite risk, skip trade

    # Размер позиции = риск / риск на единицу
    position_usd = risk_amount / risk_per_unit * entry_price

    # Ограничиваем: не больше MAX_POSITION_SIZE и не больше доступного капитала
    max_allowed = min(MAX_POSITION_SIZE, available)
    position_usd = max(MIN_POSITION_SIZE, min(position_usd, max_allowed))

    return round(position_usd, 2)


def get_rolling_performance(strategy_name: str = None, lookback: int = 50) -> dict:
    """
    Получает win rate и avg win/loss из последних закрытых сделок.
    Если strategy_name указан — только для этой стратегии.

    Returns:
        {"win_rate": float, "avg_win": float, "avg_loss": float}
    """
    from database import get_db_connection, _q
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        if strategy_name and strategy_name != "legacy":
            cursor.execute(
                _q("SELECT pnl FROM trades WHERE status = 'CLOSED' AND strategy_name = ? ORDER BY id DESC LIMIT ?"),
                (strategy_name, lookback),
            )
        else:
            cursor.execute(
                _q("SELECT pnl FROM trades WHERE status = 'CLOSED' ORDER BY id DESC LIMIT ?"),
                (lookback,),
            )
        rows = cursor.fetchall()
    finally:
        conn.close()

    if len(rows) < 10:
        return {"win_rate": 0.5, "avg_win": 10.0, "avg_loss": 5.0}

    pnls = [float(r["pnl"]) for r in rows if r["pnl"] is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    win_rate = len(wins) / len(pnls) if pnls else 0.5
    avg_win = sum(wins) / len(wins) if wins else 10.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 5.0

    return {"win_rate": win_rate, "avg_win": avg_win, "avg_loss": avg_loss}


def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float,
                   safety_factor: float = 0.25) -> float:
    """
    Quarter-Kelly sizing на основе скользящей статистики.
    Возвращает долю капитала для риска (0.005 .. 0.05).
    """
    if avg_loss == 0 or win_rate <= 0:
        return 0.005

    b = avg_win / avg_loss  # win/loss ratio
    p = win_rate
    q = 1.0 - p

    kelly = (b * p - q) / b

    if kelly <= 0:
        return 0.005  # минимум даже при отрицательном ожидании

    adjusted = kelly * safety_factor
    return max(0.005, min(0.05, adjusted))


def get_peak_balance() -> float:
    """Возвращает максимальный баланс (peak equity) для drawdown расчёта.

    Computes the running maximum of cumulative PnL across all closed trades,
    not just the current balance.  This ensures the drawdown breaker fires
    correctly when equity drops from its historical peak.
    """
    from database import get_db_connection, _q
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # Window function works in both PostgreSQL and SQLite 3.25+
        cursor.execute(_q(
            "SELECT COALESCE(MAX(cum), 0) AS peak_pnl FROM ("
            "  SELECT SUM(pnl) OVER (ORDER BY id) AS cum"
            "  FROM trades WHERE status = 'CLOSED'"
            ") sub"
        ))
        row = cursor.fetchone()
        peak_pnl = float(row["peak_pnl"]) if row else 0.0
    finally:
        conn.close()
    return INITIAL_BALANCE + peak_pnl


def current_drawdown_pct() -> float:
    """Текущий drawdown в % от пикового баланса."""
    peak = get_peak_balance()
    current = get_current_balance()
    if peak <= 0:
        return 0.0
    return max(0.0, (peak - current) / peak * 100)


DRAWDOWN_CUTOFF_PCT = 15.0


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
