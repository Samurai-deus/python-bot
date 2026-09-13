"""
Хранение записи: сжатые часовые файлы <root>/<вид>/<символ>/<ГГГГ-ММ-ДД>/<ЧЧ>.jsonl.gz (UTC),
журнал событий <root>/events.jsonl и предел занятого места — удаляются самые старые часы.

Файл часа открывается на дозапись: после перезапуска в тот же час в gzip добавляется новый
член — такой файл читается обычным gzip целиком.
"""
import gzip
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

PATTERN = "*/*/*/*.jsonl.gz"


class HourlyWriter:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.files: Dict[Tuple[str, str], Tuple[Path, object]] = {}

    def path(self, kind: str, symbol: str, t_ms: int) -> Path:
        dt = datetime.fromtimestamp(t_ms / 1000, UTC)
        return self.root / kind / symbol / f"{dt:%Y-%m-%d}" / f"{dt:%H}.jsonl.gz"

    def write(self, kind: str, symbol: str, t_ms: int, record: dict) -> None:
        p = self.path(kind, symbol, t_ms)
        cur = self.files.get((kind, symbol))
        if cur is None or cur[0] != p:
            if cur is not None:
                cur[1].close()
            p.parent.mkdir(parents=True, exist_ok=True)
            cur = (p, gzip.open(p, "at", encoding="utf-8"))
            self.files[(kind, symbol)] = cur
        cur[1].write(json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n")

    def flush(self) -> None:
        for _, fh in self.files.values():
            fh.flush()

    def open_paths(self) -> Set[Path]:
        return {p for p, _ in self.files.values()}

    def close(self) -> None:
        for _, fh in self.files.values():
            fh.close()
        self.files = {}


def log_event(root: Path, event: str, **fields) -> None:
    """Строка в журнал событий: подключение, обрыв, разрыв последовательности стакана."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    line = {"t": int(time.time() * 1000), "event": event, **fields}
    with open(root / "events.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def _chronological(files: Iterable[Path]) -> List[Path]:
    return sorted(files, key=lambda p: (p.parent.name, p.name, str(p)))


def enforce_cap(root: Path, cap_bytes: int, keep: Set[Path]) -> List[Path]:
    """Удаляет самые старые часовые файлы, пока занято больше cap_bytes; открытые (keep) не трогает."""
    root = Path(root)
    files = _chronological(root.glob(PATTERN))
    sizes = {p: p.stat().st_size for p in files}
    total = sum(sizes.values())
    removed = []
    for p in files:
        if total <= cap_bytes:
            break
        if p in keep:
            continue
        total -= sizes[p]
        p.unlink()
        removed.append(p)
    for day in {p.parent for p in removed}:
        if day.exists() and not any(day.iterdir()):
            day.rmdir()
    return removed
