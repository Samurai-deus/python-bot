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
from datetime import datetime, UTC
from typing import List, Dict, Optional
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

# Путь к базе данных
DB_PATH = os.environ.get("DB_PATH", "market_bot.db")

# ========== FAULT INJECTION (для тестирования устойчивости) ==========

FAULT_INJECT_STORAGE_FAILURE = os.environ.get("FAULT_INJECT_STORAGE_FAILURE", "false").lower() == "true"


def get_db_connection():
    """
    Получает соединение с базой данных.
    Создает базу и таблицы, если их нет.
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row  # Для доступа к колонкам по имени
    _init_database(conn)
    return conn


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
    cursor = conn.cursor()
    
    timestamp = datetime.now(UTC).isoformat()
    
    cursor.execute("""
        INSERT INTO trades (timestamp, symbol, side, entry, stop, target, status, position_size, leverage)
        VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)
    """, (timestamp, symbol, side, entry, stop, target, position_size, leverage))
    
    trade_id = cursor.lastrowid
    conn.commit()
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
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT * FROM trades WHERE status = 'OPEN' ORDER BY timestamp DESC
    """)
    
    rows = cursor.fetchall()
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
    cursor = conn.cursor()
    
    updated_at = datetime.now(UTC).isoformat()
    
    cursor.execute("""
        UPDATE trades 
        SET status = 'CLOSED', close_price = ?, close_reason = ?, pnl = ?, updated_at = ?
        WHERE id = ?
    """, (close_price, close_reason, pnl, updated_at, trade_id))
    
    conn.commit()
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
    cursor = conn.cursor()
    
    cutoff_time = (datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - 
                   __import__('datetime').timedelta(days=days)).isoformat()
    
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
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT COALESCE(SUM(pnl), 0) as total_pnl
        FROM trades
        WHERE status = 'CLOSED'
    """)
    
    row = cursor.fetchone()
    total_pnl = row["total_pnl"] or 0.0
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
    cursor = conn.cursor()
    
    migrated = 0
    errors = 0
    
    try:
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
        conn.close()
        
        logger.info(f"Миграция завершена: {migrated} сделок мигрировано, {errors} ошибок")
        
    except Exception as e:
        logger.error(f"Критическая ошибка при миграции: {e}", exc_info=True)
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
    cursor = conn.cursor()
    
    timestamp = snapshot_data.get("timestamp", datetime.now(UTC).isoformat())
    snapshot_json = json.dumps(snapshot_data)
    
    cursor.execute("""
        INSERT INTO system_state_snapshots (timestamp, snapshot_data)
        VALUES (?, ?)
    """, (timestamp, snapshot_json))
    
    snapshot_id = cursor.lastrowid
    conn.commit()
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
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT snapshot_data FROM system_state_snapshots
        ORDER BY timestamp DESC
        LIMIT 1
    """)
    
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return None
    
    try:
        return json.loads(row["snapshot_data"])
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Ошибка парсинга snapshot: {e}")
        return None


def cleanup_old_snapshots(keep_last_n: int = 10):
    """
    Удаляет старые снимки, оставляя только последние N.
    
    Args:
        keep_last_n: Количество последних снимков для сохранения
    """
    conn = get_db_connection()
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
    conn.close()
    
    if deleted > 0:
        logger.info(f"Удалено {deleted} старых snapshot'ов")

