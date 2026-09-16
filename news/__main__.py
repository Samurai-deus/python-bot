"""
Запуск сборщика: `python -m news`. Раз в POLL_SEC — опрос источников, запись новых заголовков
с моментом, когда сборщик их впервые увидел, затем оценка неоценённых моделью. Пульс для
проверки здоровья — файл NEWS_HEARTBEAT: время последнего опроса, на который ответил хоть
один источник.

Заголовок, опубликованный раньше чем за STALE_MS до первого взгляда (накопленный в ленте
к запуску или пришедший с опозданием), помечается backlog: время реакции на него неизвестно,
модель его не оценивает и в проверку И10 он не входит.

Тот же цикл раз в неделю записывает предложение монет с CoinGecko (news/supply.py).
"""
import logging
import os
import signal
import threading
import time
from pathlib import Path
from typing import Tuple

import httpx

from news import blind, scorer, sources, supply

POLL_SEC = 120
STALE_MS = 30 * 60 * 1000
USER_AGENT = "Mozilla/5.0 (market-bot news collector)"

logger = logging.getLogger("news")


def heartbeat_path() -> Path:
    return Path(os.environ.get("NEWS_HEARTBEAT", "/tmp/news-heartbeat"))


def poll_once(http, now_ms: int) -> Tuple[int, int]:
    """Опрос источников: (добавлено новых заголовков, ответивших источников)."""
    import database
    items, ok = sources.fetch_all(http)
    added = database.save_news_items(items, now_ms, STALE_MS) if items else 0
    if ok:
        heartbeat_path().write_text(f"{now_ms / 1000:.0f}\n", encoding="utf-8")
    return added, ok


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    with httpx.Client(timeout=20, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as http:
        while not stop.is_set():
            try:
                added, ok = poll_once(http, int(time.time() * 1000))
                scored = scorer.score_pending()
                blinded = blind.score_pending()  # И10б — после И10, чтобы не отнимать у неё бюджет
                logger.info("опрос: источников %d из %d, новых %d, оценено %d, без названия %d",
                            ok, len(sources.SOURCES), added, scored, blinded)
                done = supply.maybe_record(http, int(time.time() * 1000))
                if done:
                    logger.info("предложение монет за неделю записано: монет %d, контрактов Bybit покрыто %d из %d",
                                done["coins"], done["matched"], done["bases"])
            except Exception:  # база, модель — цикл продолжается, пульс покажет долгий сбой
                logger.warning("цикл сборщика не удался", exc_info=True)
            stop.wait(POLL_SEC)


if __name__ == "__main__":
    main()
