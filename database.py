"""
База данных для торговых данных.

Поддерживает два режима:
  - SQLite (по умолчанию): используется для локальной разработки
  - PostgreSQL: включается установкой DATABASE_URL в .env

Используется для:
- Статистики
- Агрегаций
- Дневника сделок

CSV остается для логов (signals_log.csv)
"""
import os
import sqlite3
import threading
from datetime import datetime, UTC, timedelta
from typing import List, Dict, Optional

import logging

logger = logging.getLogger(__name__)

# ========== DATABASE MODE ==========

_DATABASE_URL = os.environ.get("DATABASE_URL", "")
_PG_MODE = bool(_DATABASE_URL)

DB_PATH = os.environ.get("DB_PATH", "market_bot.db")

# ========== FAULT INJECTION (для тестирования устойчивости) ==========

FAULT_INJECT_STORAGE_FAILURE = (
    os.environ.get("FAULT_INJECT_STORAGE_FAILURE", "false").lower() == "true"
)

# ========== POSTGRESQL POOL ==========

_pg_pool = None

if _PG_MODE:
    try:
        import psycopg2
        import psycopg2.pool
        import psycopg2.extras

        _PG_POOL_MIN = int(os.environ.get("PG_POOL_MIN", "2"))
        _PG_POOL_MAX = int(os.environ.get("PG_POOL_MAX", "10"))
        _pg_pool = psycopg2.pool.ThreadedConnectionPool(_PG_POOL_MIN, _PG_POOL_MAX, _DATABASE_URL)
        logger.info("PostgreSQL connection pool initialized (min=%d max=%d)", _PG_POOL_MIN, _PG_POOL_MAX)
    except Exception as _pg_init_err:
        logger.critical(
            "FATAL: DATABASE_URL set but psycopg2 pool init failed: %s", _pg_init_err
        )
        raise

# ========== POOL LIFECYCLE ==========


def close_pg_pool() -> None:
    """Закрывает PostgreSQL connection pool при завершении процесса."""
    global _pg_pool
    if _pg_pool is not None:
        try:
            _pg_pool.closeall()
            logger.info("PostgreSQL connection pool closed")
        except Exception as e:
            logger.warning("Error closing PostgreSQL pool: %s", e)
        finally:
            _pg_pool = None


def checkpoint_sqlite_wal() -> None:
    """
    Запускает WAL checkpoint (TRUNCATE mode) для SQLite.

    Вызывается при graceful shutdown, чтобы очистить WAL-файл и избежать
    его неограниченного роста. Только в SQLite-режиме.
    """
    if _PG_MODE:
        return
    try:
        conn = getattr(_thread_local, "conn", None)
        if conn is not None:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            logger.info("SQLite WAL checkpoint completed")
    except Exception as e:
        logger.warning("SQLite WAL checkpoint failed: %s", e)


# ========== SQL DIALECT HELPERS ==========


def _q(sql: str) -> str:
    """Convert SQLite ? placeholders to PostgreSQL %s when in PG mode."""
    return sql.replace("?", "%s") if _PG_MODE else sql


def _exec_insert(cursor, sql: str, params) -> int:
    """
    Execute an INSERT and return the new row id.

    SQLite: uses cursor.lastrowid
    PostgreSQL: appends RETURNING id and fetches the result
    """
    if _PG_MODE:
        cursor.execute(_q(sql) + " RETURNING id", params)
        row = cursor.fetchone()
        return row["id"] if row else 0
    else:
        cursor.execute(sql, params)
        return cursor.lastrowid


def _insert_ignore(table_and_rest: str) -> str:
    """
    Build an INSERT that silently ignores unique-constraint violations.

    table_and_rest: everything after INSERT (e.g. 'INTO t (a,b) VALUES (?,?)')
    """
    if _PG_MODE:
        return f"INSERT {table_and_rest} ON CONFLICT DO NOTHING"
    return f"INSERT OR IGNORE {table_and_rest}"


# ========== THREAD-LOCAL SQLite POOL ==========
# Used only in SQLite mode.

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


def _get_raw_sqlite_connection() -> sqlite3.Connection:
    conn = getattr(_thread_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _init_database(conn)
        _thread_local.conn = conn
    return conn


# ========== POSTGRESQL CONNECTION WRAPPER ==========


class _PGConnection:
    """Wraps a psycopg2 connection from the pool with the same interface as _PooledConnection."""

    def __init__(self) -> None:
        self._conn = _pg_pool.getconn()
        self._conn.autocommit = False

    def cursor(self):
        return self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        """Return connection to pool after rolling back any open transaction."""
        try:
            self._conn.rollback()
        except Exception as e:
            logger.warning("_PGConnection.close: rollback failed: %s", e)
        _pg_pool.putconn(self._conn)


# ========== PUBLIC CONNECTION FACTORY ==========


def get_db_connection():
    """
    Returns a database connection appropriate for the current mode.

    PostgreSQL mode: borrows from ThreadedConnectionPool; .close() returns to pool.
    SQLite mode: returns thread-local _PooledConnection; .close() rolls back.

    Callers must always:
      conn.commit()  after writes
      conn.close()   in a finally block
    """
    if _PG_MODE:
        return _PGConnection()
    return _PooledConnection(_get_raw_sqlite_connection())


# ========== SCHEMA INITIALISATION ==========


def _init_pg_schema(conn) -> None:
    """Create all tables in PostgreSQL if they don't already exist."""
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id BIGSERIAL PRIMARY KEY,
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
            created_at TEXT NOT NULL DEFAULT TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
            updated_at TEXT NOT NULL DEFAULT TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp)")

    # Phase 2: колонки для trailing stop, partial TP, strategy tracking
    for col_name, col_type in [
        ("trailing_stop", "REAL"),
        ("breakeven_set", "INTEGER DEFAULT 0"),
        ("partial_closed", "INTEGER DEFAULT 0"),
        ("partial_pnl", "REAL"),
        ("strategy_name", "TEXT"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE trades ADD COLUMN {col_name} {col_type}")
        except Exception:
            pass  # колонка уже существует

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_state_snapshots (
            id BIGSERIAL PRIMARY KEY,
            timestamp TEXT NOT NULL,
            snapshot_data TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON system_state_snapshots(timestamp DESC)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id BIGSERIAL PRIMARY KEY,
            order_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            order_type TEXT NOT NULL,
            qty REAL NOT NULL,
            entry_price REAL,
            stop_loss REAL NOT NULL,
            take_profit REAL,
            status TEXT NOT NULL DEFAULT 'CREATED',
            dry_run INTEGER NOT NULL DEFAULT 1,
            error TEXT,
            created_at TEXT NOT NULL DEFAULT TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
            updated_at TEXT NOT NULL DEFAULT TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_order_id ON orders(order_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id BIGSERIAL PRIMARY KEY,
            order_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            qty REAL NOT NULL,
            entry_price REAL NOT NULL,
            stop_loss REAL NOT NULL,
            take_profit REAL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            close_price REAL,
            close_reason TEXT,
            realised_pnl REAL,
            opened_at TEXT NOT NULL DEFAULT TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
            closed_at TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pnl_history (
            id BIGSERIAL PRIMARY KEY,
            date TEXT NOT NULL UNIQUE,
            realised_pnl REAL NOT NULL DEFAULT 0.0,
            trades_count INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            balance_end REAL NOT NULL DEFAULT 0.0,
            created_at TEXT NOT NULL DEFAULT TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pnl_history_date ON pnl_history(date)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pnl_records (
            id BIGSERIAL PRIMARY KEY,
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
            created_at TEXT NOT NULL DEFAULT TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_pnl_records_closed_at ON pnl_records(closed_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_pnl_records_symbol ON pnl_records(symbol)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            id BIGSERIAL PRIMARY KEY,
            setting_key TEXT NOT NULL UNIQUE,
            setting_value TEXT NOT NULL,
            data_type TEXT NOT NULL,
            updated_at TEXT DEFAULT TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signal_outcomes (
            id BIGSERIAL PRIMARY KEY,
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
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_signal_outcomes_symbol ON signal_outcomes(symbol)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_signal_outcomes_outcome ON signal_outcomes(outcome)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_signal_outcomes_ts ON signal_outcomes(signal_ts)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS encrypted_api_keys (
            id BIGSERIAL PRIMARY KEY,
            key_name TEXT NOT NULL UNIQUE,
            encrypted_value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signal_journal (
            id BIGSERIAL PRIMARY KEY,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            state_1h TEXT,
            state_30m TEXT,
            state_15m TEXT,
            state_5m TEXT,
            risk TEXT,
            entry REAL,
            tp REAL,
            sl REAL,
            rr_ratio REAL,
            decision TEXT,
            confidence REAL,
            direction TEXT,
            created_at TEXT NOT NULL DEFAULT TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_signal_journal_symbol ON signal_journal(symbol)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_signal_journal_timestamp ON signal_journal(timestamp DESC)"
    )

    conn.commit()


def _init_database(conn) -> None:
    """Initialise database schema (SQLite or PostgreSQL)."""
    if _PG_MODE:
        _init_pg_schema(conn)
        return

    # ---- SQLite schema ----
    cursor = conn.cursor()

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
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp)")

    # Phase 2: колонки для trailing stop, partial TP, strategy tracking
    for col_name, col_type in [
        ("trailing_stop", "REAL"),
        ("breakeven_set", "INTEGER DEFAULT 0"),
        ("partial_closed", "INTEGER DEFAULT 0"),
        ("partial_pnl", "REAL"),
        ("strategy_name", "TEXT"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE trades ADD COLUMN {col_name} {col_type}")
        except Exception:
            pass  # колонка уже существует

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_state_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            snapshot_data TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON system_state_snapshots(timestamp DESC)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            order_type TEXT NOT NULL,
            qty REAL NOT NULL,
            entry_price REAL,
            stop_loss REAL NOT NULL,
            take_profit REAL,
            status TEXT NOT NULL DEFAULT 'CREATED',
            dry_run INTEGER NOT NULL DEFAULT 1,
            error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_order_id ON orders(order_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            qty REAL NOT NULL,
            entry_price REAL NOT NULL,
            stop_loss REAL NOT NULL,
            take_profit REAL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            close_price REAL,
            close_reason TEXT,
            realised_pnl REAL,
            opened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            closed_at TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pnl_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            realised_pnl REAL NOT NULL DEFAULT 0.0,
            trades_count INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            balance_end REAL NOT NULL DEFAULT 0.0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pnl_history_date ON pnl_history(date)")

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
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_pnl_records_closed_at ON pnl_records(closed_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_pnl_records_symbol ON pnl_records(symbol)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            id INTEGER PRIMARY KEY,
            setting_key TEXT NOT NULL UNIQUE,
            setting_value TEXT NOT NULL,
            data_type TEXT NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

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
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_signal_outcomes_symbol ON signal_outcomes(symbol)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_signal_outcomes_outcome ON signal_outcomes(outcome)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_signal_outcomes_ts ON signal_outcomes(signal_ts)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS encrypted_api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_name TEXT NOT NULL UNIQUE,
            encrypted_value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signal_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            state_1h TEXT,
            state_30m TEXT,
            state_15m TEXT,
            state_5m TEXT,
            risk TEXT,
            entry REAL,
            tp REAL,
            sl REAL,
            rr_ratio REAL,
            decision TEXT,
            confidence REAL,
            direction TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_signal_journal_symbol ON signal_journal(symbol)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_signal_journal_timestamp ON signal_journal(timestamp)"
    )

    conn.commit()


# ============================================================================
# INITIALISE PostgreSQL ON STARTUP
# ============================================================================

if _PG_MODE:
    _init_conn = _PGConnection()
    try:
        _init_pg_schema(_init_conn)
    finally:
        _init_conn.close()
    logger.info("PostgreSQL schema ready")


# ============================================================================
# TRADES (Сделки)
# ============================================================================


def add_trade(
    symbol: str,
    side: str,
    entry: float,
    stop: float,
    target: float,
    position_size: Optional[float] = None,
    leverage: Optional[float] = None,
    strategy_name: Optional[str] = None,
) -> int:
    """Добавляет новую сделку в базу данных. Возвращает ID."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        timestamp = datetime.now(UTC).isoformat()
        trade_id = _exec_insert(
            cursor,
            """
            INSERT INTO trades (timestamp, symbol, side, entry, stop, target, status, position_size, leverage, strategy_name)
            VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)
            """,
            (timestamp, symbol, side, entry, stop, target, position_size, leverage, strategy_name),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info(f"Добавлена сделка #{trade_id}: {symbol} {side} @ {entry} [strategy={strategy_name}]")
    return trade_id


def get_open_trades() -> List[Dict]:
    """Получает список всех открытых сделок."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            _q("SELECT * FROM trades WHERE status = 'OPEN' ORDER BY timestamp DESC")
        )
        rows = cursor.fetchall()
    finally:
        conn.close()
    return [
        {
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
            "trailing_stop": row["trailing_stop"] if "trailing_stop" in row.keys() else None,
            "breakeven_set": row["breakeven_set"] if "breakeven_set" in row.keys() else 0,
            "partial_closed": row["partial_closed"] if "partial_closed" in row.keys() else 0,
            "partial_pnl": row["partial_pnl"] if "partial_pnl" in row.keys() else None,
        }
        for row in rows
    ]


def close_trade(trade_id: int, close_price: float, close_reason: str, pnl: float):
    """Закрывает сделку в базе данных."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        updated_at = datetime.now(UTC).isoformat()
        cursor.execute(
            _q("""
            UPDATE trades
            SET status = 'CLOSED', close_price = ?, close_reason = ?, pnl = ?, updated_at = ?
            WHERE id = ?
            """),
            (close_price, close_reason, pnl, updated_at, trade_id),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info(f"Закрыта сделка #{trade_id}: PnL={pnl:.2f} USDT, причина={close_reason}")


def update_trade_stop(trade_id: int, new_stop: float, breakeven: bool = False):
    """Обновляет стоп-лосс сделки (trailing stop / breakeven)."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        updated_at = datetime.now(UTC).isoformat()
        if breakeven:
            cursor.execute(
                _q("UPDATE trades SET stop = ?, trailing_stop = ?, breakeven_set = 1, updated_at = ? WHERE id = ?"),
                (new_stop, new_stop, updated_at, trade_id),
            )
        else:
            cursor.execute(
                _q("UPDATE trades SET stop = ?, trailing_stop = ?, updated_at = ? WHERE id = ?"),
                (new_stop, new_stop, updated_at, trade_id),
            )
        conn.commit()
    finally:
        conn.close()


def update_trade_partial(trade_id: int, partial_price: float, partial_pnl: float):
    """Записывает частичное закрытие (50% позиции)."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        updated_at = datetime.now(UTC).isoformat()
        cursor.execute(
            _q("UPDATE trades SET partial_closed = 1, partial_pnl = ?, updated_at = ? WHERE id = ?"),
            (partial_pnl, updated_at, trade_id),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info(f"Частичное закрытие сделки #{trade_id}: PnL={partial_pnl:.2f} USDT @ {partial_price}")


def force_cancel_open_trades() -> int:
    """
    Принудительно закрывает все сделки со статусом OPEN (помечает как CANCELLED).
    Используется для сброса зависших позиций, блокирующих Risk Core.
    Возвращает количество обновлённых записей.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        updated_at = datetime.now(UTC).isoformat()
        cursor.execute(
            _q("UPDATE trades SET status = 'CANCELLED', close_reason = 'FORCE_CANCEL', updated_at = ? WHERE status = 'OPEN'"),
            (updated_at,),
        )
        count = cursor.rowcount
        cursor.execute(
            _q("UPDATE positions SET status = 'CANCELLED', close_reason = 'FORCE_CANCEL', closed_at = ? WHERE status = 'OPEN'"),
            (updated_at,),
        )
        count += cursor.rowcount
        conn.commit()
        logger.warning("force_cancel_open_trades: закрыто %d записей", count)
        return count
    finally:
        conn.close()


def get_trades_by_symbol(symbol: str, status: Optional[str] = None) -> List[Dict]:
    """Получает сделки по символу."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        if status:
            cursor.execute(
                _q("SELECT * FROM trades WHERE symbol = ? AND status = ? ORDER BY timestamp DESC"),
                (symbol, status),
            )
        else:
            cursor.execute(
                _q("SELECT * FROM trades WHERE symbol = ? ORDER BY timestamp DESC"),
                (symbol,),
            )
        rows = cursor.fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def get_trades_statistics(days: int = 1) -> Dict:
    """Получает статистику по сделкам за последние N дней."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cutoff_time = (
            datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            - timedelta(days=days)
        ).isoformat()

        cursor.execute(
            _q("""
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
            """),
            (cutoff_time,),
        )
        stats_row = cursor.fetchone()

        cursor.execute(
            _q("SELECT COUNT(*) as open_trades FROM trades WHERE status = 'OPEN'")
        )
        open_trades = cursor.fetchone()["open_trades"]

        cursor.execute(
            _q("""
            SELECT
                symbol,
                COUNT(*) as trades,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(pnl) as pnl
            FROM trades
            WHERE status = 'CLOSED' AND timestamp >= ?
            GROUP BY symbol
            ORDER BY pnl DESC
            """),
            (cutoff_time,),
        )
        symbol_stats = {
            row["symbol"]: {"trades": row["trades"], "wins": row["wins"], "pnl": row["pnl"]}
            for row in cursor.fetchall()
        }

        cursor.execute(
            _q("""
            SELECT symbol, side, pnl FROM trades
            WHERE status = 'CLOSED' AND timestamp >= ?
            ORDER BY pnl DESC LIMIT 1
            """),
            (cutoff_time,),
        )
        best_trade_row = cursor.fetchone()

        cursor.execute(
            _q("""
            SELECT symbol, side, pnl FROM trades
            WHERE status = 'CLOSED' AND timestamp >= ?
            ORDER BY pnl ASC LIMIT 1
            """),
            (cutoff_time,),
        )
        worst_trade_row = cursor.fetchone()
    finally:
        conn.close()

    total_trades = stats_row["total_trades"] or 0
    winning_trades = stats_row["winning_trades"] or 0
    losing_trades = stats_row["losing_trades"] or 0
    total_pnl = stats_row["total_pnl"] or 0.0
    avg_pnl = stats_row["avg_pnl"] or 0.0
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0

    best_trade = (
        {"symbol": best_trade_row["symbol"], "pnl": best_trade_row["pnl"], "side": best_trade_row["side"]}
        if best_trade_row
        else None
    )
    worst_trade = (
        {"symbol": worst_trade_row["symbol"], "pnl": worst_trade_row["pnl"], "side": worst_trade_row["side"]}
        if worst_trade_row
        else None
    )

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
        "wins": winning_trades,
        "losses": losing_trades,
    }


def get_current_balance_from_db(initial_balance: float = 10000.0) -> float:
    """Рассчитывает текущий баланс на основе закрытых сделок."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            _q("SELECT COALESCE(SUM(pnl), 0) as total_pnl FROM trades WHERE status = 'CLOSED'")
        )
        row = cursor.fetchone()
        total_pnl = row["total_pnl"] or 0.0
    finally:
        conn.close()
    return max(initial_balance + total_pnl, 10.0)


def get_total_open_positions_size() -> float:
    """Sum of position_size for all OPEN trades (locked capital)."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            _q("SELECT COALESCE(SUM(position_size), 0) AS total FROM trades WHERE status = 'OPEN' AND position_size IS NOT NULL")
        )
        return float(cursor.fetchone()["total"] or 0.0)
    finally:
        conn.close()


def migrate_from_csv(csv_file: str = "demo_trades.csv"):
    """Мигрирует данные из CSV в базу данных."""
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
            first_row = next(reader, None)
            if first_row and len(first_row) > 0:
                if first_row[0].lower() not in ("timestamp", "time", "date"):
                    f.seek(0)
                    reader = csv.reader(f)

            for row in reader:
                if len(row) < 7:
                    continue
                try:
                    timestamp, symbol, side = row[0], row[1], row[2]
                    entry, stop, target = float(row[3]), float(row[4]), float(row[5])
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
                    close_price = close_reason = pnl = None
                    if status == "CLOSED" and len(row) >= 11:
                        try:
                            close_price = float(row[9]) if row[9] else None
                            close_reason = row[10] if len(row) > 10 else None
                            pnl = float(row[11]) if len(row) > 11 and row[11] else None
                        except (ValueError, IndexError):
                            pass

                    cursor.execute(
                        _q("SELECT id FROM trades WHERE timestamp = ? AND symbol = ? AND status = ?"),
                        (timestamp, symbol, status),
                    )
                    if cursor.fetchone():
                        continue

                    cursor.execute(
                        _q("""
                        INSERT INTO trades (
                            timestamp, symbol, side, entry, stop, target, status,
                            position_size, leverage, close_price, close_reason, pnl
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """),
                        (
                            timestamp, symbol, side, entry, stop, target, status,
                            position_size, leverage, close_price, close_reason, pnl,
                        ),
                    )
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
    """Сохраняет снимок SystemState в базу данных."""
    import json

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        timestamp = snapshot_data.get("timestamp", datetime.now(UTC).isoformat())
        snapshot_json = json.dumps(snapshot_data)
        snapshot_id = _exec_insert(
            cursor,
            "INSERT INTO system_state_snapshots (timestamp, snapshot_data) VALUES (?, ?)",
            (timestamp, snapshot_json),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info(f"Сохранён snapshot SystemState #{snapshot_id}")
    return snapshot_id


def get_latest_system_state_snapshot() -> Optional[Dict]:
    """Получает последний снимок SystemState из базы данных."""
    import json

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            _q("SELECT snapshot_data FROM system_state_snapshots ORDER BY timestamp DESC LIMIT 1")
        )
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


def cleanup_old_snapshots(keep_last_n: int = 10):
    """Удаляет старые снимки, оставляя только последние N."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            _q("SELECT id FROM system_state_snapshots ORDER BY timestamp DESC LIMIT ?"),
            (keep_last_n,),
        )
        keep_ids = [row["id"] for row in cursor.fetchall()]

        if not keep_ids:
            cursor.execute("DELETE FROM system_state_snapshots")
        elif _PG_MODE:
            cursor.execute(
                "DELETE FROM system_state_snapshots WHERE id NOT IN %s",
                (tuple(keep_ids),),
            )
        else:
            placeholders = ",".join("?" * len(keep_ids))
            cursor.execute(
                f"DELETE FROM system_state_snapshots WHERE id NOT IN ({placeholders})",
                keep_ids,
            )

        deleted = cursor.rowcount
        conn.commit()
    finally:
        conn.close()
    if deleted > 0:
        logger.info(f"Удалено {deleted} старых snapshot'ов")


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
        row_id = _exec_insert(
            cursor,
            """
            INSERT INTO orders
                (order_id, symbol, side, order_type, qty, entry_price,
                 stop_loss, take_profit, status, dry_run, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (order_id, symbol, side, order_type, qty, entry_price,
             stop_loss, take_profit, status, int(dry_run), error),
        )
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
        cursor.execute(
            _q("UPDATE orders SET status = ?, error = ?, updated_at = ? WHERE order_id = ?"),
            (status, error, updated_at, order_id),
        )
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
        row_id = _exec_insert(
            cursor,
            """
            INSERT INTO positions
                (order_id, symbol, side, qty, entry_price, stop_loss, take_profit, status, opened_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
            """,
            (order_id, symbol, side, qty, entry_price, stop_loss, take_profit, opened_at),
        )
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
        cursor.execute(
            _q("""
            UPDATE positions
            SET status = 'CLOSED', close_price = ?, close_reason = ?,
                realised_pnl = ?, closed_at = ?
            WHERE order_id = ? AND status = 'OPEN'
            """),
            (close_price, close_reason, realised_pnl, closed_at, order_id),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info(f"Position closed: order_id={order_id} pnl={realised_pnl:.2f} reason={close_reason}")


def get_open_positions() -> List[Dict]:
    """Список всех открытых позиций из таблицы trades."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            _q("SELECT * FROM trades WHERE status = 'OPEN' ORDER BY timestamp DESC")
        )
        rows = cursor.fetchall()
    finally:
        conn.close()
    result = []
    for row in rows:
        d = dict(row)
        result.append({
            "id": d["id"],
            "symbol": d["symbol"],
            "side": d["side"],
            "qty": d.get("position_size") or 0.0,
            "entry_price": d.get("entry") or 0.0,
            "stop_loss": d.get("stop"),
            "take_profit": d.get("target"),
            "opened_at": d.get("timestamp") or d.get("created_at") or "",
        })
    return result


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
        cursor.execute(
            _q("""
            INSERT INTO pnl_history (date, realised_pnl, trades_count, wins, losses, balance_end)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                realised_pnl = EXCLUDED.realised_pnl,
                trades_count = EXCLUDED.trades_count,
                wins = EXCLUDED.wins,
                losses = EXCLUDED.losses,
                balance_end = EXCLUDED.balance_end
            """),
            (date, realised_pnl, trades_count, wins, losses, balance_end),
        )
        conn.commit()
    finally:
        conn.close()


def get_pnl_history(days: int = 30) -> List[Dict]:
    """Получить историю P&L за последние N дней."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            _q("SELECT * FROM pnl_history ORDER BY date DESC LIMIT ?"), (days,)
        )
        rows = cursor.fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


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
    """Записывает детальные данные по закрытой сделке в pnl_records."""
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            closed_at = datetime.now(UTC).isoformat()
            record_id = _exec_insert(
                cursor,
                """
                INSERT INTO pnl_records (
                    closed_at, symbol, side, entry_price, exit_price, quantity,
                    gross_pnl, commission, net_pnl, market_regime,
                    hold_duration_seconds, signal_confidence, signal_entropy, balance_after
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    closed_at, symbol, side, entry_price, exit_price, quantity,
                    gross_pnl, commission, net_pnl, market_regime,
                    hold_duration_seconds, signal_confidence, signal_entropy, balance_after,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return record_id
    except Exception as e:
        logger.error(f"insert_pnl_record error: {e}")
        return None


def get_closed_trades(days: int = 30) -> List[Dict]:
    """
    Возвращает закрытые сделки из таблицы trades за последние N дней.
    Поля нормализованы для совместимости с PerformanceTracker и API.
    """
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
            cursor.execute(
                _q("""
                SELECT * FROM trades
                WHERE status = 'CLOSED' AND timestamp >= ?
                ORDER BY updated_at ASC
                """),
                (since,),
            )
            rows = cursor.fetchall()
        finally:
            conn.close()
        result = []
        for row in rows:
            d = dict(row)
            net_pnl = d.get("pnl") or 0.0
            result.append({
                "id": d["id"],
                "symbol": d["symbol"],
                "side": d["side"],
                "entry_price": d.get("entry") or 0.0,
                "exit_price": d.get("close_price") or 0.0,
                "quantity": d.get("position_size") or 0.0,
                "net_pnl": net_pnl,
                "pnl": net_pnl,           # alias for legacy code
                "gross_pnl": net_pnl,     # no commission data in trades
                "commission": 0.0,
                "market_regime": d.get("market_regime"),
                "closed_at": d.get("updated_at") or d.get("created_at") or "",
            })
        return result
    except Exception as e:
        logger.error(f"get_closed_trades error: {e}")
        return []


def get_equity_curve_points(days: int = 30) -> List[Dict]:
    """Возвращает точки equity curve из таблицы trades за последние N дней.
    Баланс вычисляется нарастающим итогом от начального значения 10000 USDT."""
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
            # Sum PnL of all closed trades BEFORE the period to get starting balance
            cursor.execute(
                _q("SELECT COALESCE(SUM(pnl), 0) AS pnl_before FROM trades WHERE status = 'CLOSED' AND timestamp < ?"),
                (since,),
            )
            row0 = cursor.fetchone()
            pnl_before = float((dict(row0).get("pnl_before") or 0.0) if row0 else 0.0)
            running_balance = 10000.0 + pnl_before

            cursor.execute(
                _q("""
                SELECT updated_at AS timestamp, pnl
                FROM trades
                WHERE status = 'CLOSED' AND timestamp >= ? AND pnl IS NOT NULL
                ORDER BY updated_at ASC
                """),
                (since,),
            )
            rows = cursor.fetchall()
        finally:
            conn.close()
        result = []
        for row in rows:
            d = dict(row)
            running_balance += d["pnl"] or 0.0
            result.append({"timestamp": d["timestamp"], "balance": round(running_balance, 4)})
        return result
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
            _q("SELECT setting_value FROM user_settings WHERE setting_key = ?"), (key,)
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
        cursor.execute(
            _q("""
            INSERT INTO user_settings (setting_key, setting_value, data_type, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = EXCLUDED.setting_value,
                data_type = EXCLUDED.data_type,
                updated_at = EXCLUDED.updated_at
            """),
            (key, value, data_type, updated_at),
        )
        conn.commit()
    finally:
        conn.close()


def get_all_settings() -> Dict[str, Dict]:
    """Вернуть все настройки как {key: {value, data_type, updated_at}}."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            _q("SELECT setting_key, setting_value, data_type, updated_at FROM user_settings")
        )
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
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        params = (
            data["signal_ts"], data["symbol"], data["direction"],
            data["entry"], data["tp"], data["sl"],
            data.get("confidence"), data.get("state_15m"),
            data["checked_at"], data["outcome"],
            data.get("candles_checked", 0),
            data.get("max_favorable_pct"), data.get("max_adverse_pct"),
        )
        base = """INTO signal_outcomes
            (signal_ts, symbol, direction, entry, tp, sl, confidence, state_15m,
             checked_at, outcome, candles_checked, max_favorable_pct, max_adverse_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        if _PG_MODE:
            # RETURNING id returns row only on actual insert; nothing on conflict
            cursor.execute(_q(f"INSERT {base} ON CONFLICT DO NOTHING RETURNING id"), params)
            row = cursor.fetchone()
            conn.commit()
            return row["id"] if row else None
        else:
            cursor.execute(f"INSERT OR IGNORE {base}", params)
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
            _q("SELECT 1 FROM signal_outcomes WHERE signal_ts = ? AND symbol = ? LIMIT 1"),
            (signal_ts, symbol),
        )
        return cursor.fetchone() is not None
    finally:
        conn.close()


def get_outcomes_for_analysis(days: int = 30) -> List[Dict]:
    """Возвращает все исходы за последние N дней для анализа точности."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        cursor.execute(
            _q("SELECT * FROM signal_outcomes WHERE signal_ts >= ? ORDER BY signal_ts DESC"),
            (since,),
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# ============================================================================
# SIGNAL JOURNAL
# ============================================================================


def log_signal_to_db(snapshot) -> None:
    """Записывает SignalSnapshot в signal_journal."""
    from core.market_state import state_to_string

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        direction = snapshot.side or (
            "LONG"
            if (snapshot.entry and snapshot.tp and snapshot.tp > snapshot.entry)
            else "SHORT"
            if (snapshot.entry and snapshot.tp)
            else ""
        )
        cursor.execute(
            _q(
                """INSERT INTO signal_journal
                (timestamp, symbol, state_1h, state_30m, state_15m, state_5m,
                 risk, entry, tp, sl, rr_ratio, decision, confidence, direction)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
            ),
            (
                snapshot.timestamp.isoformat(),
                snapshot.symbol,
                state_to_string(snapshot.states.get("1h")),
                state_to_string(snapshot.states.get("30m")),
                state_to_string(snapshot.states.get("15m")),
                state_to_string(snapshot.states.get("5m")),
                snapshot.risk_level.value if snapshot.risk_level else None,
                snapshot.entry,
                snapshot.tp,
                snapshot.sl,
                snapshot.rr_ratio,
                snapshot.decision.value if snapshot.decision else None,
                snapshot.confidence,
                direction,
            ),
        )
        conn.commit()
    except Exception as e:
        logger.warning("log_signal_to_db failed: %s", e)
    finally:
        conn.close()


def get_signals_from_db(since_iso: str, limit: int = 200) -> List[Dict]:
    """Возвращает сигналы из signal_journal начиная с since_iso."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            _q(
                "SELECT * FROM signal_journal WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT ?"
            ),
            (since_iso, limit),
        )
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ============================================================================
# ENCRYPTED API KEYS (Phase 6)
# ============================================================================


def save_encrypted_api_key(key_name: str, encrypted_value: str) -> None:
    """
    Сохраняет или обновляет зашифрованный API-ключ в БД (UPSERT по key_name).

    Args:
        key_name: Имя ключа, например 'BYBIT_API_KEY'
        encrypted_value: Зашифрованное значение (Fernet token, base64)
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        updated_at = datetime.now(UTC).isoformat()
        cursor.execute(
            _q("""
            INSERT INTO encrypted_api_keys (key_name, encrypted_value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key_name) DO UPDATE SET
                encrypted_value = EXCLUDED.encrypted_value,
                updated_at = EXCLUDED.updated_at
            """),
            (key_name, encrypted_value, updated_at),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info(f"Encrypted API key saved: {key_name}")


def get_encrypted_api_key(key_name: str) -> Optional[str]:
    """
    Возвращает зашифрованное значение ключа или None если не найдено.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            _q("SELECT encrypted_value FROM encrypted_api_keys WHERE key_name = ?"),
            (key_name,),
        )
        row = cursor.fetchone()
    finally:
        conn.close()
    return row["encrypted_value"] if row else None


def list_encrypted_key_names() -> List[str]:
    """Возвращает список имён сохранённых ключей (без значений)."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(_q("SELECT key_name, updated_at FROM encrypted_api_keys ORDER BY key_name"))
        rows = cursor.fetchall()
    finally:
        conn.close()
    return [{"key_name": row["key_name"], "updated_at": row["updated_at"]} for row in rows]
