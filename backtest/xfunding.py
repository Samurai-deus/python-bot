"""
И19 — межбиржевой фандинг Bybit / Bitget / OKX (docs/TRADER_PLAN.md): загрузка публичной истории и проверка
замороженного правила.

    py -m backtest.xfunding download [--db data/xfunding.db] [--days 95]   # ставки + часовые свечи, с докачкой
    py -m backtest.xfunding run [--db data/xfunding.db] [--out xfunding.json]

Данные — без ключей: выплаченные ставки фандинга (у Bitget и OKX API отдаёт ~3 месяца) и часовые свечи
(закрытие, оборот) USDT-бессрочных контрактов с одной базой хотя бы на двух биржах. Ключ монеты — символ без
USDT (Bybit/Bitget) или семейство OKX без «-USDT»; «1000PEPE» и «PEPE» — разные ключи, одинаковые ключи с
разными активами отсекает проверка отношения цен.
"""
import argparse
import json
import logging
import random
import sqlite3
import statistics as st
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
EXCHANGES = ("bybit", "bitget", "okx")
TAKER_FEE = {"bybit": 0.00055, "bitget": 0.0006, "okx": 0.0005}

# --- правило (заморожено 21.09.2026, docs/TRADER_PLAN.md, И19) ---------------------------------------
ENTRY_DAY = 0.0030          # вход: разница ставок ≥ 0,30 % в сутки
EXIT_DAY = 0.0005           # выход: < 0,05 % в сутки (и смена знака)
MIN_TURNOVER_24H = 2_000_000.0
MAX_HOLD_MS = 14 * DAY_MS
MAX_POSITIONS = 10
LEG_FRACTION = 1 / 20       # номинал ноги = капитал / 20
SLIPPAGE = 0.0005
SAME_ASSET_BAND = (0.98, 1.02)

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS instruments (ex TEXT, key TEXT, symbol TEXT, PRIMARY KEY (ex, key))",
    "CREATE TABLE IF NOT EXISTS funding (ex TEXT, key TEXT, ts INTEGER, rate REAL, PRIMARY KEY (ex, key, ts))",
    "CREATE TABLE IF NOT EXISTS candles (ex TEXT, key TEXT, ts INTEGER, close REAL, quote_vol REAL, PRIMARY KEY (ex, key, ts))",
    "CREATE TABLE IF NOT EXISTS done (ex TEXT, key TEXT, kind TEXT, PRIMARY KEY (ex, key, kind))",
)


def connect(path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)   # загрузчик пишет из потоков бирж под общим замком
    for sql in SCHEMA:
        conn.execute(sql)
    return conn


# --- биржи ---------------------------------------------------------------------------------------------
Getter = Callable[[str, dict], dict]


def http_get(url: str, params: dict, retries: int = 4) -> dict:
    q = url + ("?" + urllib.parse.urlencode(params) if params else "")
    for attempt in range(retries):
        try:
            req = urllib.request.Request(q, headers={"User-Agent": "market-bot-research"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception as exc:  # сеть, 429 — пауза и повтор
            if attempt == retries - 1:
                raise
            logger.warning("xfunding: %s — повтор (%s)", q[:90], type(exc).__name__)
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")


class Bybit:
    name = "bybit"
    pause = 0.06

    def __init__(self, get: Getter):
        self.get = get

    def instruments(self) -> Dict[str, str]:
        out, cursor = {}, ""
        for _ in range(10):
            p = {"category": "linear", "limit": 1000}
            if cursor:
                p["cursor"] = cursor
            r = self.get("https://api.bybit.com/v5/market/instruments-info", p)["result"]
            for i in r["list"]:
                if i.get("quoteCoin") == "USDT" and i.get("contractType") == "LinearPerpetual" and i.get("status") == "Trading":
                    out[i["symbol"][:-4]] = i["symbol"]
            cursor = r.get("nextPageCursor") or ""
            if not cursor:
                break
        return out

    def funding(self, symbol: str, start: int, end: int) -> List[Tuple[int, float]]:
        out, cur_end = [], end
        for _ in range(50):
            r = self.get("https://api.bybit.com/v5/market/funding/history",
                         {"category": "linear", "symbol": symbol, "startTime": start, "endTime": cur_end, "limit": 200})["result"]["list"]
            rows = [(int(x["fundingRateTimestamp"]), float(x["fundingRate"])) for x in r]
            out += rows
            if len(r) < 200:
                break
            cur_end = min(t for t, _ in rows) - 1
            time.sleep(self.pause)
        return out

    def candles(self, symbol: str, start: int, end: int) -> List[Tuple[int, float, float]]:
        """Bybit отдаёт последние limit свечей диапазона (новые первыми) — листаем назад по самой старой."""
        out, cur_end = [], end
        for _ in range(100):
            r = self.get("https://api.bybit.com/v5/market/kline",
                         {"category": "linear", "symbol": symbol, "interval": "60", "start": start, "end": cur_end, "limit": 1000})["result"]["list"]
            rows = [(int(x[0]), float(x[4]), float(x[6])) for x in r]
            out += rows
            if len(r) < 1000:
                break
            cur_end = min(t for t, _, _ in rows) - 1
            time.sleep(self.pause)
        return out


class Bitget:
    name = "bitget"
    pause = 0.08

    def __init__(self, get: Getter):
        self.get = get

    def instruments(self) -> Dict[str, str]:
        rows = self.get("https://api.bitget.com/api/v2/mix/market/contracts", {"productType": "USDT-FUTURES"})["data"]
        return {c["symbol"][:-4]: c["symbol"] for c in rows
                if c["symbol"].endswith("USDT") and c.get("symbolStatus") in (None, "normal") and c.get("symbolType", "perpetual") == "perpetual"}

    def funding(self, symbol: str, start: int, end: int) -> List[Tuple[int, float]]:
        out = []
        for page in range(1, 50):
            r = self.get("https://api.bitget.com/api/v2/mix/market/history-fund-rate",
                         {"symbol": symbol, "productType": "usdt-futures", "pageSize": 100, "pageNo": page}).get("data") or []
            rows = [(int(x["fundingTime"]), float(x["fundingRate"])) for x in r]
            out += [x for x in rows if start <= x[0] <= end]
            if len(r) < 100 or min(t for t, _ in rows) < start:
                break
            time.sleep(self.pause)
        return out

    def candles(self, symbol: str, start: int, end: int) -> List[Tuple[int, float, float]]:
        out, cur_end = [], end
        for _ in range(100):
            r = self.get("https://api.bitget.com/api/v2/mix/market/history-candles",
                         {"symbol": symbol, "productType": "usdt-futures", "granularity": "1H", "limit": 200, "endTime": cur_end}).get("data") or []
            rows = [(int(x[0]), float(x[4]), float(x[6])) for x in r]
            out += [x for x in rows if x[0] >= start]
            if len(r) < 200 or min(t for t, _, _ in rows) <= start:
                break
            cur_end = min(t for t, _, _ in rows) - 1
            time.sleep(self.pause)
        return out


class Okx:
    name = "okx"
    pause = 0.25          # история ставок OKX: 10 запросов за 2 с

    def __init__(self, get: Getter):
        self.get = get

    def instruments(self) -> Dict[str, str]:
        rows = self.get("https://www.okx.com/api/v5/public/instruments", {"instType": "SWAP"})["data"]
        return {i["instFamily"][:-5]: i["instId"] for i in rows
                if i.get("settleCcy") == "USDT" and i.get("state") == "live" and i.get("instFamily", "").endswith("-USDT")}

    def funding(self, symbol: str, start: int, end: int) -> List[Tuple[int, float]]:
        out, after = [], ""
        for _ in range(50):
            p = {"instId": symbol, "limit": 100}
            if after:
                p["after"] = after
            r = self.get("https://www.okx.com/api/v5/public/funding-rate-history", p).get("data") or []
            rows = [(int(x["fundingTime"]), float(x.get("realizedRate") or x["fundingRate"])) for x in r]
            out += [x for x in rows if start <= x[0] <= end]
            if len(r) < 100 or min(t for t, _ in rows) < start:
                break
            after = str(min(t for t, _ in rows))
            time.sleep(self.pause)
        return out

    def candles(self, symbol: str, start: int, end: int) -> List[Tuple[int, float, float]]:
        out, after = [], str(end)
        for _ in range(100):
            r = self.get("https://www.okx.com/api/v5/market/history-candles", {"instId": symbol, "bar": "1H", "limit": 100, "after": after}).get("data") or []
            rows = [(int(x[0]), float(x[4]), float(x[7])) for x in r]
            out += [x for x in rows if x[0] >= start]
            if len(r) < 100 or min(t for t, _, _ in rows) <= start:
                break
            after = str(min(t for t, _, _ in rows))
            time.sleep(self.pause)
        return out


def adapters(get: Getter = http_get):
    return {"bybit": Bybit(get), "bitget": Bitget(get), "okx": Okx(get)}


def download(conn: sqlite3.Connection, now_ms: int, days: int = 95, ex_map=None) -> dict:
    """
    Инструменты, ставки и свечи общих контрактов (база хотя бы на двух биржах); уже скачанное пропускается.
    Биржи качаются параллельно — по потоку на биржу, у каждой свой лимит запросов (последовательно — ~10 ч).
    """
    ex_map = ex_map or adapters()
    start = now_ms - days * DAY_MS
    end = now_ms // HOUR_MS * HOUR_MS
    inst = {name: a.instruments() for name, a in ex_map.items()}
    keys = sorted(k for k in set().union(*inst.values()) if sum(k in m for m in inst.values()) >= 2)
    conn.executemany("INSERT OR REPLACE INTO instruments VALUES (?, ?, ?)",
                     [(ex, k, inst[ex][k]) for ex in inst for k in keys if k in inst[ex]])
    conn.commit()
    done = {tuple(r) for r in conn.execute("SELECT ex, key, kind FROM done")}
    stats = {"keys": len(keys), "series": 0, "funding": 0, "candles": 0, "failed": 0}
    lock = threading.Lock()

    def one_exchange(ex: str, a) -> None:
        for k in keys:
            if k not in inst[ex]:
                continue
            sym = inst[ex][k]
            for kind in ("funding", "candles"):
                if (ex, k, kind) in done:
                    continue
                try:
                    rows = a.funding(sym, start, end) if kind == "funding" else a.candles(sym, start, end)
                except Exception as exc:  # один ряд не скачался — остальные идут, повтор при следующем запуске
                    with lock:
                        stats["failed"] += 1
                    logger.warning("xfunding: %s %s %s не скачан: %s", ex, sym, kind, type(exc).__name__)
                    continue
                with lock:
                    if kind == "funding":
                        conn.executemany("INSERT OR IGNORE INTO funding VALUES (?, ?, ?, ?)", [(ex, k, t, r) for t, r in rows])
                    else:
                        conn.executemany("INSERT OR IGNORE INTO candles VALUES (?, ?, ?, ?, ?)", [(ex, k, t, c, v) for t, c, v in rows])
                    conn.execute("INSERT OR IGNORE INTO done VALUES (?, ?, ?)", (ex, k, kind))
                    conn.commit()
                    stats[kind] += len(rows)
                    stats["series"] += 1
                    if stats["series"] % 200 == 0:
                        logger.info("xfunding: рядов %d, ставок %d, свечей %d", stats["series"], stats["funding"], stats["candles"])
                time.sleep(a.pause)

    threads = [threading.Thread(target=one_exchange, args=(ex, a), name=f"xfunding-{ex}") for ex, a in ex_map.items()]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return stats


# --- данные для прогона ---------------------------------------------------------------------------------
@dataclass
class Series:
    funding: List[Tuple[int, float]] = field(default_factory=list)       # (время выплаты, ставка за интервал), по времени
    close: Dict[int, float] = field(default_factory=dict)                 # открытие часовой свечи → закрытие
    vol: Dict[int, float] = field(default_factory=dict)                   # открытие часовой свечи → оборот, USDT


def load(conn: sqlite3.Connection) -> Dict[str, Dict[str, Series]]:
    data: Dict[str, Dict[str, Series]] = {}
    for ex, k, t, r in conn.execute("SELECT ex, key, ts, rate FROM funding ORDER BY ts"):
        data.setdefault(k, {}).setdefault(ex, Series()).funding.append((t, r))
    for ex, k, t, c, v in conn.execute("SELECT ex, key, ts, close, quote_vol FROM candles"):
        s = data.setdefault(k, {}).setdefault(ex, Series())
        s.close[t], s.vol[t] = c, v
    return data


def same_asset(a: Series, b: Series) -> Optional[float]:
    """Медиана отношения закрытий a / b по общим часам; None — общих часов нет."""
    common = [a.close[t] / b.close[t] for t in a.close if t in b.close and b.close[t] > 0]
    return st.median(common) if len(common) >= 24 else None


def rate_day(s: Series, t: int) -> Optional[float]:
    """Последняя выплаченная к t ставка, в сутки (ставка × 24 / интервал между двумя последними выплатами)."""
    fund = s.funding
    lo, hi = 0, len(fund)
    while lo < hi:                         # число выплат с временем ≤ t
        mid = (lo + hi) // 2
        if fund[mid][0] <= t:
            lo = mid + 1
        else:
            hi = mid
    if lo < 2:
        return None
    interval_h = (fund[lo - 1][0] - fund[lo - 2][0]) / HOUR_MS
    if interval_h <= 0:
        return None
    return fund[lo - 1][1] * 24 / interval_h


def price(s: Series, t: int) -> Optional[float]:
    """Цена в момент t — закрытие часовой свечи, открывшейся в t − 1 ч."""
    return s.close.get(t - HOUR_MS)


def turnover_24h(s: Series, t: int) -> float:
    return sum(s.vol.get(t - i * HOUR_MS, 0.0) for i in range(1, 25))


@dataclass
class Position:
    key: str
    short_ex: str
    long_ex: str
    t0: int
    p_short: float
    p_long: float
    notional: float
    funding: float = 0.0
    costs: float = 0.0


@dataclass
class Params:
    entry: float = ENTRY_DAY
    exit: float = EXIT_DAY
    slippage: float = SLIPPAGE
    min_turnover: float = MIN_TURNOVER_24H
    max_hold: int = MAX_HOLD_MS
    max_positions: int = MAX_POSITIONS
    leg: float = LEG_FRACTION


def pairs_for(data: Dict[str, Dict[str, Series]]) -> Tuple[Dict[str, List[Tuple[str, str]]], List[str]]:
    """Допустимые пары бирж по монете (тот же актив по отношению цен) и список исключённых."""
    ok, excluded = {}, []
    for k, by_ex in data.items():
        exs = [e for e in EXCHANGES if e in by_ex and len(by_ex[e].funding) >= 2 and by_ex[e].close]
        for i, a in enumerate(exs):
            for b in exs[i + 1:]:
                ratio = same_asset(by_ex[a], by_ex[b])
                if ratio is not None and SAME_ASSET_BAND[0] <= ratio <= SAME_ASSET_BAND[1]:
                    ok.setdefault(k, []).append((a, b))
                elif ratio is not None:
                    excluded.append(f"{k} {a}/{b} {ratio:.3f}")
    return ok, excluded


def simulate(data: Dict[str, Dict[str, Series]], start: int, end: int, p: Params = Params()) -> dict:
    """
    Прогон правила И19 по часам [start, end). Капитал 1. Возвращает сделки, почасовую стоимость и итоги.
    Порядок в часе t: выплаты фандинга за (t − 1 ч, t] по открытым позициям → выходы → входы.
    """
    pairs, excluded = pairs_for(data)
    open_pos: Dict[str, Position] = {}
    trades, equity_curve = [], []
    realized = 0.0

    def cost(ex: str, notional: float) -> float:
        return notional * (TAKER_FEE[ex] + p.slippage)

    def leg_value(pos: Position, t: int) -> Optional[Tuple[float, float, float]]:
        ps, pl = price(data[pos.key][pos.short_ex], t), price(data[pos.key][pos.long_ex], t)
        if ps is None or pl is None:
            return None
        pnl = pos.notional * ((pos.p_short - ps) / pos.p_short + (pl - pos.p_long) / pos.p_long)
        return pnl, ps, pl

    def spread(k: str, a: str, b: str, t: int) -> Optional[float]:
        ra, rb = rate_day(data[k][a], t), rate_day(data[k][b], t)
        return None if ra is None or rb is None else ra - rb

    def liquid(k: str, ex: str, t: int) -> bool:
        return turnover_24h(data[k][ex], t) >= p.min_turnover

    for t in range(start, end, HOUR_MS):
        # 1) выплаты фандинга: шорт получает ставку × номинал, лонг платит (номинал — по цене на момент выплаты)
        for pos in open_pos.values():
            for ex, sign, p0 in ((pos.short_ex, 1, pos.p_short), (pos.long_ex, -1, pos.p_long)):
                for ts, r in data[pos.key][ex].funding:
                    if t - HOUR_MS < ts <= t and ts > pos.t0:
                        px = price(data[pos.key][ex], ts - ts % HOUR_MS) or p0
                        pos.funding += sign * r * pos.notional * px / p0
        # 2) выходы
        for k in list(open_pos):
            pos = open_pos[k]
            s = spread(k, pos.short_ex, pos.long_ex, t)
            reason = None
            if s is None or s < p.exit:
                reason = "spread"
            elif not (liquid(k, pos.short_ex, t) and liquid(k, pos.long_ex, t)):
                reason = "liquidity"
            elif t - pos.t0 >= p.max_hold:
                reason = "max_hold"
            elif t + HOUR_MS >= end:
                reason = "end"
            if reason is None:
                continue
            v = leg_value(pos, t)
            if v is None:
                if t + HOUR_MS < end:
                    continue        # цены нет — ждём следующего часа
                v = (0.0, pos.p_short, pos.p_long)
            pnl_price, ps, pl = v
            pos.costs += cost(pos.short_ex, pos.notional * ps / pos.p_short) + cost(pos.long_ex, pos.notional * pl / pos.p_long)
            net = pnl_price + pos.funding - pos.costs
            realized += net
            trades.append({"key": k, "short": pos.short_ex, "long": pos.long_ex, "t0": pos.t0, "t1": t,
                           "hours": (t - pos.t0) / HOUR_MS, "funding": pos.funding, "price": pnl_price, "costs": pos.costs,
                           "net": net, "reason": reason, "notional": pos.notional})
            del open_pos[k]
        # 3) входы
        cands = []
        for k, prs in pairs.items():
            if k in open_pos:
                continue
            best = None
            for a, b in prs:
                s = spread(k, a, b, t)
                if s is None or abs(s) < p.entry:
                    continue
                hi, lo = (a, b) if s > 0 else (b, a)
                if not (liquid(k, hi, t) and liquid(k, lo, t)):
                    continue
                if price(data[k][hi], t) is None or price(data[k][lo], t) is None:
                    continue
                if best is None or abs(s) > best[0]:
                    best = (abs(s), hi, lo)
            if best:
                cands.append((best[0], k, best[1], best[2]))
        cands.sort(reverse=True)
        for s, k, hi, lo in cands[: max(0, p.max_positions - len(open_pos))]:
            if t + HOUR_MS >= end:
                break
            pos = Position(k, hi, lo, t, price(data[k][hi], t), price(data[k][lo], t), p.leg)
            pos.costs = cost(hi, p.leg) + cost(lo, p.leg)
            open_pos[k] = pos
        # стоимость: реализованное + открытые по цене + начисленный фандинг − издержки входа
        unreal = 0.0
        for pos in open_pos.values():
            v = leg_value(pos, t)
            unreal += (v[0] if v else 0.0) + pos.funding - pos.costs
        equity_curve.append((t, realized + unreal))
    return {"trades": trades, "equity": equity_curve, "excluded": excluded, "pairs": sum(len(v) for v in pairs.values())}


def summarize(res: dict, seed: int = 7, n_boot: int = 2000) -> dict:
    """Недельная средняя по дневным приращениям стоимости, 95 % интервал бутстрапом по дням, итоги сделок."""
    eq = res["equity"]
    daily = {}
    for t, e in eq:
        daily[t // DAY_MS] = e
    days = sorted(daily)
    inc = [daily[d] - daily[days[i - 1]] for i, d in enumerate(days) if i] if len(days) > 1 else []
    if eq and days:
        inc = [daily[days[0]]] + inc
    mean_w = 7 * st.mean(inc) if inc else 0.0
    rnd = random.Random(seed)
    boots = sorted(7 * st.mean(rnd.choices(inc, k=len(inc))) for _ in range(n_boot)) if inc else [0.0]
    trades = res["trades"]
    nets = sorted((x["net"] for x in trades), reverse=True)
    return {
        "trades": len(trades), "days": len(inc), "mean_week": mean_w,
        "ci95": (boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots)) - 1]),
        "total": sum(nets), "without_top5": sum(nets[5:]),
        "win_share": sum(1 for x in trades if x["net"] > 0) / len(trades) if trades else 0.0,
        "funding_covers_costs": sum(1 for x in trades if x["funding"] > x["costs"]) / len(trades) if trades else 0.0,
        "median_hours": st.median([x["hours"] for x in trades]) if trades else 0.0,
        "funding": sum(x["funding"] for x in trades), "price": sum(x["price"] for x in trades),
        "costs": sum(x["costs"] for x in trades),
    }


def verdict(base: dict, stress: dict) -> dict:
    checks = {
        "сделок ≥ 50": base["trades"] >= 50,
        "средняя недели > 0": base["mean_week"] > 0,
        "нижняя граница 95 % > 0": base["ci95"][0] > 0,
        "проскальзывание 0,10 %: средняя > 0": stress["mean_week"] > 0,
        "без 5 лучших сделок > 0": base["without_top5"] > 0,
    }
    return {"checks": checks, "passed": all(checks.values())}


def run(conn: sqlite3.Connection) -> dict:
    data = load(conn)
    ts = [t for by in data.values() for s in by.values() for t, _ in s.funding]
    first_by_ex = {}
    for by in data.values():
        for ex, s in by.items():
            if s.funding:
                first_by_ex[ex] = min(first_by_ex.get(ex, s.funding[0][0]), s.funding[0][0])
    start = (max(first_by_ex.values()) // DAY_MS + 2) * DAY_MS          # все три биржи уже с историей, +сутки на интервал
    end = max(ts) // HOUR_MS * HOUR_MS + HOUR_MS
    base = simulate(data, start, end)
    stress = simulate(data, start, end, Params(slippage=0.0010))
    out = {"start": start, "end": end, "pairs": base["pairs"], "excluded": base["excluded"],
           "base": summarize(base), "stress": summarize(stress)}
    out["verdict"] = verdict(out["base"], out["stress"])
    by_pair = {}
    for x in base["trades"]:
        k = f"{x['short']}>{x['long']}"
        by_pair.setdefault(k, []).append(x["net"])
    out["by_exchange_pair"] = {k: {"trades": len(v), "net": sum(v)} for k, v in by_pair.items()}
    out["top_trades"] = sorted(base["trades"], key=lambda x: -x["net"])[:10]
    out["worst_trades"] = sorted(base["trades"], key=lambda x: x["net"])[:10]
    out["reasons"] = {r: sum(1 for x in base["trades"] if x["reason"] == r) for r in ("spread", "liquidity", "max_hold", "end")}
    grid = {}
    for e in (0.002, 0.003, 0.005):
        for x in (0.0, 0.0005, 0.001):
            s = summarize(simulate(data, start, end, Params(entry=e, exit=x)), n_boot=300)
            grid[f"вход {100 * e:.1f} / выход {100 * x:.2f}"] = {"trades": s["trades"], "mean_week": s["mean_week"], "ci95": s["ci95"]}
    out["grid"] = grid
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="И19: межбиржевой фандинг Bybit / Bitget / OKX")
    ap.add_argument("cmd", choices=("download", "run"))
    ap.add_argument("--db", default="data/xfunding.db")
    ap.add_argument("--days", type=int, default=95)
    ap.add_argument("--out", default="xfunding.json")
    a = ap.parse_args(argv)
    conn = connect(a.db)
    if a.cmd == "download":
        print(json.dumps(download(conn, int(time.time() * 1000), a.days)))
    else:
        res = run(conn)
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(json.dumps({k: res[k] for k in ("base", "stress", "verdict")}, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
