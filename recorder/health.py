"""
Проверка здоровья контейнера записи: `python -m recorder.health`. Здоров — если последнее
сообщение биржи пришло не раньше чем MAX_AGE секунд назад (пульс пишет recorder каждые 30 с).
"""
import os
import sys
import time
from pathlib import Path

MAX_AGE = 120


def check(root: Path, now: float, max_age: float = MAX_AGE) -> bool:
    try:
        last = float((Path(root) / "heartbeat").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    return now - last <= max_age


if __name__ == "__main__":
    sys.exit(0 if check(Path(os.environ.get("RECORDER_DIR", "/recorder")), time.time()) else 1)
