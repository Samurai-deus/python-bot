"""
И12 плана трейдера (docs/TRADER_PLAN.md): сбор фандинга — спот LONG + бессрочный SHORT на
одинаковый номинал по K монетам с наибольшим фандингом за прошлые 7 дней, раз в неделю.
Правило, варианты и критерии записаны в плане ДО прогона — здесь только исполнение.

    py -m backtest.funding_carry --sync-spot      # докачать 1h свечи спота Bybit в кэш
    py -m backtest.funding_carry                  # видимый период
    py -m backtest.funding_carry --holdout        # отложенный конец (один раз, после видимого)

Решение в t (понедельник 00:00 UTC) — только по ставкам с отметкой ≤ t; доход позиции — ставки
в (t; t + 7 дн]. Цены входа и выхода — open часовой свечи в t и t + 7 дн (спот и контракт).
"""
import argparse
import bisect
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Dict, List, Sequence, Tuple

from backtest import history, report

H = 3_600_000
DAY = 86_400_000
WEEK = 7 * DAY
LOOKBACK = 7 * DAY
WARMUP = 30 * DAY
KS = (5, 10)
ALPHA = 0.05 / len(KS)                  # интервал 97,5 %
SIDE_COST = 0.001 + 0.00055 + 2 * 0.0005  # спот + контракт + проскальзывание по ноге — 0,255 % номинала
CAPITAL_PER_NOTIONAL = 1.1              # спот оплачен + 10 % под маржу шорта
MIN_WEEKS = 150
MIN_MEAN = 0.0029                       # ≈ +15 % годовых на капитал
MAX_DRAWDOWN = 0.10
BLOCK = 4
EXCLUDE = {"SHIB1000USDT"}              # спота в том же масштабе цены нет


def _ms(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


START = _ms("2022-09-05")
VISIBLE_LAST = _ms("2026-03-09")
HOLDOUT_FIRST = _ms("2026-03-16")
HOLDOUT_LAST = _ms("2026-08-31")


@dataclass
class Week:
    t: int
    r: float            # итог недели на капитал (доля)
    pairs: int
    funding: float      # вклад фандинга на капитал
    basis: float        # вклад базиса на капитал
    cost: float         # издержки на капитал


def spot_name(symbol: str) -> str:
    """Спот для контракта: 1000PEPEUSDT → PEPEUSDT (масштаб цены на доходность не влияет)."""
    return symbol[4:] if symbol.startswith("1000") else symbol


def funding_sum(rows: Sequence[Tuple[int, float]], start: int, end: int) -> float:
    """Сумма ставок с отметкой в (start; end]; rows — (ts, ставка) по возрастанию."""
    stamps = [r[0] for r in rows]
    lo, hi = bisect.bisect_right(stamps, start), bisect.bisect_right(stamps, end)
    return sum(r[1] for r in rows[lo:hi])


def eligible(d: Dict, t: int) -> bool:
    """30 дней фандинга и обеих цен до t, и цены на входе и выходе недели."""
    if not d["funding"] or d["funding"][0][0] > t - WARMUP:
        return False
    for prices in (d["perp"], d["spot"]):
        if not prices or min(prices) > t - WARMUP or t not in prices or t + WEEK not in prices:
            return False
    return True


def select(data: Dict[str, Dict], t: int, k: int) -> List[str]:
    """K монет с наибольшей положительной суммой фандинга за (t − 7 дн; t]."""
    scored = [(funding_sum(d["funding"], t - LOOKBACK, t), s) for s, d in data.items() if eligible(d, t)]
    scored = sorted(((f, s) for f, s in scored if f > 0), key=lambda x: (-x[0], x[1]))
    return [s for _, s in scored[:k]]


def pair_parts(d: Dict, t: int) -> Tuple[float, float]:
    """(фандинг в пользу шорта, базис) пары за неделю на единицу номинала."""
    f = funding_sum(d["funding"], t, t + WEEK)
    basis = (d["spot"][t + WEEK] / d["spot"][t] - 1) - (d["perp"][t + WEEK] / d["perp"][t] - 1)
    return f, basis


def run(data: Dict[str, Dict], k: int, rebalances: Sequence[int]) -> List[Week]:
    weeks: List[Week] = []
    held: Dict[str, float] = {}   # монета → номинал (доля капитала)
    for i, t in enumerate(rebalances):
        chosen = select(data, t, k)
        target = {s: 1 / (len(chosen) * CAPITAL_PER_NOTIONAL) for s in chosen}
        turnover = sum(abs(target.get(s, 0.0) - held.get(s, 0.0)) for s in set(target) | set(held))
        fund = basis = 0.0
        for s, n in target.items():
            f, b = pair_parts(data[s], t)
            fund += n * f
            basis += n * b
        cost = SIDE_COST * turnover
        if i == len(rebalances) - 1:
            cost += SIDE_COST * sum(target.values())   # закрыть всё в конце периода
        weeks.append(Week(t, fund + basis - cost, len(target), fund, basis, cost))
        held = target
    return weeks


def block_ci(rs: Sequence[float], alpha: float = ALPHA, bootstrap: int = 2000, block: int = BLOCK,
             seed: int = 7) -> Tuple[float, float]:
    """Интервал средней недели: блочный бутстреп подряд идущими блоками по block недель."""
    n = len(rs)
    if n < 2 * block:
        m = sum(rs) / n
        return m, m
    rng = random.Random(seed)
    starts = list(range(n - block + 1))
    means = []
    for _ in range(bootstrap):
        sample: List[float] = []
        while len(sample) < n:
            s = rng.choice(starts)
            sample.extend(rs[s:s + block])
        means.append(sum(sample[:n]) / n)
    means.sort()
    return means[int(alpha / 2 * bootstrap)], means[int((1 - alpha / 2) * bootstrap) - 1]


def evaluate(weeks: Sequence[Week]) -> Dict:
    """Критерии И12 на видимом периоде (перебалансировки ≤ VISIBLE_LAST)."""
    vis = [w for w in weeks if w.t <= VISIBLE_LAST]
    n = len(vis)
    if not n:
        return {"weeks": 0, "passed": False, "checks": {}}
    rs = [w.r for w in vis]
    mean = sum(rs) / n
    low, high = block_ci(rs)
    third = n // 3
    parts = [rs[:third], rs[third:2 * third], rs[2 * third:]]
    segments = [sum(p) / len(p) if p else None for p in parts]
    drawdown = report.max_drawdown(rs)
    checks = {
        "weeks": n >= MIN_WEEKS,
        "ci": low > 0,
        "mean": mean >= MIN_MEAN,
        "segments": sum(1 for m in segments if m is not None and m > 0) >= 2,
        "drawdown": drawdown <= MAX_DRAWDOWN,
    }
    return {"weeks": n, "mean": mean, "ci": (low, high), "segments": segments, "drawdown": drawdown,
            "pairs": sum(w.pairs for w in vis) / n, "funding": sum(w.funding for w in vis) / n,
            "basis": sum(w.basis for w in vis) / n, "cost": sum(w.cost for w in vis) / n,
            "checks": checks, "passed": all(checks.values())}


def holdout(weeks: Sequence[Week]) -> Dict:
    tail = [w.r for w in weeks if HOLDOUT_FIRST <= w.t <= HOLDOUT_LAST]
    if not tail:
        return {"weeks": 0}
    mean = sum(tail) / len(tail)
    dd = report.max_drawdown(tail)
    return {"weeks": len(tail), "mean": mean, "drawdown": dd, "contradiction": mean <= 0 or dd > MAX_DRAWDOWN}


# ---------------------------------------------------------------------------
# Данные
# ---------------------------------------------------------------------------

SPOT_SCHEMA = ("CREATE TABLE IF NOT EXISTS spot_candles (symbol TEXT NOT NULL, ts INTEGER NOT NULL,"
               " open REAL, close REAL, PRIMARY KEY (symbol, ts))")


def sync_spot(conn, api, symbol: str, start_ms: int, end_ms: int) -> int:
    """1h свечи спота [start_ms, end_ms) в кэш, вперёд страницами от последней записанной; число новых."""
    conn.execute(SPOT_SCHEMA)
    last = conn.execute("SELECT MAX(ts) FROM spot_candles WHERE symbol = ?", (symbol,)).fetchone()[0]
    cursor = max(start_ms, last + H) if last is not None else start_ms
    added = 0
    while cursor < end_ms:
        page_end = min(end_ms - H, cursor + (history.KLINE_LIMIT - 1) * H)
        res = api.get("/v5/market/kline", {"category": "spot", "symbol": symbol, "interval": "60",
                                           "start": cursor, "end": page_end, "limit": history.KLINE_LIMIT})
        rows = [(int(r[0]), float(r[1]), float(r[4])) for r in res.get("list") or [] if int(r[0]) < end_ms]
        conn.executemany("INSERT OR IGNORE INTO spot_candles (symbol, ts, open, close) VALUES (?, ?, ?, ?)",
                         [(symbol, *r) for r in rows])
        added += len(rows)
        cursor = page_end + H
    conn.commit()
    return added


def load(conn, symbols: Sequence[str], end_ms: int) -> Dict[str, Dict]:
    conn.execute(SPOT_SCHEMA)
    data = {}
    for s in symbols:
        if s in EXCLUDE:
            continue
        funding = [(int(ts), float(r)) for ts, r in conn.execute(
            "SELECT ts, rate FROM funding WHERE symbol = ? ORDER BY ts", (s,))]
        perp = {int(b[0]): float(b[1]) for b in history.load_candles(conn, s, "1h", 0, end_ms)}
        spot = {int(ts): float(o) for ts, o in conn.execute(
            "SELECT ts, open FROM spot_candles WHERE symbol = ? AND ts < ?", (spot_name(s), end_ms))}
        data[s] = {"funding": funding, "perp": perp, "spot": spot}
    return data


def render(k: int, st: Dict) -> str:
    if not st["weeks"]:
        return f"K = {k:>2}: недель нет"
    low, high = st["ci"]
    seg = " / ".join("—" if m is None else f"{100 * m:+.3f}" for m in st["segments"])
    failed = [c for c, ok in st["checks"].items() if not ok]
    return (f"K = {k:>2}: {st['weeks']} нед., средняя {100 * st['mean']:+.3f} % "
            f"[{100 * low:+.3f}; {100 * high:+.3f}] (97,5 %) ≈ {100 * 52 * st['mean']:+.1f} % годовых; "
            f"отрезки {seg} %; просадка {100 * st['drawdown']:.1f} %; пар в среднем {st['pairs']:.1f}; "
            f"фандинг {100 * st['funding']:+.3f} / базис {100 * st['basis']:+.3f} / издержки {100 * st['cost']:.3f} % в нед. → "
            f"{'ПРОХОДИТ' if st['passed'] else 'не проходит: ' + ', '.join(failed)}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="И12: сбор фандинга — спот LONG + контракт SHORT")
    parser.add_argument("--db", default=str(history.DEFAULT_DB))
    parser.add_argument("--sync-spot", action="store_true", help="докачать 1h свечи спота Bybit в кэш")
    parser.add_argument("--holdout", action="store_true", help="вскрыть отложенный конец (один раз)")
    args = parser.parse_args(argv)

    conn = history.connect(args.db)
    symbols = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM funding ORDER BY symbol")]
    end_ms = HOLDOUT_LAST + WEEK + H
    if args.sync_spot:
        api = history.BybitHistory()
        for s in symbols:
            if s not in EXCLUDE:
                print(f"спот {spot_name(s)}: +{sync_spot(conn, api, spot_name(s), START - WARMUP - DAY, end_ms)}")
        return 0
    data = load(conn, symbols, end_ms)
    conn.close()
    rebalances = list(range(START, HOLDOUT_LAST + 1, WEEK))
    print(f"И12: {len(data)} монет, перебалансировки {len(rebalances)} (видимые до 09.03.2026), "
          f"издержки {100 * SIDE_COST:.3f} % номинала на сторону, капитал 1,1 номинала на пару")
    passed = False
    for k in KS:
        weeks = run(data, k, rebalances)
        st = evaluate(weeks)
        print(render(k, st))
        passed = passed or st["passed"]
        if args.holdout:
            ho = holdout(weeks)
            print(f"    отложенный конец: {ho['weeks']} нед., средняя {100 * ho['mean']:+.3f} %, "
                  f"просадка {100 * ho['drawdown']:.1f} % → {'противоречие' if ho['contradiction'] else 'без противоречия'}")
    print("Вердикт видимого периода И12:", "проходит" if passed else "не проходит")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
