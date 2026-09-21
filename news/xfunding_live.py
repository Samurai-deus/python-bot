"""
И19 вперёд (docs/TRADER_PLAN.md): раз в час снимок трёх бирж по общим USDT-бессрочным контрактам — текущая
(расчётная) ставка фандинга, время следующей выплаты, интервал, лучшие цены, оборот за сутки. Своя база
(XFUNDING_LIVE_DB, по умолчанию /data/db/xfunding_live.db), не база бота: ≈ 40 тыс. строк в сутки.

Снимок — в первые минуты часа (UTC), один на час; биржа, не ответившая в этот раз, пропускается, остальные
пишутся. В истории у бирж есть только выплаченные ставки — здесь записывается то, что видно в момент решения:
через 4 и 8 недель по этим данным проверяется время жизни расхождений и само правило И19.
"""
import logging
import os
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

HOUR_MS = 3_600_000
SCHEMA = ("CREATE TABLE IF NOT EXISTS snap (hour_ms INTEGER, ex TEXT, key TEXT, rate REAL, next_ms INTEGER, interval_h REAL,"
          " bid REAL, ask REAL, turnover24h REAL, taken_ms INTEGER, PRIMARY KEY (hour_ms, ex, key))")
BYBIT = "https://api.bybit.com/v5/market"
BITGET = "https://api.bitget.com/api/v2/mix/market"
OKX = "https://www.okx.com/api/v5"
Row = Tuple[str, float, Optional[int], Optional[float], float, float, float]   # key, rate, next_ms, interval_h, bid, ask, turnover


def db_path() -> Path:
    return Path(os.environ.get("XFUNDING_LIVE_DB", "/data/db/xfunding_live.db"))


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    p = path or db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.execute(SCHEMA)
    return conn


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def bybit(http) -> List[Row]:
    inst, cursor = {}, ""
    for _ in range(10):
        params = {"category": "linear", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        r = http.get(f"{BYBIT}/instruments-info", params=params)
        r.raise_for_status()
        res = r.json()["result"]
        for i in res["list"]:
            if i.get("quoteCoin") == "USDT" and i.get("contractType") == "LinearPerpetual" and i.get("status") == "Trading":
                inst[i["symbol"]] = _f(i.get("fundingInterval")) / 60 or None
        cursor = res.get("nextPageCursor") or ""
        if not cursor:
            break
    r = http.get(f"{BYBIT}/tickers", params={"category": "linear"})
    r.raise_for_status()
    return [(t["symbol"][:-4], _f(t.get("fundingRate")), int(_f(t.get("nextFundingTime"))) or None, inst[t["symbol"]],
             _f(t.get("bid1Price")), _f(t.get("ask1Price")), _f(t.get("turnover24h")))
            for t in r.json()["result"]["list"] if t["symbol"] in inst]


def bitget(http) -> List[Row]:
    r = http.get(f"{BITGET}/current-fund-rate", params={"productType": "usdt-futures"})
    r.raise_for_status()
    fund = {x["symbol"]: x for x in r.json()["data"]}
    r = http.get(f"{BITGET}/tickers", params={"productType": "USDT-FUTURES"})
    r.raise_for_status()
    out = []
    for t in r.json()["data"]:
        s = t["symbol"]
        if not s.endswith("USDT") or s not in fund:
            continue
        f = fund[s]
        out.append((s[:-4], _f(f.get("fundingRate")), int(_f(f.get("nextUpdate"))) or None, _f(f.get("fundingRateInterval")) or None,
                    _f(t.get("bidPr")), _f(t.get("askPr")), _f(t.get("usdtVolume") or t.get("quoteVolume"))))
    return out


def okx(http) -> List[Row]:
    r = http.get(f"{OKX}/public/funding-rate", params={"instId": "ANY"})
    r.raise_for_status()
    fund = {x["instId"]: x for x in r.json()["data"]}
    r = http.get(f"{OKX}/market/tickers", params={"instType": "SWAP"})
    r.raise_for_status()
    out = []
    for t in r.json()["data"]:
        inst = t["instId"]
        if not inst.endswith("-USDT-SWAP") or inst not in fund:
            continue
        f = fund[inst]
        nxt, prev = int(_f(f.get("fundingTime"))) or None, int(_f(f.get("prevFundingTime"))) or None
        interval = (nxt - prev) / HOUR_MS if nxt and prev else None
        # volCcy24h у SWAP — объём в базовой монете; оборот в USDT ≈ объём × последняя цена
        out.append((inst[:-10], _f(f.get("fundingRate")), nxt, interval, _f(t.get("bidPx")), _f(t.get("askPx")),
                    _f(t.get("volCcy24h")) * _f(t.get("last"))))
    return out


SOURCES = (("bybit", bybit), ("bitget", bitget), ("okx", okx))


def snapshot(http) -> Dict[str, List[Row]]:
    """{биржа: строки}; биржа со сбоем — пустой список (в журнал), остальные идут."""
    out = {}
    for name, fetch in SOURCES:
        try:
            out[name] = fetch(http)
        except Exception as exc:  # сеть, формат — пропуск биржи в этом часе
            logger.warning("xfunding_live: %s не ответила: %s", name, type(exc).__name__)
            out[name] = []
    return out


def record(conn: sqlite3.Connection, http, now_ms: int) -> dict:
    snap = snapshot(http)
    counts: Dict[str, int] = {}
    for rows in snap.values():
        for r in rows:
            counts[r[0]] = counts.get(r[0], 0) + 1
    hour = now_ms // HOUR_MS * HOUR_MS
    rows = [(hour, ex, *r[:7], now_ms) for ex, rs in snap.items() for r in rs if counts[r[0]] >= 2]
    conn.executemany("INSERT OR IGNORE INTO snap VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    return {"hour_ms": hour, "rows": len(rows), "keys": sum(1 for n in counts.values() if n >= 2),
            "exchanges": sum(1 for rs in snap.values() if rs)}


def maybe_record(conn: sqlite3.Connection, http, now_ms: int) -> Optional[dict]:
    """Раз в час: если за текущий час снимка ещё нет. None — ничего не делалось."""
    hour = now_ms // HOUR_MS * HOUR_MS
    if conn.execute("SELECT 1 FROM snap WHERE hour_ms = ? LIMIT 1", (hour,)).fetchone():
        return None
    return record(conn, http, now_ms)
