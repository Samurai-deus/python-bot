"""
Docker healthcheck бота. Exit 0 — здоров, 1 — нет; причина уходит в stderr, видна
в `docker inspect` (State.Health.Log), и её же пересказывает владельцу сторож
хоста (deploy/watchdog.sh).

Проверяет:
  1. база отвечает на SELECT 1;
  2. метка heartbeat свежее HEARTBEAT_MAX_AGE — цикл событий не заблокирован;
  3. метка цикла анализа свежее analysis_max_age() — торговый цикл идёт.

До 10.09.2026 проверялся только пункт 1: бот с зависшим циклом считался здоровым.
"""
import os
import sys

# Скрипт запускают как `python scripts/healthcheck.py`: в sys.path попадает
# каталог scripts/, а не корень приложения, где лежит пакет utils.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.liveness import age  # noqa: E402

# Удар каждые 10 с; ThreadWatchdog сам гасит процесс после 30 с тишины.
HEARTBEAT_MAX_AGE = 60.0


def analysis_max_age() -> float:
    """
    Два самых длинных интервала анализа с запасом. Интервал адаптивный: при
    ошибках и низкой волатильности растёт до ADAPTIVE_INTERVAL_MAX.
    """
    base = float(os.environ.get("BOT_INTERVAL", "300"))
    longest = max(base, float(os.environ.get("ADAPTIVE_INTERVAL_MAX", "900")))
    return 2 * longest + 120


def check_database() -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if database_url:
        import psycopg2
        conn = psycopg2.connect(database_url)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
    else:
        import sqlite3
        conn = sqlite3.connect(os.environ.get("DB_PATH", "market_bot.db"))
        conn.execute("SELECT 1")
        conn.close()


def check_liveness(now=None) -> None:
    beat = age("heartbeat", now)
    if beat is None:
        raise RuntimeError("нет метки heartbeat: цикл событий не дошёл до первого удара")
    if beat > HEARTBEAT_MAX_AGE:
        raise RuntimeError(f"heartbeat {beat:.0f} с назад (порог {HEARTBEAT_MAX_AGE:.0f} с): цикл событий стоит")

    limit = analysis_max_age()
    turn = age("analysis", now)
    if turn is None:
        raise RuntimeError("нет метки цикла анализа: цикл не запускался")
    if turn > limit:
        raise RuntimeError(f"цикл анализа не оборачивался {turn:.0f} с (порог {limit:.0f} с)")


def check() -> None:
    check_database()
    check_liveness()


if __name__ == "__main__":
    try:
        check()
        sys.exit(0)
    except Exception as e:
        print(f"Healthcheck failed: {e}", file=sys.stderr)
        sys.exit(1)
