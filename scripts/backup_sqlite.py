"""
Согласованная копия SQLite на ходу с проверкой целостности.

Запускается внутри контейнера бота (см. deploy/backup.sh):
    python scripts/backup_sqlite.py /data/backups/market_bot-<время>.db

Почему не копирование файла: база работает в режиме WAL, и часть последних
изменений может лежать в market_bot.db-wal. Скопированный отдельно основной файл
— это база без этих изменений, а при неудачном моменте ещё и повреждённая.
VACUUM INTO строит согласованную копию из живой базы, integrity_check её проверяет.
Копия, которую никто не проверял, — это надежда, а не бэкап.
"""
import os
import sqlite3
import sys


def main(target: str) -> int:
    source = os.environ.get("DB_PATH", "/data/db/market_bot.db")
    if not os.path.exists(source):
        print(f"нет базы {source}", file=sys.stderr)
        return 1
    if os.path.exists(target):
        os.remove(target)

    conn = sqlite3.connect(source)
    try:
        conn.execute("VACUUM INTO ?", (target,))
    finally:
        conn.close()

    check = sqlite3.connect(target)
    try:
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        tables = check.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0]
    finally:
        check.close()

    if integrity != "ok":
        print(f"integrity_check копии: {integrity}", file=sys.stderr)
        return 1
    print(f"копия {target}: integrity ok, таблиц {tables}, {os.path.getsize(target)} байт")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("использование: backup_sqlite.py <путь к копии>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
