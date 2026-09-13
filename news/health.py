"""
Проверка здоровья сборщика: `python -m news.health`. Здоров — если последний удачный опрос
источников был не раньше чем MAX_AGE секунд назад (опрос раз в 2 минуты).
"""
import sys
import time
from pathlib import Path

MAX_AGE = 600


def check(path: Path, now: float, max_age: float = MAX_AGE) -> bool:
    try:
        last = float(Path(path).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    return now - last <= max_age


if __name__ == "__main__":
    from news.__main__ import heartbeat_path
    sys.exit(0 if check(heartbeat_path(), time.time()) else 1)
