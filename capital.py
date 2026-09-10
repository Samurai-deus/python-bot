"""
Управление капиталом и расчёт размера позиции.

Константы (стартовый баланс, риск на сделку, пределы размера) живут в config.py —
и только там. До 10.09.2026 этот модуль держал собственные копии тех же констант,
и разные модули читали разные: gatekeeper и replay_engine — отсюда,
position_sizer, bot_statistics и risk_exposure_brain — из config.

Откуда берётся баланс (аудит 10.09.2026, блокер B-4):
  • режимы с реальными ордерами (TESTNET, LIVE) — кошелёк биржи. Раньше и там
    считалось «стартовый баланс + PnL бумажной таблицы», то есть при реальном
    кошельке в 300 $ размер позиции и все лимиты риска считались от 10 000 $;
  • бумажные режимы (DRY_RUN, PAPER_TRADING) — стартовый бумажный баланс плюс PnL
    закрытых бумажных сделок.

Если кошелёк недоступен — баланс 0, а не запасное значение: не можем узнать,
сколько денег, значит, не открываем позиций.
"""
import logging
import threading
import time

from config import INITIAL_BALANCE, MAX_POSITION_SIZE, MIN_POSITION_SIZE, RISK_PERCENT  # noqa: F401  (реэкспорт)
from database import get_current_balance_from_db, get_total_open_positions_size

logger = logging.getLogger(__name__)

# Кошелёк спрашиваем не чаще раза в 30 секунд: размер считается на каждый сигнал,
# а сигналов по 28 символам за цикл бывает несколько.
_WALLET_CACHE_TTL = 30.0
_wallet_cache = {"at": 0.0, "value": None}
_wallet_lock = threading.Lock()


def _real_orders_mode() -> bool:
    from trading_mode import sends_real_orders
    return sends_real_orders()


def _get_wallet_client():
    from exchange.bybit_client import get_bybit_client
    return get_bybit_client()


def _clear_wallet_cache() -> None:
    with _wallet_lock:
        _wallet_cache.update(at=0.0, value=None)


def _wallet_snapshot():
    """
    (equity, available) из кошелька или None, если спросить не удалось.

    available — walletBalance из ответа биржи: в нём учтена и маржа открытых
    позиций. Для точного свободного остатка нужен totalAvailableBalance уровня
    счёта — это отдельная задача (аудит, находка L4).
    """
    now = time.time()
    with _wallet_lock:
        if _wallet_cache["value"] is not None and now - _wallet_cache["at"] < _WALLET_CACHE_TTL:
            return _wallet_cache["value"]
    try:
        info = _get_wallet_client().get_wallet_balance()
        value = (float(info.total_equity), float(info.available_balance))
    except Exception as exc:
        logger.error("capital: баланс кошелька недоступен — позиции открываться не будут: %s", exc)
        return None
    with _wallet_lock:
        _wallet_cache.update(at=now, value=value)
    return value


def get_current_balance():
    """
    Полный капитал в USDT: equity кошелька в реальных режимах, иначе бумажный
    стартовый баланс плюс PnL закрытых бумажных сделок. Замороженное в открытых
    позициях не вычитается — для этого get_available_capital().
    """
    if _real_orders_mode():
        snap = _wallet_snapshot()
        return snap[0] if snap else 0.0
    return get_current_balance_from_db(INITIAL_BALANCE)


def _real_mode_key() -> str:
    from trading_mode import get_trading_mode
    return get_trading_mode().value


def _real_baseline():
    """
    База капитала реального режима: из базы данных, а при первом запуске режима —
    текущая equity кошелька, записанная туда же. None — кошелёк недоступен.
    """
    from database import get_capital_baseline, save_capital_baseline
    mode = _real_mode_key()
    stored = get_capital_baseline(mode)
    if stored:
        return stored
    snap = _wallet_snapshot()
    if not snap or snap[0] <= 0:
        return None
    save_capital_baseline(mode, snap[0], snap[0])
    logger.warning("capital: база капитала режима %s записана — %.2f USDT (equity кошелька)", mode, snap[0])
    return {"initial": snap[0], "peak": snap[0]}


def get_initial_balance() -> float:
    """
    Стартовый капитал, от которого считаются убыток (Risk Core), просадка и PnL
    в отчётах. Бумажные режимы — PAPER_INITIAL_BALANCE_USDT. Реальные — equity
    кошелька при первом запуске режима, записанная в базу отдельно для TESTNET и
    LIVE. До 10.09.2026 и в реальных режимах бралось бумажное значение: при
    кошельке в 1000 $ «убыток» и «просадка» считались от 100 $ и ничего не значили.

    Пополнение или вывод средств эту базу не меняют — после них её нужно
    переустановить (таблица capital_baseline).
    """
    if not _real_orders_mode():
        return INITIAL_BALANCE
    baseline = _real_baseline()
    return baseline["initial"] if baseline else 0.0


def get_available_capital() -> float:
    """
    Капитал, свободный для новой позиции (>= 0).

    Бумажный режим: полный капитал минус номинал открытых позиций. Позиции
    считаются без плеча — номинал целиком занимает капитал. Это консервативно:
    на реальном счёте с плечом свободной маржи было бы больше.
    """
    if _real_orders_mode():
        snap = _wallet_snapshot()
        return max(snap[1], 0.0) if snap else 0.0
    equity = get_current_balance_from_db(INITIAL_BALANCE)
    locked = get_total_open_positions_size()
    return max(equity - locked, 0.0)


def position_size(entry_price, stop_price, side="LONG"):
    """
    Размер позиции (номинал в USDT) из риска на сделку.

    Риск = доступный капитал × RISK_PERCENT; номинал = риск / расстояние до стопа,
    то есть при срабатывании стопа теряется ровно риск. Номинал ограничен
    MAX_POSITION_SIZE и доступным капиталом.

    Returns:
        float: номинал в USDT, 0.0 — если позицию открывать не нужно
    """
    available = get_available_capital()
    if available < MIN_POSITION_SIZE:
        return 0.0

    risk_amount = available * (RISK_PERCENT / 100.0)

    if side == "LONG":
        risk_per_unit = abs(entry_price - stop_price)
    else:  # SHORT
        risk_per_unit = abs(stop_price - entry_price)

    if risk_per_unit == 0:
        return 0.0  # нулевое расстояние до стопа = бесконечный риск

    position_usd = risk_amount / risk_per_unit * entry_price

    # Пределы — те же, которыми позицию затем проверит Risk Core: не больше
    # max_single_position_pct баланса и не больше остатка до max_aggregate_exposure_pct.
    # Раньше предел был числом MAX_POSITION_SIZE = 1000 $, которое совпадало с
    # «10 % баланса» только при прежнем стартовом балансе 10 000 $. При 100 $ оно
    # перестало ограничивать, размер упирался во весь капитал, и Risk Core отклонял
    # каждый сигнал: одна позиция 100 % > 10 % и экспозиция 100 % > 50 % → LOCKED.
    # Первые циклы на проде 10.09.2026: 28 сигналов, одобрено 0.
    from core.risk_core import get_risk_core
    limits = get_risk_core().config
    equity = get_current_balance()
    single_cap = equity * limits.max_single_position_pct / 100.0
    aggregate_room = equity * limits.max_aggregate_exposure_pct / 100.0 - get_total_open_positions_size()
    position_usd = min(position_usd, MAX_POSITION_SIZE, available, single_cap, aggregate_room)

    # Меньше минимума — не открываем, а не раздуваем до минимума. Раньше здесь
    # стоял max(MIN_POSITION_SIZE, ...): позиция, которую риск разрешал на 3 $,
    # открывалась на 10 $ — риск на сделке превышался втрое.
    if position_usd < MIN_POSITION_SIZE:
        return 0.0
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
        return {"win_rate": 0.4, "avg_win": 5.0, "avg_loss": 5.0}

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
    Возвращает долю капитала для риска (0.0 .. 0.05).
    Returns 0.0 on negative expectancy (don't trade).
    """
    if avg_loss == 0 or win_rate <= 0:
        return 0.0

    b = avg_win / avg_loss  # win/loss ratio
    p = win_rate
    q = 1.0 - p

    kelly = (b * p - q) / b

    if kelly <= 0:
        return 0.0  # negative expectancy = don't trade

    adjusted = kelly * safety_factor
    return max(0.005, min(0.05, adjusted))


def get_peak_balance() -> float:
    """Возвращает максимальный баланс (peak equity) для drawdown расчёта.

    Computes the running maximum of cumulative PnL across all closed trades,
    not just the current balance.  This ensures the drawdown breaker fires
    correctly when equity drops from its historical peak.
    """
    if _real_orders_mode():
        # Реальные режимы: пик — наибольшая виденная equity кошелька (в базе).
        # Раньше пик считался как бумажные 100 $ плюс PnL бумажной таблицы.
        baseline = _real_baseline()
        if not baseline:
            return 0.0
        current = get_current_balance()
        if current > baseline["peak"]:
            from database import save_capital_baseline
            save_capital_baseline(_real_mode_key(), baseline["initial"], current)
            return current
        return baseline["peak"]

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
