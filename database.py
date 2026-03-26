"""
SQLite база данных для торговых данных

Используется для:
- Статистики
- Агрегаций
- Дневника сделок

CSV остается для логов (signals_log.csv)
"""
import sqlite3
import os
import threading
from datetime import datetime, UTC, timedelta
from typing import List, Dict, Optional
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

# Путь к базе данных
DB_PATH = os.environ.get("DB_PATH", "market_bot.db")

# ========== FAULT INJECTION (для тестирования устойчивости) ==========

FAULT_INJECT_STORAGE_FAILURE = os.environ.get("FAULT_INJECT_STORAGE_FAILURE", "false").lower() == "true"

# ========== THREAD-LOCAL CONNECTION POOL ==========
# Each thread gets its own persistent connection (check_same_thread=True).
# _PooledConnection wraps the real connection: .close() rolls back any open
# transaction but keeps the underlying connection alive for reuse.

_thread_local = threading.local()


class _PooledConnection:
    """Thread-local pooled SQLite connection. .close() rolls back instead of closing."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def close(self) -> None:
        """Roll back any uncommitted transaction; keep connection alive for reuse."""
        try:
            self._conn.rollback()
        except sqlite3.Error as e:
            logger.warning("_PooledConnection.close: rollback failed: %s", e)


def _get_raw_connection() -> sqlite3.Connection:
    conn = getattr(_thread_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _init_database(conn)
        _thread_local.conn = conn
    return conn


def get_db_connection() -> _PooledConnection:
    """
    Returns a thread-local pooled connection.

    Each thread reuses one persistent connection; check_same_thread=True
    enforces this at the SQLite level.  WAL mode enables concurrent readers.
    Callers must still call conn.commit() after writes and conn.close()
    after each logical operation — close() rolls back any open transaction
    but keeps the underlying connection alive.
    """
    return _PooledConnection(_get_raw_connection())


def _init_database(conn: sqlite3.Connection):
    """
    Инициализирует схему базы данных.
    """
    cursor = conn.cursor()
    
    # Таблица сделок
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            entry REAL NOT NULL,
            stop REAL NOT NULL,
            target REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            position_size REAL,
            leverage REAL,
            close_price REAL,
            close_reason TEXT,
            pnl REAL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Индексы для быстрого поиска
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp)
    """)
    
    # Таблица для snapshot SystemState
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_state_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            snapshot_data TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Индекс для быстрого поиска последнего snapshot
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON system_state_snapshots(timestamp DESC)
    """)

    # ========== Phase 2: Exchange order/position tables ==========

    # Ордера — запись каждого ордера размещённого на бирже (или DRY_RUN)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,           -- Bybit order ID (или "dry_*")
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,               -- "LONG" | "SHORT"
            order_type TEXT NOT NULL,         -- "Market" | "Limit"
            qty REAL NOT NULL,
            entry_price REAL,                 -- NULL для Market ордеров
            stop_loss REAL NOT NULL,
            take_profit REAL,
            status TEXT NOT NULL DEFAULT 'CREATED',  -- CREATED | FILLED | CANCELLED | FAILED
            dry_run INTEGER NOT NULL DEFAULT 1,       -- 1 = симуляция, 0 = реальный
            error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_order_id ON orders(order_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")

    # Позиции — жизненный цикл позиции от открытия до закрытия
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,           -- Ссылка на orders.order_id
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,               -- "LONG" | "SHORT"
            qty REAL NOT NULL,
            entry_price REAL NOT NULL,
            stop_loss REAL NOT NULL,
            take_profit REAL,
            status TEXT NOT NULL DEFAULT 'OPEN',  -- OPEN | CLOSED
            close_price REAL,
            close_reason TEXT,                -- "SL" | "TP" | "MANUAL" | "EXPIRED"
            realised_pnl REAL,
            opened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            closed_at TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status)")

    # Ежедневные P&L снимки для аналитики (Phase 3)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pnl_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,        -- "2026-03-13"
            realised_pnl REAL NOT NULL DEFAULT 0.0,
            trades_count INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            balance_end REAL NOT NULL DEFAULT 0.0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pnl_history_date ON pnl_history(date)")

    # Детальные записи P&L по каждой закрытой сделке (Phase 3)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pnl_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            closed_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL NOT NULL,
            quantity REAL NOT NULL,
            gross_pnl REAL NOT NULL,
            commission REAL NOT NULL DEFAULT 0.0,
            net_pnl REAL NOT NULL,
            market_regime TEXT,
            hold_duration_seconds INTEGER,
            signal_confidence REAL,
            signal_entropy REAL,
            balance_after REAL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pnl_records_closed_at ON pnl_records(closed_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pnl_records_symbol ON pnl_records(symbol)")

    # Настройки пользователя (Phase 5)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            id INTEGER PRIMARY KEY,
            setting_key TEXT NOT NULL UNIQUE,
            setting_value TEXT NOT NULL,
            data_type TEXT NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Исходы сигналов — для системы обучения (Phase 5+)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signal_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_ts TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry REAL NOT NULL,
            tp REAL NOT NULL,
            sl REAL NOT NULL,
            confidence REAL,
            state_15m TEXT,
            checked_at TEXT NOT NULL,
            outcome TEXT NOT NULL,
            candles_checked INTEGER NOT NULL DEFAULT 0,
            max_favorable_pct REAL,
            max_adverse_pct REAL,
            UNIQUE(signal_ts, symbol)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_signal_outcomes_symbol ON signal_outcomes(symbol)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_signal_outcomes_outcome ON signal_outcomes(outcome)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_signal_outcomes_ts ON signal_outcomes(signal_ts)")

    conn.commit()


# ============================================================================
# TRADES (Сделки)
# ============================================================================

def add_trade(symbol: str, side: str, entry: float, stop: float, target: float,
              position_size: Optional[float] = None, leverage: Optional[float] = None) -> int:
    """
    Добавляет новую сделку в базу данных.
    
    Args:
        symbol: Торговая пара
        side: "LONG" или "SHORT"
        entry: Цена входа
        stop: Цена стоп-лосса
        target: Цена тейк-профита
        position_size: Размер позиции в USDT
        leverage: Плечо
    
    Returns:
        int: ID созданной сделки
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        timestamp = datetime.now(UTC).isoformat()

        cursor.execute("""
            INSERT INTO trades (timestamp, symbol, side, entry, stop, target, status, position_size, leverage)
            VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)
        """, (timestamp, symbol, side, entry, stop, target, position_size, leverage))

        trade_id = cursor.lastrowid
        conn.commit()
    finally:
        conn.close()

    logger.info(f"Добавлена сделка #{trade_id}: {symbol} {side} @ {entry}")
    return trade_id


def get_open_trades() -> List[Dict]:
    """
    Получает список всех открытых сделок.
    
    Returns:
        list: Список словарей с данными открытых сделок
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM trades WHERE status = 'OPEN' ORDER BY timestamp DESC
        """)

        rows = cursor.fetchall()
    finally:
        conn.close()

    trades = []
    for row in rows:
        trades.append({
            "id": row["id"],
            "timestamp": row["timestamp"],
            "symbol": row["symbol"],
            "side": row["side"],
            "entry": row["entry"],
            "stop": row["stop"],
            "target": row["target"],
            "status": row["status"],
            "position_size": row["position_size"],
            "leverage": row["leverage"],
        })

    return trades


def close_trade(trade_id: int, close_price: float, close_reason: str, pnl: float):
    """
    Закрывает сделку в базе данных.
    
    Args:
        trade_id: ID сделки
        close_price: Цена закрытия
        close_reason: Причина закрытия (STOP_LOSS/TAKE_PROFIT)
        pnl: Прибыль/убыток
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        updated_at = datetime.now(UTC).isoformat()

        cursor.execute("""
            UPDATE trades
            SET status = 'CLOSED', close_price = ?, close_reason = ?, pnl = ?, updated_at = ?
            WHERE id = ?
        """, (close_price, close_reason, pnl, updated_at, trade_id))

        conn.commit()
    finally:
        conn.close()

    logger.info(f"Закрыта сделка #{trade_id}: PnL={pnl:.2f} USDT, причина={close_reason}")


def get_trades_by_symbol(symbol: str, status: Optional[str] = None) -> List[Dict]:
    """
    Получает сделки по символу.
    
    Args:
        symbol: Торговая пара
        status: Статус сделки (OPEN/CLOSED) или None для всех
    
    Returns:
        list: Список сделок
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        if status:
            cursor.execute("""
                SELECT * FROM trades WHERE symbol = ? AND status = ? ORDER BY timestamp DESC
            """, (symbol, status))
        else:
            cursor.execute("""
                SELECT * FROM trades WHERE symbol = ? ORDER BY timestamp DESC
            """, (symbol,))

        rows = cursor.fetchall()
    finally:
        conn.close()

    trades = []
    for row in rows:
        trades.append(dict(row))

    return trades


def get_trades_statistics(days: int = 1) -> Dict:
    """
    Получает статистику по сделкам за последние N дней.
    
    Args:
        days: Количество дней для анализа
    
    Returns:
        dict: Статистика по сделкам
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        cutoff_time = (datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) -
                       timedelta(days=days)).isoformat()

        # Закрытые сделки за период
        cursor.execute("""
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losing_trades,
                SUM(pnl) as total_pnl,
                AVG(pnl) as avg_pnl,
                MAX(pnl) as best_pnl,
                MIN(pnl) as worst_pnl
            FROM trades
            WHERE status = 'CLOSED' AND timestamp >= ?
        """, (cutoff_time,))

        stats_row = cursor.fetchone()

        # Открытые сделки
        cursor.execute("""
            SELECT COUNT(*) as open_trades FROM trades WHERE status = 'OPEN'
        """)
        open_trades = cursor.fetchone()["open_trades"]

        # Статистика по символам
        cursor.execute("""
            SELECT
                symbol,
                COUNT(*) as trades,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(pnl) as pnl
            FROM trades
            WHERE status = 'CLOSED' AND timestamp >= ?
            GROUP BY symbol
            ORDER BY pnl DESC
        """, (cutoff_time,))

        symbol_stats = {}
        for row in cursor.fetchall():
            symbol_stats[row["symbol"]] = {
                "trades": row["trades"],
                "wins": row["wins"],
                "pnl": row["pnl"]
            }

        # Лучшая и худшая сделки
        cursor.execute("""
            SELECT symbol, side, pnl FROM trades
            WHERE status = 'CLOSED' AND timestamp >= ?
            ORDER BY pnl DESC LIMIT 1
        """, (cutoff_time,))
        best_trade_row = cursor.fetchone()

        cursor.execute("""
            SELECT symbol, side, pnl FROM trades
            WHERE status = 'CLOSED' AND timestamp >= ?
            ORDER BY pnl ASC LIMIT 1
        """, (cutoff_time,))
        worst_trade_row = cursor.fetchone()
    finally:
        conn.close()
    
    total_trades = stats_row["total_trades"] or 0
    winning_trades = stats_row["winning_trades"] or 0
    losing_trades = stats_row["losing_trades"] or 0
    total_pnl = stats_row["total_pnl"] or 0.0
    avg_pnl = stats_row["avg_pnl"] or 0.0
    
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    
    best_trade = None
    if best_trade_row:
        best_trade = {
            "symbol": best_trade_row["symbol"],
            "pnl": best_trade_row["pnl"],
            "side": best_trade_row["side"]
        }
    
    worst_trade = None
    if worst_trade_row:
        worst_trade = {
            "symbol": worst_trade_row["symbol"],
            "pnl": worst_trade_row["pnl"],
            "side": worst_trade_row["side"]
        }
    
    return {
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "total_pnl": total_pnl,
        "avg_pnl_per_trade": avg_pnl,
        "win_rate": win_rate,
        "open_trades": open_trades,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "symbol_stats": symbol_stats,
        # Для совместимости со старым форматом
        "wins": winning_trades,
        "losses": losing_trades
    }


def get_current_balance_from_db(initial_balance: float = 10000.0) -> float:
    """
    Рассчитывает текущий баланс на основе закрытых сделок.
    
    Args:
        initial_balance: Начальный баланс
    
    Returns:
        float: Текущий баланс
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT COALESCE(SUM(pnl), 0) as total_pnl
            FROM trades
            WHERE status = 'CLOSED'
        """)

        row = cursor.fetchone()
        total_pnl = row["total_pnl"] or 0.0
    finally:
        conn.close()

    balance = initial_balance + total_pnl
    return max(balance, 10.0)  # Минимум 10 USDT


def migrate_from_csv(csv_file: str = "demo_trades.csv"):
    """
    Мигрирует данные из CSV в SQLite.
    
    Args:
        csv_file: Путь к CSV файлу
    """
    if not os.path.exists(csv_file):
        logger.info(f"CSV файл {csv_file} не найден, миграция не требуется")
        return
    
    import csv
    
    conn = get_db_connection()
    migrated = 0
    errors = 0

    try:
        cursor = conn.cursor()

        with open(csv_file, "r", encoding="utf-8") as f:
            reader = csv.reader(f)

            # Пропускаем заголовок, если есть
            first_row = next(reader, None)
            if first_row and len(first_row) > 0:
                if first_row[0].lower() in ['timestamp', 'time', 'date']:
                    # Это заголовок, пропускаем
                    pass
                else:
                    # Это не заголовок, возвращаемся к началу
                    f.seek(0)
                    reader = csv.reader(f)

            for row in reader:
                if len(row) < 7:
                    continue

                try:
                    timestamp = row[0]
                    symbol = row[1]
                    side = row[2]
                    entry = float(row[3])
                    stop = float(row[4])
                    target = float(row[5])
                    status = row[6] if len(row) > 6 else "OPEN"

                    position_size = None
                    if len(row) > 7 and row[7]:
                        try:
                            position_size = float(row[7])
                        except (ValueError, IndexError):
                            pass

                    leverage = None
                    if len(row) > 8 and row[8]:
                        try:
                            leverage = float(row[8])
                        except (ValueError, IndexError):
                            pass

                    close_price = None
                    close_reason = None
                    pnl = None

                    if status == "CLOSED" and len(row) >= 11:
                        try:
                            close_price = float(row[9]) if row[9] else None
                            close_reason = row[10] if len(row) > 10 else None
                            pnl = float(row[11]) if len(row) > 11 and row[11] else None
                        except (ValueError, IndexError):
                            pass

                    # Проверяем, не существует ли уже эта сделка
                    cursor.execute("""
                        SELECT id FROM trades WHERE timestamp = ? AND symbol = ? AND status = ?
                    """, (timestamp, symbol, status))

                    if cursor.fetchone():
                        # Сделка уже существует, пропускаем
                        continue

                    # Добавляем сделку
                    cursor.execute("""
                        INSERT INTO trades (
                            timestamp, symbol, side, entry, stop, target, status,
                            position_size, leverage, close_price, close_reason, pnl
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (timestamp, symbol, side, entry, stop, target, status,
                          position_size, leverage, close_price, close_reason, pnl))

                    migrated += 1

                except Exception as e:
                    errors += 1
                    logger.warning(f"Ошибка при миграции строки: {e}")
                    continue

        conn.commit()
        logger.info(f"Миграция завершена: {migrated} сделок мигрировано, {errors} ошибок")

    except Exception as e:
        logger.error(f"Критическая ошибка при миграции: {e}", exc_info=True)
    finally:
        conn.close()


# ============================================================================
# SYSTEM STATE SNAPSHOTS
# ============================================================================

def save_system_state_snapshot(snapshot_data: Dict) -> int:
    """
    Сохраняет снимок SystemState в базу данных.
    
    Args:
        snapshot_data: Данные снимка (из SystemState.create_snapshot())
    
    Returns:
        int: ID сохранённого снимка
    
    Note:
        Fault injection проверяется в SystemStateSnapshotStore.save() - entry point.
        Эта функция вызывается только после проверки fault injection.
    """
    import json
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        timestamp = snapshot_data.get("timestamp", datetime.now(UTC).isoformat())
        snapshot_json = json.dumps(snapshot_data)

        cursor.execute("""
            INSERT INTO system_state_snapshots (timestamp, snapshot_data)
            VALUES (?, ?)
        """, (timestamp, snapshot_json))

        snapshot_id = cursor.lastrowid
        conn.commit()
    finally:
        conn.close()

    logger.info(f"Сохранён snapshot SystemState #{snapshot_id}")
    return snapshot_id


def get_latest_system_state_snapshot() -> Optional[Dict]:
    """
    Получает последний снимок SystemState из базы данных.
    
    Returns:
        dict: Последний снимок или None если нет снимков
    
    Note:
        Fault injection проверяется в SystemStateSnapshotStore.load_latest() - entry point.
        Эта функция вызывается только после проверки fault injection.
    """
    import json
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT snapshot_data FROM system_state_snapshots
            ORDER BY timestamp DESC
            LIMIT 1
        """)

        row = cursor.fetchone()
    finally:
        conn.close()

    if not row:
        return None

    try:
        return json.loads(row["snapshot_data"])
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Ошибка парсинга snapshot: {e}")
        return None


# ============================================================================
# ORDERS (Phase 2)
# ============================================================================

def save_order(
    order_id: str,
    symbol: str,
    side: str,
    order_type: str,
    qty: float,
    stop_loss: float,
    dry_run: bool,
    entry_price: Optional[float] = None,
    take_profit: Optional[float] = None,
    status: str = "CREATED",
    error: Optional[str] = None,
) -> int:
    """Сохранить ордер (реальный или DRY_RUN). Возвращает internal id."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO orders
                (order_id, symbol, side, order_type, qty, entry_price,
                 stop_loss, take_profit, status, dry_run, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (order_id, symbol, side, order_type, qty, entry_price,
              stop_loss, take_profit, status, int(dry_run), error))
        row_id = cursor.lastrowid
        conn.commit()
    finally:
        conn.close()
    logger.info(f"Order saved #{row_id}: {symbol} {side} order_id={order_id} dry_run={dry_run}")
    return row_id


def update_order_status(order_id: str, status: str, error: Optional[str] = None) -> None:
    """Обновить статус ордера."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        updated_at = datetime.now(UTC).isoformat()
        cursor.execute("""
            UPDATE orders SET status = ?, error = ?, updated_at = ?
            WHERE order_id = ?
        """, (status, error, updated_at, order_id))
        conn.commit()
    finally:
        conn.close()


# ============================================================================
# POSITIONS (Phase 2)
# ============================================================================

def open_position(
    order_id: str,
    symbol: str,
    side: str,
    qty: float,
    entry_price: float,
    stop_loss: float,
    take_profit: Optional[float] = None,
) -> int:
    """Записать открытие позиции. Возвращает internal id."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        opened_at = datetime.now(UTC).isoformat()
        cursor.execute("""
            INSERT INTO positions
                (order_id, symbol, side, qty, entry_price, stop_loss, take_profit, status, opened_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
        """, (order_id, symbol, side, qty, entry_price, stop_loss, take_profit, opened_at))
        row_id = cursor.lastrowid
        conn.commit()
    finally:
        conn.close()
    logger.info(f"Position opened #{row_id}: {symbol} {side} entry={entry_price}")
    return row_id


def close_position_by_order_id(
    order_id: str,
    close_price: float,
    close_reason: str,
    realised_pnl: float,
) -> None:
    """Закрыть позицию по order_id."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        closed_at = datetime.now(UTC).isoformat()
        cursor.execute("""
            UPDATE positions
            SET status = 'CLOSED', close_price = ?, close_reason = ?,
                realised_pnl = ?, closed_at = ?
            WHERE order_id = ? AND status = 'OPEN'
        """, (close_price, close_reason, realised_pnl, closed_at, order_id))
        conn.commit()
    finally:
        conn.close()
    logger.info(f"Position closed: order_id={order_id} pnl={realised_pnl:.2f} reason={close_reason}")


def get_open_positions() -> List[Dict]:
    """Список всех открытых позиций."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM positions WHERE status = 'OPEN' ORDER BY opened_at DESC
        """)
        rows = cursor.fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


# ============================================================================
# PNL HISTORY (Phase 2)
# ============================================================================

def upsert_daily_pnl(
    date: str,
    realised_pnl: float,
    trades_count: int,
    wins: int,
    losses: int,
    balance_end: float,
) -> None:
    """Создать или обновить запись дневного P&L (upsert по date)."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO pnl_history (date, realised_pnl, trades_count, wins, losses, balance_end)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                realised_pnl = excluded.realised_pnl,
                trades_count = excluded.trades_count,
                wins = excluded.wins,
                losses = excluded.losses,
                balance_end = excluded.balance_end
        """, (date, realised_pnl, trades_count, wins, losses, balance_end))
        conn.commit()
    finally:
        conn.close()


def get_pnl_history(days: int = 30) -> List[Dict]:
    """Получить историю P&L за последние N дней."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM pnl_history ORDER BY date DESC LIMIT ?
        """, (days,))
        rows = cursor.fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def cleanup_old_snapshots(keep_last_n: int = 10):
    """
    Удаляет старые снимки, оставляя только последние N.
    
    Args:
        keep_last_n: Количество последних снимков для сохранения
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        # Получаем ID последних N снимков
        cursor.execute("""
            SELECT id FROM system_state_snapshots
            ORDER BY timestamp DESC
            LIMIT ?
        """, (keep_last_n,))

        keep_ids = [row["id"] for row in cursor.fetchall()]

        if keep_ids:
            # Удаляем все остальные
            placeholders = ",".join("?" * len(keep_ids))
            cursor.execute(f"""
                DELETE FROM system_state_snapshots
                WHERE id NOT IN ({placeholders})
            """, keep_ids)
        else:
            # Если нет снимков для сохранения, удаляем все
            cursor.execute("DELETE FROM system_state_snapshots")

        deleted = cursor.rowcount
        conn.commit()
    finally:
        conn.close()

    if deleted > 0:
        logger.info(f"Удалено {deleted} старых snapshot'ов")


# ============================================================================
# PNL RECORDS (Phase 3)
# ============================================================================

def insert_pnl_record(
    symbol: str,
    side: str,
    entry_price: float,
    exit_price: float,
    quantity: float,
    gross_pnl: float,
    net_pnl: float,
    commission: float = 0.0,
    market_regime: Optional[str] = None,
    hold_duration_seconds: Optional[int] = None,
    signal_confidence: Optional[float] = None,
    signal_entropy: Optional[float] = None,
    balance_after: Optional[float] = None,
) -> Optional[int]:
    """
    Записывает детальные данные по закрытой сделке в pnl_records.

    Returns:
        ID новой записи или None при ошибке.
    """
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            closed_at = datetime.now(UTC).isoformat()
            cursor.execute("""
                INSERT INTO pnl_records (
                    closed_at, symbol, side, entry_price, exit_price, quantity,
                    gross_pnl, commission, net_pnl, market_regime,
                    hold_duration_seconds, signal_confidence, signal_entropy, balance_after
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                closed_at, symbol, side, entry_price, exit_price, quantity,
                gross_pnl, commission, net_pnl, market_regime,
                hold_duration_seconds, signal_confidence, signal_entropy, balance_after,
            ))
            conn.commit()
            record_id = cursor.lastrowid
        finally:
            conn.close()
        return record_id
    except Exception as e:
        logger.error(f"insert_pnl_record error: {e}")
        return None


def get_closed_trades(days: int = 30) -> List[Dict]:
    """
    Возвращает закрытые сделки из pnl_records за последние N дней.

    Каждая запись содержит:
        symbol, side, entry_price, exit_price, quantity,
        gross_pnl, commission, net_pnl, market_regime,
        hold_duration_seconds, signal_confidence, signal_entropy,
        balance_after, closed_at.

    Ключ 'pnl' является алиасом net_pnl для совместимости с
    функциями финансовых метрик в bot_statistics.py.
    """
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT *
                FROM pnl_records
                WHERE closed_at >= datetime('now', ? || ' days')
                ORDER BY closed_at ASC
            """, (f'-{days}',))
            rows = cursor.fetchall()
        finally:
            conn.close()
        result = []
        for row in rows:
            d = dict(row)
            d['pnl'] = d['net_pnl']  # алиас для calculate_profit_factor и др.
            result.append(d)
        return result
    except Exception as e:
        logger.error(f"get_closed_trades error: {e}")
        return []


def get_equity_curve_points(days: int = 30) -> List[Dict]:
    """
    Возвращает точки equity curve из pnl_records за последние N дней.

    Каждая точка: {'timestamp': str, 'balance': float}.
    Используется для расчёта Sharpe Ratio и Max Drawdown.
    """
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT closed_at AS timestamp, balance_after AS balance
                FROM pnl_records
                WHERE closed_at >= datetime('now', ? || ' days')
                  AND balance_after IS NOT NULL
                ORDER BY closed_at ASC
            """, (f'-{days}',))
            rows = cursor.fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"get_equity_curve_points error: {e}")
        return []


# ============================================================================
# USER SETTINGS (Phase 5)
# ============================================================================

def get_setting(key: str) -> Optional[str]:
    """Вернуть значение настройки или None."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT setting_value FROM user_settings WHERE setting_key = ?", (key,)
        )
        row = cursor.fetchone()
    finally:
        conn.close()
    return row["setting_value"] if row else None


def set_setting(key: str, value: str, data_type: str) -> None:
    """Сохранить или обновить настройку (UPSERT)."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        updated_at = datetime.now(UTC).isoformat()
        cursor.execute("""
            INSERT INTO user_settings (setting_key, setting_value, data_type, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = excluded.setting_value,
                data_type = excluded.data_type,
                updated_at = excluded.updated_at
        """, (key, value, data_type, updated_at))
        conn.commit()
    finally:
        conn.close()


def get_all_settings() -> Dict[str, Dict]:
    """Вернуть все настройки как {key: {value, data_type, updated_at}}."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT setting_key, setting_value, data_type, updated_at FROM user_settings")
        rows = cursor.fetchall()
    finally:
        conn.close()
    return {
        row["setting_key"]: {
            "value": row["setting_value"],
            "data_type": row["data_type"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    }


# ============================================================================
# SIGNAL OUTCOMES (система обучения)
# ============================================================================

def save_signal_outcome(data: dict) -> Optional[int]:
    """
    Сохраняет исход сигнала. Возвращает id или None если дубликат.

    Args:
        data: dict with keys: signal_ts, symbol, direction, entry, tp, sl,
              confidence (opt), state_15m (opt), checked_at, outcome,
              candles_checked (opt), max_favorable_pct (opt), max_adverse_pct (opt)
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO signal_outcomes
            (signal_ts, symbol, direction, entry, tp, sl, confidence, state_15m,
             checked_at, outcome, candles_checked, max_favorable_pct, max_adverse_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["signal_ts"], data["symbol"], data["direction"],
            data["entry"], data["tp"], data["sl"],
            data.get("confidence"), data.get("state_15m"),
            data["checked_at"], data["outcome"],
            data.get("candles_checked", 0),
            data.get("max_favorable_pct"), data.get("max_adverse_pct"),
        ))
        conn.commit()
        return cursor.lastrowid if cursor.rowcount > 0 else None
    finally:
        conn.close()


def is_outcome_tracked(signal_ts: str, symbol: str) -> bool:
    """Проверяет, записан ли уже исход для данного сигнала."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM signal_outcomes WHERE signal_ts = ? AND symbol = ? LIMIT 1",
            (signal_ts, symbol),
        )
        return cursor.fetchone() is not None
    finally:
        conn.close()


def get_outcomes_for_analysis(days: int = 30) -> List[Dict]:
    """
    Возвращает все исходы за последние N дней для анализа точности.

    Args:
        days: Период в днях
    Returns:
        Список словарей с полями таблицы signal_outcomes
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        cursor.execute("""
            SELECT * FROM signal_outcomes
            WHERE signal_ts >= ?
            ORDER BY signal_ts DESC
        """, (since,))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
