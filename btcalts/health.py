"""Проверка здоровья исполнителя И18: `python -m btcalts.health` — пульс не старше MAX_AGE с (цикл раз в час)."""
import sys
import time

from portfolio.health import MAX_AGE, check  # noqa: F401 — та же проверка, свой каталог

if __name__ == "__main__":
    from btcalts.__main__ import root_dir
    sys.exit(0 if check(root_dir() / "heartbeat", time.time()) else 1)
