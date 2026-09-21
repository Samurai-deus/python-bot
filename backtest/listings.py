"""
И21 — шорт новых листингов против BTC (docs/TRADER_PLAN.md). Прогон замороженного правила по кэшу широкой
вселенной Bybit (`backtest/history_wide.py`: 4h-свечи и фандинг от листинга).

    py -m backtest.listings run [--db data/history_wide.db] [--out listings.json]
    py -m backtest.listings holdout [--db ...]          # листинги с 21.03.2026 — только после прохождения видимой части
"""
import argparse
import bisect
import json
import random
import sqlite3
import statistics as st
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from backtest.wide_search import is_crypto

H4 = 4 * 3_600_000
DAY_MS = 86_400_000
WEEK_MS = 7 * DAY_MS
BTC = "BTCUSDT"

# --- правило (заморожено 21.09.2026, docs/TRADER_PLAN.md, И21) ---------------------------------------
FROM = "2022-01-01"
HOLDOUT_FROM = "2026-03-21"
WAIT_MS = DAY_MS                  # вход — первый 4h-бар после суток с листинга
HOLD_MS = 28 * DAY_MS
MIN_FIRST_DAY_TURNOVER = 2_000_000.0
FEE = 0.00055
SLIP_NEW, SLIP_BTC = 0.0010, 0.0002
LEG = 1 / 20
MAX_OPEN = 20


@dataclass
class Series:
    open: Dict[int, float] = field(default_factory=dict)       # 4h: ts → open
    turnover: Dict[int, float] = field(default_factory=dict)   # 4h: ts → оборот, USDT
    fund_ts: List[int] = field(default_factory=list)
    fund_rate: List[float] = field(default_factory=list)


def day(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def load(conn: sqlite3.Connection, since: int) -> tuple:
    launches = {s: int(t) for s, t in conn.execute("SELECT symbol, launch_ms FROM instruments") if int(t) >= since or s == BTC}
    data: Dict[str, Series] = {s: Series() for s in launches}
    for s, ts, o, turn in conn.execute("SELECT symbol, ts, open, turnover FROM candles WHERE interval = '4h'"):
        if s in data:
            data[s].open[int(ts)] = float(o)
            data[s].turnover[int(ts)] = float(turn or 0.0)
    for s, ts, r in conn.execute("SELECT symbol, ts, rate FROM funding ORDER BY ts"):
        if s in data and r is not None:
            data[s].fund_ts.append(int(ts))
            data[s].fund_rate.append(float(r))
    return launches, data


def price_at(s: Series, t: int) -> Optional[float]:
    return s.open.get(t)


def funding_between(s: Series, t0: int, t1: int) -> float:
    """Сумма ставок с временем выплаты в (t0, t1]."""
    i, j = bisect.bisect_right(s.fund_ts, t0), bisect.bisect_right(s.fund_ts, t1)
    return sum(s.fund_rate[i:j])


def trade(symbol: str, launch: int, data: Dict[str, Series], slip_mult: float = 1.0) -> Optional[dict]:
    """Сделка по листингу или None (мало оборота / нет цен). Результат — доля номинала ноги."""
    s, b = data[symbol], data[BTC]
    t0 = (launch + WAIT_MS + H4 - 1) // H4 * H4
    first_day = sum(v for t, v in s.turnover.items() if launch <= t < launch + DAY_MS)
    if first_day < MIN_FIRST_DAY_TURNOVER:
        return None
    t1 = t0 + HOLD_MS
    p0, b0 = price_at(s, t0), price_at(b, t0)
    if not p0 or not b0:
        return None
    ended = False
    if price_at(s, t1) is None or price_at(b, t1) is None:        # данных нет до конца удержания — последний общий бар
        common = [t for t in s.open if t0 < t <= t1 and t in b.open]
        if not common:
            return None
        t1, ended = max(common), True
    p1, b1 = price_at(s, t1), price_at(b, t1)
    short_leg = 1 - p1 / p0
    long_leg = b1 / b0 - 1
    costs = (FEE + SLIP_NEW * slip_mult) * (1 + p1 / p0) + (FEE + SLIP_BTC * slip_mult) * (1 + b1 / b0)
    funding = funding_between(s, t0, t1) - funding_between(b, t0, t1)   # шорт получает ставку нового, лонг платит ставку BTC
    return {"symbol": symbol, "launch": launch, "t0": t0, "t1": t1, "short_leg": short_leg, "long_leg": long_leg,
            "costs": costs, "funding": funding, "r": short_leg + long_leg - costs + funding, "truncated": ended}


def trades(launches: Dict[str, int], data: Dict[str, Series], since: int, until: Optional[int] = None, slip_mult: float = 1.0) -> List[dict]:
    out = []
    for s, t in sorted(launches.items(), key=lambda kv: kv[1]):
        if s == BTC or t < since or (until is not None and t >= until) or not is_crypto(s):
            continue
        tr = trade(s, t, data, slip_mult)
        if tr:
            out.append(tr)
    return out


def month(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m")


def boot_mean_ci(trs: Sequence[dict], n: int = 2000, seed: int = 7) -> tuple:
    """95 % интервал средней сделки бутстрапом по месяцам листинга (сделки одного месяца коррелированы)."""
    groups: Dict[str, List[float]] = {}
    for x in trs:
        groups.setdefault(month(x["launch"]), []).append(x["r"])
    keys = list(groups)
    rnd = random.Random(seed)
    means = []
    for _ in range(n):
        pick = [groups[rnd.choice(keys)] for _ in keys]
        flat = [r for g in pick for r in g]
        means.append(st.mean(flat))
    means.sort()
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]


def portfolio_weeks(trs: Sequence[dict], data: Dict[str, Series], weeks: Sequence[int]) -> Dict[int, float]:
    """Недельный результат портфеля на капитал: нога = капитал/20, не больше 20 одновременно (лишние пропускаются)."""
    active, taken = [], []
    for x in sorted(trs, key=lambda x: x["t0"]):
        active = [a for a in active if a["t1"] > x["t0"]]
        if len(active) < MAX_OPEN:
            active.append(x)
            taken.append(x)
    out = {}
    for t, t_next in zip(weeks, weeks[1:]):
        r = 0.0
        for x in taken:
            a, z = max(t, x["t0"]), min(t_next, x["t1"])
            if a >= z:
                continue
            s, b = data[x["symbol"]], data[BTC]
            pa, pz, ba, bz = price_at(s, a), price_at(s, z), price_at(b, a), price_at(b, z)
            p0, b0 = price_at(s, x["t0"]), price_at(b, x["t0"])
            if None in (pa, pz, ba, bz, p0, b0):
                continue
            r += LEG * ((pa - pz) / p0 + (bz - ba) / b0 + funding_between(s, a, z) - funding_between(b, a, z))
            if x["t0"] >= t:
                r -= LEG * ((FEE + SLIP_NEW) + (FEE + SLIP_BTC))
            if x["t1"] <= t_next:             # выход ровно в понедельник 00:00 — издержки в этой неделе
                r -= LEG * ((FEE + SLIP_NEW) * pz / p0 + (FEE + SLIP_BTC) * bz / b0)
        out[t] = r
    return out


def mondays(start: int, end: int) -> List[int]:
    t = start - (start - day("2022-01-03")) % WEEK_MS
    out = []
    while t <= end:
        if t >= start:
            out.append(t)
        t += WEEK_MS
    return out


def summarize(trs: Sequence[dict]) -> dict:
    rs = sorted((x["r"] for x in trs), reverse=True)
    k = max(1, int(round(0.05 * len(rs))))
    by_year: Dict[int, List[float]] = {}
    for x in trs:
        by_year.setdefault(datetime.fromtimestamp(x["launch"] / 1000, UTC).year, []).append(x["r"])
    lo, hi = boot_mean_ci(trs) if len(trs) >= 10 else (float("nan"), float("nan"))
    return {"trades": len(trs), "mean": st.mean(rs) if rs else 0.0, "ci95": (lo, hi), "median": st.median(rs) if rs else 0.0,
            "win_share": sum(1 for r in rs if r > 0) / len(rs) if rs else 0.0,
            "mean_without_top5pct": st.mean(rs[k:]) if len(rs) > k else 0.0,
            "worst": min(rs) if rs else 0.0, "best": max(rs) if rs else 0.0,
            "by_year": {y: (len(v), st.mean(v)) for y, v in sorted(by_year.items())},
            "short_leg": st.mean([x["short_leg"] for x in trs]) if trs else 0.0,
            "long_leg": st.mean([x["long_leg"] for x in trs]) if trs else 0.0,
            "costs": st.mean([x["costs"] for x in trs]) if trs else 0.0,
            "funding": st.mean([x["funding"] for x in trs]) if trs else 0.0}


def correlation(a: Dict[int, float], b: Dict[int, float]) -> Optional[float]:
    keys = [k for k in a if k in b]
    if len(keys) < 10:
        return None
    return st.correlation([a[k] for k in keys], [b[k] for k in keys])


def h18_weeks(db: str, start: int, end: int) -> Dict[int, float]:
    """Недели бумажного И18 (атлас: лонг BTC 50 % / шорт 30 ликвидных альтов) — для проверки, не тот ли это эффект."""
    from backtest import atlas as at
    from backtest import history
    from backtest import momentum_xs as mx
    from backtest import wide_search as ws
    conn = history.connect(db)
    data = ws.load(conn, start, end)
    atl = at.Atlas(data, start, end)

    def fn(t, t_next):
        alts = [s for s in atl.top(t // DAY_MS, 31) if s != BTC and data[s].open.get(t) and data[s].open.get(t_next)][:30]
        if len(alts) < 10 or not data[BTC].open.get(t_next):
            return {}
        return {BTC: 0.5, **{s: -0.5 / len(alts) for s in alts}}
    return {w.entry_t: w.r for w in at.simulate_weights(atl, fn, mx.mondays(start, end))}


def verdict(s: dict, corr: Optional[float]) -> dict:
    years = [y for y in range(2022, 2027) if y in s["by_year"]]
    plus_years = sum(1 for y in years if s["by_year"][y][1] > 0)
    checks = {"сделок ≥ 100": s["trades"] >= 100, "средняя > 0": s["mean"] > 0, "нижняя граница 95 % > 0": s["ci95"][0] > 0,
              "плюс в ≥ 4 из 5 лет": plus_years >= 4, "без 5 % лучших > 0": s["mean_without_top5pct"] > 0,
              "корреляция с И18 < 0,7": corr is not None and corr < 0.7}
    return {"checks": checks, "passed": all(checks.values()), "plus_years": plus_years, "corr_h18": corr}


def run(db: str) -> dict:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    launches, data = load(conn, day(FROM))
    trs = trades(launches, data, day(FROM), day(HOLDOUT_FROM))
    stress = trades(launches, data, day(FROM), day(HOLDOUT_FROM), slip_mult=2.0)
    s = summarize(trs)
    end = max(x["t1"] for x in trs) if trs else day(HOLDOUT_FROM)
    weeks = mondays(day(FROM), end)
    port = portfolio_weeks(trs, data, weeks)
    corr = correlation(port, h18_weeks(db, day(FROM), end))
    pr = list(port.values())
    return {"visible": s, "stress": summarize(stress), "portfolio": {"weeks": len(pr), "mean_week": st.mean(pr) if pr else 0.0,
                                                                    "sd_week": st.pstdev(pr) if pr else 0.0},
            "verdict": verdict(s, corr), "worst_trades": sorted(trs, key=lambda x: x["r"])[:8],
            "best_trades": sorted(trs, key=lambda x: -x["r"])[:8]}


def run_holdout(db: str, visible: dict) -> dict:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    launches, data = load(conn, day(FROM))
    trs = trades(launches, data, day(HOLDOUT_FROM))
    s = summarize(trs)
    v = visible["visible"]
    se = (v["ci95"][1] - v["ci95"][0]) / (2 * 1.96)
    return {"holdout": s, "contradiction": s["mean"] < v["mean"] - 2 * se, "threshold": v["mean"] - 2 * se}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="И21: шорт новых листингов против BTC")
    ap.add_argument("cmd", choices=("run", "holdout"))
    ap.add_argument("--db", default="data/history_wide.db")
    ap.add_argument("--out", default="listings.json")
    ap.add_argument("--visible", help="json видимой части (для holdout)")
    a = ap.parse_args(argv)
    res = run(a.db) if a.cmd == "run" else run_holdout(a.db, json.loads(Path(a.visible).read_text(encoding="utf-8")))
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k not in ("worst_trades", "best_trades")}, ensure_ascii=False, default=str)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
