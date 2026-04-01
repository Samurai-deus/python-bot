"""
Docker healthcheck script.

Detects database mode (PostgreSQL or SQLite) and runs SELECT 1.
Exit code 0 = healthy, 1 = unhealthy.
"""
import os
import sys


def check():
    database_url = os.environ.get("DATABASE_URL", "")
    if database_url:
        # PostgreSQL mode
        import psycopg2
        conn = psycopg2.connect(database_url)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
    else:
        # SQLite mode
        import sqlite3
        db_path = os.environ.get("DB_PATH", "market_bot.db")
        conn = sqlite3.connect(db_path)
        conn.execute("SELECT 1")
        conn.close()


if __name__ == "__main__":
    try:
        check()
        sys.exit(0)
    except Exception as e:
        print(f"Healthcheck failed: {e}", file=sys.stderr)
        sys.exit(1)
