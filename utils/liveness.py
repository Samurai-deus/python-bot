"""
Метки жизни бота для healthcheck контейнера.

Раньше healthcheck делал SELECT 1 к SQLite и считал бот здоровым, пока открывается
файл базы: зависший цикл событий или остановившийся цикл анализа выглядели бы
«healthy», и сторож на хосте (deploy/watchdog.sh) молчал бы. Теперь бот ставит
две метки:

  heartbeat — каждые 10 с из runtime_heartbeat_loop: цикл событий не заблокирован;
  analysis  — при старте и после каждого оборота цикла анализа: торговый цикл идёт.

Метка — файл с unix-временем. Каталог внутри контейнера (/tmp): healthcheck
запускается в том же контейнере, а после перезапуска меток нет, и первые удары
healthcheck ждёт в пределах start_period.
"""
import logging
import os
import pathlib
import time
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_DIR = "/tmp/market-bot-liveness"
_REPLACE_ATTEMPTS = 3
_REPLACE_PAUSE_SECONDS = 0.05


def _dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("LIVENESS_DIR") or DEFAULT_DIR)


def _replace(src: pathlib.Path, dst: pathlib.Path) -> None:
    """
    os.replace с повтором. На Windows антивирус на мгновение держит только что
    записанный файл, и замена получает WinError 5 (PermissionError): тест метки
    падал примерно раз из трёх. На Linux (прод, CI) повтор не срабатывает.
    """
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_PAUSE_SECONDS)


def mark(name: str, now: Optional[float] = None) -> None:
    """Ставит метку. Сбой записи не роняет бот: пропавшую метку заметит healthcheck."""
    try:
        directory = _dir()
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / f".{name}.tmp"
        tmp.write_text(f"{time.time() if now is None else now:.3f}", encoding="ascii")
        _replace(tmp, directory / name)
    except OSError as exc:
        logger.warning("liveness: не удалось записать метку %s: %s", name, exc)


def age(name: str, now: Optional[float] = None) -> Optional[float]:
    """Сколько секунд назад ставилась метка; None — метки нет или она испорчена."""
    try:
        stamp = float((_dir() / name).read_text(encoding="ascii"))
    except (OSError, ValueError):
        return None
    return (time.time() if now is None else now) - stamp
