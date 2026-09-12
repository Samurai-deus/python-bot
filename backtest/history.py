"""
История Bybit для проверки на истории (Ф1 плана трейдера, docs/TRADER_PLAN.md).

Свечи, фандинг и открытый интерес публичных эндпоинтов Bybit — в локальный кэш SQLite
(по умолчанию data/history.db, вне git: *.db в .gitignore). Загрузка докачивает: при
повторном запуске запросы идут только за то, чего в кэше ещё нет. Хранятся только
закрытые свечи — незакрытая в проверку на истории попасть не должна.

Запуск: py -m backtest.history sync --months 12
"""
import argparse
import logging
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

BASE_URL = "https://api.bybit.com"
DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "history.db"
# Таймфрейм бота → интервал Bybit и длина свечи в мс
INTERVALS: Dict[str, Tuple[str, int]] = {
    "1m": ("1", 60_000),
    "5m": ("5", 300_000),
    "15m": ("15", 900_000),
    "30m": ("30", 1_800_000),
    "1h": ("60", 3_600_000),
    "4h": ("240", 14_400_000),
}
KLINE_LIMIT = 1000
FUNDING_LIMIT = 200
OI_LIMIT = 200
MIN_REQUEST_GAP = 0.12                  # ≈ 8 запросов в секунду — с запасом до лимита Bybit
RETRIES = 5
RATE_LIMIT_CODES = {10006, 10018}


class BybitHistory:
    """Запросы к публичным эндпоинтам с паузой между запросами и повтором при лимите."""

    def __init__(self, session=None, sleep=time.sleep, clock=time.monotonic):
        if session is None:
            import requests
            session = requests.Session()
        self.session = session
        self._sleep = sleep
        self._clock = clock
        self._last = None
        self.requests = 0

    def get(self, path: str, params: Dict) -> Dict:
        for attempt in range(RETRIES):
            if self._last is not None:
                wait = MIN_REQUEST_GAP - (self._clock() - self._last)
                if wait > 0:
                    self._sleep(wait)
            self._last = self._clock()
            self.requests += 1
            try:
                response = self.session.get(BASE_URL + path, params=params, timeout=15)
                data = response.json()
            except Exception as exc:
                logger.warning("Bybit %s: %s — повтор %d", path, type(exc).__name__, attempt + 1)
                self._sleep(2 ** attempt)
                continue
            code = data.get("retCode")
            if code == 0:
                return data.get("result") or {}
            if code in RATE_LIMIT_CODES:
                self._sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Bybit {path}: {code} {data.get('retMsg')}")
        raise RuntimeError(f"Bybit {path}: не ответил за {RETRIES} попыток")


# ---------------------------------------------------------------------------
# Кэш
# ---------------------------------------------------------------------------

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS candles (symbol TEXT NOT NULL, interval TEXT NOT NULL, ts INTEGER NOT NULL,"
    " open REAL, high REAL, low REAL, close REAL, volume REAL, turnover REAL,"
    " PRIMARY KEY (symbol, interval, ts))",
    "CREATE TABLE IF NOT EXISTS funding (symbol TEXT NOT NULL, ts INTEGER NOT NULL, rate REAL,"
    " PRIMARY KEY (symbol, ts))",
    "CREATE TABLE IF NOT EXISTS open_interest (symbol TEXT NOT NULL, interval TEXT NOT NULL, ts INTEGER NOT NULL,"
    " value REAL, PRIMARY KEY (symbol, interval, ts))",
)


def connect(path=DEFAULT_DB) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    for statement in SCHEMA:
        conn.execute(statement)
    return conn


def _latest(conn, sql: str, params: Sequence) -> Optional[int]:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row and row[0] is not None else None


# ---------------------------------------------------------------------------
# Загрузка
# ---------------------------------------------------------------------------

def _page_candles(conn, api: BybitHistory, symbol: str, timeframe: str, cursor: int, end_ms: int) -> int:
    """Закрытые свечи [cursor, end_ms) — вперёд страницами; возвращает число новых."""
    interval, step = INTERVALS[timeframe]
    added = 0
    while cursor < end_ms:
        result = api.get("/v5/market/kline", {"category": "linear", "symbol": symbol, "interval": interval,
                                              "start": cursor, "limit": KLINE_LIMIT})
        rows = sorted(result.get("list") or [], key=lambda r: int(r[0]))
        closed = [r for r in rows if cursor <= int(r[0]) and int(r[0]) + step <= end_ms]
        conn.executemany(
            "INSERT OR IGNORE INTO candles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(symbol, timeframe, int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]),
              float(r[5]), float(r[6]) if len(r) > 6 else None) for r in closed],
        )
        conn.commit()
        added += len(closed)
        if not rows:
            break  # символа ещё не было — дальше пусто до его листинга не бывает
        last = int(rows[-1][0])
        if last + step <= cursor:
            break
        cursor = last + step
        if len(rows) < KLINE_LIMIT and cursor + step > end_ms:
            break
    return added


def sync_candles(conn, api: BybitHistory, symbol: str, timeframe: str, start_ms: int, end_ms: int) -> int:
    """
    Закрытые свечи [start_ms, end_ms) с докачкой; возвращает число новых. Если период
    начинается раньше кэша — сначала докачивается начало (назад до первой свечи кэша).
    """
    step = INTERVALS[timeframe][1]
    first = _latest(conn, "SELECT MIN(ts) FROM candles WHERE symbol = ? AND interval = ?", (symbol, timeframe))
    have = _latest(conn, "SELECT MAX(ts) FROM candles WHERE symbol = ? AND interval = ?", (symbol, timeframe))
    if have is None:
        return _page_candles(conn, api, symbol, timeframe, start_ms, end_ms)
    added = _page_candles(conn, api, symbol, timeframe, start_ms, first) if start_ms < first else 0
    return added + _page_candles(conn, api, symbol, timeframe, max(start_ms, have + step), end_ms)


def sync_funding(conn, api: BybitHistory, symbol: str, start_ms: int, end_ms: int) -> int:
    """
    Ставки фандинга [start_ms, end_ms]: листаем назад от конца по полученным записям.
    Биржа отдаёт последние ≤200 записей до endTime, а интервал выплат у монеты бывает 8, 4
    и 1 ч и меняется со временем — окно фиксированной длины теряло записи. Период берётся
    целиком каждый раз (запросов мало), так заполняются и старые дыры. Возвращает число новых.
    """
    before = conn.total_changes
    cursor_end = end_ms
    while True:
        result = api.get("/v5/market/funding/history", {"category": "linear", "symbol": symbol,
                                                        "endTime": cursor_end, "limit": FUNDING_LIMIT})
        rows = result.get("list") or []
        stamps = [int(r["fundingRateTimestamp"]) for r in rows]
        conn.executemany("INSERT OR IGNORE INTO funding VALUES (?, ?, ?)",
                         [(symbol, ts, float(r["fundingRate"])) for ts, r in zip(stamps, rows) if start_ms <= ts <= end_ms])
        conn.commit()
        if len(rows) < FUNDING_LIMIT or min(stamps) <= start_ms:
            break
        cursor_end = min(stamps) - 1
    return conn.total_changes - before


def _page_open_interest(conn, api: BybitHistory, symbol: str, interval: str, begin: int, end_ms: int) -> int:
    added = 0
    cursor = None
    while True:
        params = {"category": "linear", "symbol": symbol, "intervalTime": interval,
                  "startTime": begin, "endTime": end_ms, "limit": OI_LIMIT}
        if cursor:
            params["cursor"] = cursor
        result = api.get("/v5/market/open-interest", params)
        rows = result.get("list") or []
        conn.executemany("INSERT OR IGNORE INTO open_interest VALUES (?, ?, ?, ?)",
                         [(symbol, interval, int(r["timestamp"]), float(r["openInterest"])) for r in rows])
        conn.commit()
        added += len(rows)
        cursor = result.get("nextPageCursor")
        if not rows or not cursor:
            break
    return added


def sync_open_interest(conn, api: BybitHistory, symbol: str, interval: str, start_ms: int, end_ms: int) -> int:
    """interval — как у Bybit: 5min, 15min, 30min, 1h, 4h, 1d. Период раньше кэша докачивается назад."""
    sql = "SELECT {}(ts) FROM open_interest WHERE symbol = ? AND interval = ?"
    first = _latest(conn, sql.format("MIN"), (symbol, interval))
    have = _latest(conn, sql.format("MAX"), (symbol, interval))
    if have is None:
        return _page_open_interest(conn, api, symbol, interval, start_ms, end_ms)
    added = _page_open_interest(conn, api, symbol, interval, start_ms, first - 1) if start_ms < first else 0
    return added + _page_open_interest(conn, api, symbol, interval, max(start_ms, have + 1), end_ms)


# ---------------------------------------------------------------------------
# Чтение и контроль
# ---------------------------------------------------------------------------

def load_candles(conn, symbol: str, timeframe: str, start_ms: int, end_ms: int) -> List[list]:
    """
    Свечи [ts, open, high, low, close, volume, turnover] по возрастанию времени — та же
    форма ряда, что у data_loader.get_candles (Bybit, развёрнутый от старых к новым).
    """
    return [list(r) for r in conn.execute(
        "SELECT ts, open, high, low, close, volume, turnover FROM candles WHERE symbol = ? AND interval = ?"
        " AND ts >= ? AND ts < ? ORDER BY ts", (symbol, timeframe, start_ms, end_ms))]


def find_gaps(conn, symbol: str, timeframe: str) -> List[Tuple[int, int]]:
    """Пропуски в ряду свечей: пары (последняя перед пропуском, первая после)."""
    step = INTERVALS[timeframe][1]
    stamps = [r[0] for r in conn.execute(
        "SELECT ts FROM candles WHERE symbol = ? AND interval = ? ORDER BY ts", (symbol, timeframe))]
    return [(a, b) for a, b in zip(stamps, stamps[1:]) if b - a != step]


def sync(conn, api: BybitHistory, symbols: Iterable[str], timeframes: Iterable[str], months: int,
         oi_interval: Optional[str] = None, now: Optional[datetime] = None) -> Dict[str, int]:
    now = now or datetime.now(UTC)
    end_ms = int(now.timestamp() * 1000)
    start_ms = int((now - timedelta(days=30 * months)).timestamp() * 1000)
    totals = {"candles": 0, "funding": 0, "open_interest": 0}
    for symbol in symbols:
        for timeframe in timeframes:
            totals["candles"] += sync_candles(conn, api, symbol, timeframe, start_ms, end_ms)
        totals["funding"] += sync_funding(conn, api, symbol, start_ms, end_ms)
        if oi_interval:
            totals["open_interest"] += sync_open_interest(conn, api, symbol, oi_interval, start_ms, end_ms)
        logger.info("%s: готово, запросов всего %d", symbol, api.requests)
    return totals


def main(argv=None) -> int:
    import config
    parser = argparse.ArgumentParser(description="История Bybit в локальный кэш")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("sync")
    run.add_argument("--months", type=int, default=12)
    run.add_argument("--db", default=str(DEFAULT_DB))
    run.add_argument("--symbols", default=",".join(config.SYMBOLS))
    run.add_argument("--timeframes", default="5m,15m,30m,1h,4h")
    run.add_argument("--oi", default="5min", help="интервал открытого интереса; пусто — не качать")
    gaps = sub.add_parser("gaps")
    gaps.add_argument("--db", default=str(DEFAULT_DB))
    gaps.add_argument("--timeframe", default="5m")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    conn = connect(args.db)
    if args.command == "sync":
        totals = sync(conn, BybitHistory(), args.symbols.split(","), args.timeframes.split(","), args.months,
                      oi_interval=args.oi or None)
        print("new rows:", totals)
    else:
        for (symbol,) in conn.execute("SELECT DISTINCT symbol FROM candles ORDER BY symbol"):
            holes = find_gaps(conn, symbol, args.timeframe)
            print(symbol, "gaps:", len(holes), holes[:3])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
