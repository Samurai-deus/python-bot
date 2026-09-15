"""
Кэш широкой вселенной Bybit (И15–И18, атлас): все бессрочные USDT-контракты старше N дней — таблица
instruments, 4h-свечи и фандинг от листинга; 1h-свечи для top-K по обороту за 24 ч с историей ≥ 2 лет.
Схема — та же, что у history.py (candles / funding) плюс instruments. Кэш — локальный файл вне git
(по умолчанию data/history_wide.db, на машине владельца ≈ 430 МБ).

    py -m backtest.history_wide universe [--db data/history_wide.db] [--min-age-days 365] [--tail-days 3]
    py -m backtest.history_wide hourly   [--db ...] [--top 40]

Повтор `universe` докачивает только новые свечи, а фандинг — хвост за --tail-days (0 — весь период
заново: так закрываются старые дыры, но на 400 контрактах это ≈ 45 минут).
"""
import argparse
import logging
import time
from typing import Dict, List, Optional

from backtest import history
from backtest.wide_search import is_crypto

DAY_MS = 86_400_000
START_MS = 1_577_836_800_000          # 01.01.2020 — раньше linear USDT perp на Bybit не было
NON_CRYPTO_QUOTE = ("USDC",)
logger = logging.getLogger("history_wide")

SCHEMA = ("CREATE TABLE IF NOT EXISTS instruments (symbol TEXT PRIMARY KEY, launch_ms INTEGER, min_qty REAL, tick REAL,"
          " turnover24h REAL, fetched_ms INTEGER)",)


def connect(path):
    conn = history.connect(path)
    for sql in SCHEMA:
        conn.execute(sql)
    return conn


def instruments(api: history.BybitHistory) -> List[Dict]:
    """Все торгуемые linear-контракты Bybit (страницами)."""
    out, cursor = [], ""
    for _ in range(20):
        params = {"category": "linear", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        result = api.get("/v5/market/instruments-info", params)
        out += result.get("list") or []
        cursor = result.get("nextPageCursor") or ""
        if not cursor:
            break
    return out


def turnover24h(api: history.BybitHistory) -> Dict[str, float]:
    rows = api.get("/v5/market/tickers", {"category": "linear"}).get("list") or []
    return {r["symbol"]: float(r.get("turnover24h") or 0) for r in rows}


def perps(items: List[Dict], now_ms: int, min_age_days: int) -> List[Dict]:
    """USDT-perp, торгуется, запущен не позже min_age_days назад; не-крипта (по именам) отбрасывается."""
    return [i for i in items
            if i.get("quoteCoin") == "USDT" and i.get("contractType") == "LinearPerpetual" and i.get("status") == "Trading"
            and int(i.get("launchTime") or 0) and now_ms - int(i["launchTime"]) >= min_age_days * DAY_MS
            and is_crypto(i["symbol"])]


def save_instruments(conn, items: List[Dict], turnover: Dict[str, float], now_ms: int) -> None:
    conn.executemany("INSERT OR REPLACE INTO instruments VALUES (?, ?, ?, ?, ?, ?)",
                     [(i["symbol"], int(i["launchTime"]), float(i["lotSizeFilter"]["minOrderQty"]),
                       float(i["priceFilter"]["tickSize"]), turnover.get(i["symbol"], 0.0), now_ms) for i in items])
    conn.commit()


def sync_universe(conn, api: history.BybitHistory, min_age_days: int = 365, tail_days: Optional[int] = 3,
                  now_ms: Optional[int] = None, progress=None) -> Dict[str, int]:
    now_ms = now_ms or int(time.time() * 1000)
    items = perps(instruments(api), now_ms, min_age_days)
    turnover = turnover24h(api)
    save_instruments(conn, items, turnover, now_ms)
    items.sort(key=lambda i: -turnover.get(i["symbol"], 0.0))
    end_ms = now_ms - now_ms % (4 * 3_600_000)
    totals = {"instruments": len(items), "candles": 0, "funding": 0}
    for n, i in enumerate(items, 1):
        start = max(START_MS, int(i["launchTime"]) - DAY_MS)
        totals["candles"] += history.sync_candles(conn, api, i["symbol"], "4h", start, end_ms)
        totals["funding"] += history.sync_funding(conn, api, i["symbol"], start, end_ms, tail_days=tail_days or None)
        if progress and n % 25 == 0:
            progress(n, len(items), api.requests)
    return totals


def hourly_symbols(conn, top: int, now_ms: int, min_age_days: int = 730) -> List[str]:
    rows = conn.execute("SELECT symbol FROM instruments WHERE launch_ms <= ? ORDER BY turnover24h DESC",
                        (now_ms - min_age_days * DAY_MS,)).fetchall()
    return [r[0] for r in rows if is_crypto(r[0])][:top]


def sync_hourly(conn, api: history.BybitHistory, top: int = 40, now_ms: Optional[int] = None) -> Dict[str, int]:
    now_ms = now_ms or int(time.time() * 1000)
    end_ms = now_ms - now_ms % 3_600_000
    symbols = hourly_symbols(conn, top, now_ms)
    added = 0
    for s in symbols:
        launch = conn.execute("SELECT launch_ms FROM instruments WHERE symbol = ?", (s,)).fetchone()[0]
        added += history.sync_candles(conn, api, s, "1h", max(START_MS, int(launch) - DAY_MS), end_ms)
    return {"symbols": len(symbols), "candles": added}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Кэш широкой вселенной Bybit")
    sub = parser.add_subparsers(dest="command", required=True)
    u = sub.add_parser("universe")
    u.add_argument("--db", default=str(history.DEFAULT_DB.with_name("history_wide.db")))
    u.add_argument("--min-age-days", type=int, default=365)
    u.add_argument("--tail-days", type=int, default=3, help="фандинг: докачивать только хвост за N дней; 0 — весь период")
    h = sub.add_parser("hourly")
    h.add_argument("--db", default=str(history.DEFAULT_DB.with_name("history_wide.db")))
    h.add_argument("--top", type=int, default=40)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    conn = connect(args.db)
    api = history.BybitHistory()
    if args.command == "universe":
        totals = sync_universe(conn, api, args.min_age_days, args.tail_days,
                               progress=lambda n, total, req: logger.info("%d/%d, запросов %d", n, total, req))
    else:
        totals = sync_hourly(conn, api, args.top)
    print("new rows:", totals, "requests:", api.requests)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
