"""Проверка здоровья исполнителя И14: `python -m portfolio.health` — пульс не старше MAX_AGE с (цикл раз в час)."""
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
    from portfolio.__main__ import root_dir
    sys.exit(0 if check(root_dir() / "heartbeat", time.time()) else 1)
