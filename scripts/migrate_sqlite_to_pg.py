"""
Миграция данных из SQLite в PostgreSQL.

Запуск (один раз, перед переходом на PostgreSQL):

    # На сервере:
    cd /root/market_bot
    source venv/bin/activate
    python scripts/migrate_sqlite_to_pg.py --sqlite /data/db/market_bot.db

    # С явным DATABASE_URL:
    DATABASE_URL=postgresql://user:pass@host:5432/db python scripts/migrate_sqlite_to_pg.py

Скрипт:
  1. Читает все таблицы из SQLite
  2. Создаёт схему в PostgreSQL (идемпотентно)
  3. Вставляет строки пачками по 500 (skip duplicates — ON CONFLICT DO NOTHING)
  4. Печатает итоговую статистику

Безопасен для повторного запуска: ON CONFLICT DO NOTHING не создаёт дублей.
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


TABLES = [
    "trades",
    "system_state_snapshots",
    "orders",
    "positions",
    "pnl_history",
    "pnl_records",
    "user_settings",
    "signal_outcomes",
    "encrypted_api_keys",
]

BATCH_SIZE = 500


def migrate(sqlite_path: str, database_url: str) -> None:
    print(f"[migrate] Source SQLite: {sqlite_path}")
    print(f"[migrate] Target PostgreSQL: {database_url[:30]}...")

    if not Path(sqlite_path).exists():
        print(f"[ERROR] SQLite file not found: {sqlite_path}")
        sys.exit(1)

    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        print("[ERROR] psycopg2-binary not installed. Run: pip install psycopg2-binary")
        sys.exit(1)

    # ---- Source: SQLite ----
    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row

    # ---- Target: PostgreSQL ----
    pg = psycopg2.connect(database_url)
    pg.autocommit = False

    # Ensure PG schema exists (creates tables if not present)
    os.environ["DATABASE_URL"] = database_url
    import database as db_module

    pg.commit()  # schema was committed inside _init_pg_schema

    totals: dict[str, int] = {}

    for table in TABLES:
        # Check if table exists in SQLite
        src_cur = src.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        )
        if not src_cur.fetchone():
            print(f"  [{table}] not found in SQLite — skipping")
            totals[table] = 0
            continue

        # Get columns from SQLite
        pragma_cur = src.execute(f"PRAGMA table_info({table})")
        columns = [row[1] for row in pragma_cur.fetchall()]
        if not columns:
            print(f"  [{table}] no columns — skipping")
            totals[table] = 0
            continue

        # Get rows from SQLite
        all_rows = src.execute(f"SELECT * FROM {table}").fetchall()
        if not all_rows:
            print(f"  [{table}] 0 rows — nothing to migrate")
            totals[table] = 0
            continue

        # Build INSERT with ON CONFLICT DO NOTHING
        col_list = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(columns))
        insert_sql = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
            f" ON CONFLICT DO NOTHING"
        )

        pg_cur = pg.cursor()
        inserted = 0

        # Batch insert
        for i in range(0, len(all_rows), BATCH_SIZE):
            batch = [tuple(row) for row in all_rows[i : i + BATCH_SIZE]]
            psycopg2.extras.execute_batch(pg_cur, insert_sql, batch)
            inserted += len(batch)

        pg.commit()
        print(f"  [{table}] {len(all_rows)} rows → {inserted} processed (duplicates skipped)")
        totals[table] = len(all_rows)

    src.close()
    pg.close()

    print("\n[migrate] Done!")
    total_rows = sum(totals.values())
    print(f"  Total rows processed: {total_rows}")
    for t, n in totals.items():
        if n:
            print(f"    {t}: {n}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate SQLite → PostgreSQL")
    parser.add_argument(
        "--sqlite",
        default=os.environ.get("DB_PATH", "market_bot.db"),
        help="Path to SQLite database file (default: market_bot.db or DB_PATH env)",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="PostgreSQL connection string (default: DATABASE_URL env)",
    )
    args = parser.parse_args()

    if not args.database_url:
        print("[ERROR] DATABASE_URL is not set. Pass --database-url or set DATABASE_URL env.")
        sys.exit(1)

    migrate(args.sqlite, args.database_url)


if __name__ == "__main__":
    main()
