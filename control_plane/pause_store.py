"""
Ручная пауза сигнальной торговли, пережившая перезапуск (аудит 29.09.2026).

До этого manual_pause_active жил только в памяти: владелец ставил /pause, а деплой, нехватка памяти или
выход по FATAL поднимали бота уже без паузы. Здесь хранится ТОЛЬКО ручная пауза владельца —
автоматические состояния (SAFE_MODE) при старте не восстанавливаются: в марте 2026 их восстановление из
снимка запускало бота «замороженным».
"""
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _path() -> Path:
    return Path(os.environ.get("MANUAL_PAUSE_FILE", "/data/db/manual_pause"))


def remember(active: bool) -> None:
    """Записать состояние ручной паузы; сбой записи — в лог (пауза в памяти уже стоит)."""
    p = _path()
    try:
        if active:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("paused\n", encoding="utf-8")
        elif p.exists():
            p.unlink()
    except OSError:
        logger.error("ручная пауза: не удалось сохранить состояние в %s", p, exc_info=True)


def remembered() -> bool:
    return _path().exists()
