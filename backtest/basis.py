"""
И20 — базис срочных фьючерсов Bybit (docs/TRADER_PLAN.md): спот BTC/ETH в лонг + датированный фьючерс в шорт
до поставки. Загрузка публичной истории и прогон замороженного правила.

    py -m backtest.basis download [--db data/basis.db]
    py -m backtest.basis run [--db data/basis.db] [--out basis.json]

Данные: поставленные контракты с ценой поставки (/v5/market/delivery-price), живые датированные контракты
(BTC/ETH), дневные свечи каждого контракта и спота BTCUSDT/ETHUSDT. Цена дня d — закрытие дневной свечи,
открывшейся в d − 1 сутки (то есть цена на 00:00 UTC дня d).
"""
import argparse
import json
import logging
import sqlite3
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

DAY_MS = 86_400_000
BASES = ("BTC", "ETH")
BYBIT = "https://api.bybit.com/v5"

# --- правило (заморожено 21.09.2026, docs/TRADER_PLAN.md, И20) ---------------------------------------
MIN_DAYS, MAX_DAYS = 14, 200
FUT_COST = 0.00055 + 0.0005        # тейкер фьючерса + проскальзывание
SPOT_COST = 0.001 + 0.0005         # тейкер спота + проскальзывание
DELIVERY_FEE = 0.0005
MARGIN = 1 / 3                     # маржа шорта — треть номинала (плечо 3×)
MIN_YIELD = 0.03                   # вход — не ниже 3 % годовых на капитал
HOLDOUT_FROM = "2025-09-21"        # отложенный конец: входы с этой даты

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS contracts (symbol TEXT PRIMARY KEY, base TEXT, delivery_ms INTEGER, delivery_price REAL)",
    "CREATE TABLE IF NOT EXISTS daily (symbol TEXT, ts INTEGER, close REAL, PRIMARY KEY (symbol, ts))",
)


def connect(path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    for sql in SCHEMA:
        conn.execute(sql)
    return conn


def http_get(url: str, params: dict) -> dict:
    q = url + "?" + urllib.parse.urlencode(params)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(q, headers={"User-Agent": "market-bot-research"}), timeout=30) as r:
                return json.loads(r.read())
        except Exception:  # сеть — повтор с паузой
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")


def base_of(symbol: str) -> Optional[str]:
    """BTC-24MAR23 / BTCUSDT-25DEC26 → BTC; остальное (бессрочные, другие монеты) → None."""
    if "-" not in symbol:
        return None
    head = symbol.split("-")[0]
    for suffix in ("USDT", "USDC", "PERP"):
        if head.endswith(suffix):
            head = head[: -len(suffix)]
    return head if head in BASES else None


def download(conn: sqlite3.Connection, get: Callable[[str, dict], dict] = http_get, now_ms: Optional[int] = None) -> dict:
    now_ms = now_ms or int(time.time() * 1000)
    rows, cursor = [], ""
    for _ in range(50):
        p = {"category": "linear", "limit": 200}
        if cursor:
            p["cursor"] = cursor
        r = get(f"{BYBIT}/market/delivery-price", p)["result"]
        rows += [(x["symbol"], base_of(x["symbol"]), int(x["deliveryTime"]), float(x["deliveryPrice"])) for x in r["list"]]
        cursor = r.get("nextPageCursor") or ""
        if not cursor:
            break
    live = get(f"{BYBIT}/market/instruments-info", {"category": "linear", "limit": 1000})["result"]["list"]
    rows += [(i["symbol"], base_of(i["symbol"]), int(i["deliveryTime"]), None) for i in live
             if i.get("contractType") == "LinearFutures" and base_of(i["symbol"]) and int(i.get("deliveryTime") or 0) > now_ms]
    rows = [r for r in rows if r[1]]
    conn.executemany("INSERT OR REPLACE INTO contracts VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    n = 0
    for sym, _, delivery, _ in rows:
        end = min(delivery, now_ms)
        k = get(f"{BYBIT}/market/kline", {"category": "linear", "symbol": sym, "interval": "D", "end": end, "limit": 1000})["result"]["list"]
        conn.executemany("INSERT OR IGNORE INTO daily VALUES (?, ?, ?)", [(sym, int(x[0]), float(x[4])) for x in k])
        n += len(k)
        time.sleep(0.05)
    first = min(r[2] for r in rows) - 400 * DAY_MS
    for b in BASES:
        end = now_ms
        while end > first:
            k = get(f"{BYBIT}/market/kline", {"category": "spot", "symbol": f"{b}USDT", "interval": "D", "end": end, "limit": 1000})["result"]["list"]
            if not k:
                break
            conn.executemany("INSERT OR IGNORE INTO daily VALUES (?, ?, ?)", [(f"SPOT:{b}", int(x[0]), float(x[4])) for x in k])
            end = min(int(x[0]) for x in k) - 1
            if len(k) < 1000:
                break
    conn.commit()
    return {"contracts": len(rows), "candles": n}


@dataclass
class Contract:
    symbol: str
    base: str
    delivery_ms: int
    delivery_price: Optional[float]


def load(conn) -> Tuple[List[Contract], Dict[str, Dict[int, float]]]:
    cs = [Contract(*r) for r in conn.execute("SELECT symbol, base, delivery_ms, delivery_price FROM contracts")]
    px: Dict[str, Dict[int, float]] = {}
    for s, ts, c in conn.execute("SELECT symbol, ts, close FROM daily"):
        px.setdefault(s, {})[int(ts) + DAY_MS] = float(c)       # закрытие свечи дня d−1 = цена на 00:00 дня d
    return cs, px


def locked_yield(F: float, S: float, days: float, cost_mult: float = 1.0) -> Tuple[float, float]:
    """(доход на капитал за круг, годовых) по формуле правила; цена поставки ≈ F."""
    net = F * (1 - FUT_COST * cost_mult) - S * (1 + SPOT_COST * cost_mult) - F * (DELIVERY_FEE + SPOT_COST) * cost_mult
    r = net / (S + F * MARGIN)
    return r, r * 365 / days


def realized(F: float, S: float, P: float, cost_mult: float = 1.0) -> float:
    """Итог круга на капитал по фактической цене поставки P."""
    net = F * (1 - FUT_COST * cost_mult) - S * (1 + SPOT_COST * cost_mult) - P * (DELIVERY_FEE + SPOT_COST) * cost_mult
    return net / (S + F * MARGIN)


def closed_early(F: float, S: float, F_now: float, S_now: float, cost_mult: float = 1.0) -> float:
    """Круг, закрытый до поставки (только конец данных): откуп фьючерса и продажа спота по ценам дня."""
    net = (F * (1 - FUT_COST * cost_mult) - F_now * (1 + FUT_COST * cost_mult)
           + S_now * (1 - SPOT_COST * cost_mult) - S * (1 + SPOT_COST * cost_mult))
    return net / (S + F * MARGIN)


def simulate(contracts: Sequence[Contract], px: Dict[str, Dict[int, float]], start: int, end: int, cost_mult: float = 1.0,
             entry_until: Optional[int] = None) -> dict:
    """
    Весь капитал в одной позиции; круг — до поставки; в день поставки — следующий вход по правилу.
    entry_until — новых входов с этого дня нет, начатый круг держится до поставки (граница отложенного конца).
    """
    capital, rounds, pos, cash_days = 1.0, [], None, 0
    curve, last_d = [], start
    for d in range(start - start % DAY_MS, end, DAY_MS):
        if pos and d >= pos["c"].delivery_ms - pos["c"].delivery_ms % DAY_MS and pos["c"].delivery_price is not None:
            c = pos["c"]
            r = realized(pos["F"], pos["S"], c.delivery_price, cost_mult)
            capital *= 1 + r
            rounds.append({"symbol": c.symbol, "entry": pos["d"], "exit": d, "days": (d - pos["d"]) / DAY_MS,
                           "locked": pos["locked"], "realized": r, "delivered": True})
            pos = None
        if entry_until is not None and d >= entry_until and pos is None:
            break
        last_d = d
        if pos is None:
            best = None
            for c in contracts:
                days = (c.delivery_ms - d) / DAY_MS
                if not (MIN_DAYS <= days <= MAX_DAYS):
                    continue
                F, S = px.get(c.symbol, {}).get(d), px.get(f"SPOT:{c.base}", {}).get(d)
                if not F or not S:
                    continue
                r, ann = locked_yield(F, S, days, cost_mult)
                if ann >= MIN_YIELD and (best is None or ann > best[0]):
                    best = (ann, r, c, F, S)
            if best:
                pos = {"c": best[2], "F": best[3], "S": best[4], "d": d, "locked": best[1]}
            else:
                cash_days += 1
        curve.append((d, capital))
    if pos:                                        # не поставлен к концу данных — закрыть по ценам последнего дня
        c, d = pos["c"], last_d
        F_now = px.get(c.symbol, {}).get(d) or pos["F"]
        S_now = px.get(f"SPOT:{c.base}", {}).get(d) or pos["S"]
        r = closed_early(pos["F"], pos["S"], F_now, S_now, cost_mult)
        capital *= 1 + r
        rounds.append({"symbol": c.symbol, "entry": pos["d"], "exit": d, "days": (d - pos["d"]) / DAY_MS,
                       "locked": pos["locked"], "realized": r, "delivered": False})
    days_total = (last_d - start) / DAY_MS
    annual = capital ** (365 / days_total) - 1 if days_total > 0 else 0.0
    return {"rounds": rounds, "capital": capital, "annual": annual, "cash_days": cash_days, "days": days_total, "curve": curve}


def day(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def verdict(base: dict, stress: dict) -> dict:
    worst = min((r["realized"] for r in base["rounds"]), default=0.0)
    checks = {"кругов ≥ 6": len(base["rounds"]) >= 6, "годовая ≥ 5 %": base["annual"] >= 0.05,
              "двойные издержки: годовая ≥ 3 %": stress["annual"] >= 0.03, "худший круг ≥ −0,5 %": worst >= -0.005}
    return {"checks": checks, "passed": all(checks.values()), "worst_round": worst}


def run(conn) -> dict:
    cs, px = load(conn)
    firsts = [min(px[c.symbol]) for c in cs if px.get(c.symbol)]
    start = min(firsts)
    cut = day(HOLDOUT_FROM)
    end = max(max(v) for v in px.values()) + DAY_MS
    visible = simulate(cs, px, start, end, entry_until=cut)
    stress = simulate(cs, px, start, end, cost_mult=2.0, entry_until=cut)
    out = {"start": start, "cut": cut, "visible": {k: v for k, v in visible.items() if k != "curve"},
           "stress": {k: v for k, v in stress.items() if k not in ("curve", "rounds")}}
    out["verdict"] = verdict(visible, stress)
    return out


def run_holdout(conn) -> dict:
    """Отложенный конец (входы с HOLDOUT_FROM) — только после того, как видимая часть прошла."""
    cs, px = load(conn)
    end = max(max(v) for v in px.values())
    res = simulate(cs, px, day(HOLDOUT_FROM), end + DAY_MS)
    return {k: v for k, v in res.items() if k != "curve"}


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="И20: базис срочных фьючерсов Bybit")
    ap.add_argument("cmd", choices=("download", "run", "holdout"))
    ap.add_argument("--db", default="data/basis.db")
    ap.add_argument("--out", default="basis.json")
    a = ap.parse_args(argv)
    conn = connect(a.db)
    if a.cmd == "download":
        print(json.dumps(download(conn)))
        return 0
    res = run(conn) if a.cmd == "run" else run_holdout(conn)
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "rounds"}, ensure_ascii=False, default=str)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
