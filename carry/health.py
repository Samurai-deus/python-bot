"""
Проверка здоровья исполнителя И13: `python -m carry.health`. Здоров — если последний цикл был не
раньше чем MAX_AGE секунд назад (цикл раз в час).
"""
import sys
import time
from pathlib import Path

MAX_AGE = 9000


def check(path: Path, now: float, max_age: float = MAX_AGE) -> bool:
    try:
        last = float(Path(path).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    return now - last <= max_age


if __name__ == "__main__":
    from carry.__main__ import carry_dir
    sys.exit(0 if check(carry_dir() / "heartbeat", time.time()) else 1)
