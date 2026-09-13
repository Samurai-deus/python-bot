"""
Запуск записи: `python -m recorder`. Настройки — переменные окружения:
RECORDER_DIR (/recorder), RECORDER_SYMBOLS (BTC, ETH, SOL, XRP, DOGE, BNB, XAU в USDT),
RECORDER_CAP_GB (20).

Публичный поток Bybit linear: orderbook.50 и publicTrade по каждому символу. Раз в секунду —
верхние 20 уровней каждого достоверного стакана, сделки — все. Разрыв последовательности
стакана, обрыв или ошибка — событие в журнал и переподключение (биржа пришлёт новый снимок);
пауза растёт до минуты. Пульс для проверки здоровья — файл heartbeat со временем последнего
сообщения биржи.
"""
import asyncio
import json
import os
import signal
import time
from pathlib import Path
from typing import Dict, List, Sequence

from recorder.book import GAP, OrderBook
from recorder.storage import HourlyWriter, enforce_cap, log_event

URL = "wss://stream.bybit.com/v5/public/linear"
DEFAULT_SYMBOLS = "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,XAUUSDT"
DEPTH = 50
TOP = 20
SAMPLE_SEC = 1.0
FLUSH_SEC = 30
CAP_CHECK_SEC = 600
TOPICS_PER_REQUEST = 10


def topics(symbols: Sequence[str]) -> List[str]:
    return [t for s in symbols for t in (f"orderbook.{DEPTH}.{s}", f"publicTrade.{s}")]


def chunks(items: Sequence[str], n: int = TOPICS_PER_REQUEST) -> List[List[str]]:
    return [list(items[i:i + n]) for i in range(0, len(items), n)]


class Recorder:
    def __init__(self, symbols: Sequence[str], writer: HourlyWriter, clock=time.time) -> None:
        self.books: Dict[str, OrderBook] = {s: OrderBook() for s in symbols}
        self.writer = writer
        self.clock = clock
        self.last_message = 0.0

    def handle(self, msg: dict) -> bool:
        """Разбор сообщения биржи. False — разрыв последовательности стакана: нужно переподключение."""
        topic = msg.get("topic") or ""
        if topic.startswith("orderbook."):
            book = self.books.get(topic.rsplit(".", 1)[-1])
            if book is not None and book.apply(msg.get("type", ""), msg.get("data") or {}) == GAP:
                return False
        elif topic.startswith("publicTrade."):
            for tr in msg.get("data") or []:
                self.writer.write("trades", tr["s"], int(tr["T"]),
                                  {"T": int(tr["T"]), "S": tr["S"], "p": tr["p"], "v": tr["v"], "i": tr["i"]})
        if topic:
            self.last_message = self.clock()
        return True

    def sample(self, t_ms: int) -> None:
        """Снимок верхних TOP уровней каждого достоверного стакана."""
        for symbol, book in self.books.items():
            if book.ready:
                bids, asks = book.top(TOP)
                self.writer.write("book", symbol, t_ms, {"t": t_ms, "u": book.update_id, "b": bids, "a": asks})

    def reset(self) -> None:
        for book in self.books.values():
            book.reset()


async def _sampler(rec: Recorder) -> None:
    while True:
        await asyncio.sleep(SAMPLE_SEC - (time.time() % SAMPLE_SEC))
        rec.sample(int(time.time() * 1000))


async def _housekeeping(rec: Recorder, root: Path, cap_bytes: int) -> None:
    last_cap = 0.0
    while True:
        await asyncio.sleep(FLUSH_SEC)
        rec.writer.flush()
        (root / "heartbeat").write_text(f"{rec.last_message:.0f}\n", encoding="utf-8")
        if time.time() - last_cap >= CAP_CHECK_SEC:
            last_cap = time.time()
            removed = enforce_cap(root, cap_bytes, rec.writer.open_paths())
            if removed:
                log_event(root, "cap", removed=len(removed))


async def run(root: Path, symbols: Sequence[str], cap_bytes: int) -> None:
    """Задачи записи до SIGTERM/SIGINT; при остановке часовые файлы закрываются целыми."""
    root.mkdir(parents=True, exist_ok=True)
    rec = Recorder(symbols, HourlyWriter(root))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):  # Windows: только для локального запуска
            pass
    tasks = [asyncio.create_task(c) for c in (_sampler(rec), _housekeeping(rec, root, cap_bytes),
                                              _connection(rec, root, symbols))]
    await stop.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    rec.writer.close()
    log_event(root, "stop")


async def _connection(rec: Recorder, root: Path, symbols: Sequence[str]) -> None:
    import aiohttp
    backoff = 1
    while True:
        reason = "closed"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(URL, heartbeat=20, receive_timeout=60) as ws:
                    for part in chunks(topics(symbols)):
                        await ws.send_json({"op": "subscribe", "args": part})
                    log_event(root, "connect", symbols=list(symbols))
                    backoff = 1
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            reason = f"ws {msg.type.name}"
                            break
                        if not rec.handle(json.loads(msg.data)):
                            reason = "gap"
                            break
        except Exception as exc:  # сеть, биржа, разбор — всё ведёт к переподключению
            reason = f"{type(exc).__name__}: {exc}"[:200]
        rec.reset()
        log_event(root, "disconnect", reason=reason, retry_in=backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)


def main() -> None:
    root = Path(os.environ.get("RECORDER_DIR", "/recorder"))
    symbols = [s for s in os.environ.get("RECORDER_SYMBOLS", DEFAULT_SYMBOLS).split(",") if s]
    cap_bytes = int(float(os.environ.get("RECORDER_CAP_GB", "20")) * 1024 ** 3)
    asyncio.run(run(root, symbols, cap_bytes))


if __name__ == "__main__":
    main()
