"""
И5 плана трейдера (docs/TRADER_PLAN.md, «Исследование преимущества»): факторы между монетами —
ликвидность и низкая волатильность. Правило, варианты и критерии записаны в плане ДО прогона —
здесь только исполнение.

    py -m backtest.factors_xs

Раз в неделю (понедельник 00:00 UTC) по закрытым 4h: «ликвидность» — оборот за 28 дней (лонг
5 наименьших, шорт 5 наибольших); «низкая волатильность» — годовая волатильность по 28 дневным
закрытиям (лонг 5 самых спокойных, шорт 5 самых волатильных). По 10 % капитала на позицию,
веса каждую неделю к 10 %; вход и выход по open 4h-бара; издержки на весь оборот, фандинг.
"""
import argparse
import bisect
import math
from datetime import UTC, datetime
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from backtest import funding_events as fe
from backtest import history, report
from backtest import momentum_xs as mx
from backtest.portfolio import SLIPPAGE, TAKER_FEE

DAY_MS, H4_MS, WEEK_MS = mx.DAY_MS, mx.H4_MS, mx.WEEK_MS
WINDOW_D = 28
TOP = 5
WEIGHT = 0.10
FACTORS = ("liquidity", "lowvol")
ALPHA = 0.05 / len(FACTORS)        # интервал 97,5 %
MIN_WEEKS = 150
MIN_MEAN = 0.0015
MAX_DRAWDOWN = 0.15
START = "2022-10-03"
HOLDOUT_DAYS = 75


def turnover(t4: Dict[int, float], t: int) -> Optional[float]:
    """Оборот за WINDOW_D дней до t — сумма по 4h-барам, закрывшимся к t; None, если бара не хватает."""
    bars = [t4.get(t - k * H4_MS) for k in range(1, WINDOW_D * 6 + 1)]
    return None if any(b is None for b in bars) else sum(bars)


def volatility(c4: Dict[int, float], t: int) -> Optional[float]:
    """Годовая волатильность по WINDOW_D дневным лог-доходностям (закрытия 4h-баров в 00:00 UTC до t)."""
    day0 = t - t % DAY_MS
    closes = [c4.get(day0 - k * DAY_MS - H4_MS) for k in range(WINDOW_D + 1)]
    if any(not c for c in closes):
        return None
    rets = [math.log(closes[k] / closes[k + 1]) for k in range(WINDOW_D)]
    mean = sum(rets) / WINDOW_D
    var = sum((r - mean) ** 2 for r in rets) / (WINDOW_D - 1)
    return math.sqrt(var * 365)


SCORES: Dict[str, Callable[[Dict, int], Optional[float]]] = {
    "liquidity": lambda d, t: turnover(d["t4"], t),
    "lowvol": lambda d, t: volatility(d["c4"], t),
}


def baskets(data: Dict, symbols: Sequence[str], t: int, t_next: int, factor: str) -> Optional[Tuple[Tuple[str, ...], Tuple[str, ...]]]:
    """(лонг, шорт): лонг — TOP с наименьшим значением фактора, шорт — TOP с наибольшим."""
    scored = []
    for s in symbols:
        d = data.get(s)
        if not d or not d["o4"].get(t) or not d["o4"].get(t_next):
            continue
        v = SCORES[factor](d, t)
        if v is not None:
            scored.append((v, s))
    if len(scored) < 2 * TOP:
        return None
    scored.sort()
    return tuple(s for _, s in scored[:TOP]), tuple(s for _, s in scored[-TOP:])


def simulate(data: Dict, symbols: Sequence[str], weeks_t: Sequence[int], factor: str) -> List[mx.Week]:
    out: List[mx.Week] = []
    held: Dict[str, float] = {}  # символ → экспозиция в долях капитала после дрейфа цены (+ лонг, − шорт)
    for t, t_next in zip(weeks_t, weeks_t[1:]):
        pick = baskets(data, symbols, t, t_next, factor)
        target = {} if pick is None else {**{s: WEIGHT for s in pick[0]}, **{s: -WEIGHT for s in pick[1]}}
        turnover_cap = sum(abs(target.get(s, 0.0) - held.get(s, 0.0)) for s in set(target) | set(held))
        costs = turnover_cap * (TAKER_FEE + SLIPPAGE)
        gross = funding = 0.0
        new_held: Dict[str, float] = {}
        for s, w in target.items():
            p0, p1 = data[s]["o4"][t], data[s]["o4"][t_next]
            gross += w * (p1 / p0 - 1)
            f = data[s]["fund"]
            for i in range(bisect.bisect_right(f["ts"], t), bisect.bisect_right(f["ts"], t_next)):
                funding += w * f["rate"][i] * data[s]["o4"].get(f["ts"][i], p0) / p0  # лонг платит положительную
            new_held[s] = w * p1 / p0
        out.append(mx.Week(t, t_next, gross - costs - funding, gross, costs, funding,
                           pick[0] if pick else (), pick[1] if pick else ()))
        held = new_held
    if out and held:
        exit_cost = sum(abs(v) for v in held.values()) * (TAKER_FEE + SLIPPAGE)
        out[-1].r -= exit_cost
        out[-1].costs += exit_cost
    return out


def evaluate(weeks: Sequence[mx.Week], bootstrap: int = 2000, alpha: float = ALPHA) -> Dict:
    """Критерии И5 из плана; отрезки — три равные части периода недель."""
    n = len(weeks)
    if not n:
        return {"weeks": 0, "passed": False, "checks": {}}
    rs = [w.r for w in weeks]
    mean = sum(rs) / n
    low, high = report.expectancy_ci(weeks, bootstrap, alpha=alpha)
    parts, _ = report.splits(weeks[0].entry_t, weeks[-1].exit_t, holdout_days=0)
    segments = []
    for part in parts:
        chunk = report.within(weeks, part)
        segments.append(sum(w.r for w in chunk) / len(chunk) if chunk else None)
    drawdown = report.max_drawdown(rs)
    sd = (sum((r - mean) ** 2 for r in rs) / (n - 1)) ** 0.5 if n > 1 else 0.0
    checks = {
        "weeks": n >= MIN_WEEKS,
        "ci": low > 0,
        "mean": mean >= MIN_MEAN,
        "segments": sum(1 for m in segments if m is not None and m > 0) >= 2,
        "drawdown": drawdown <= MAX_DRAWDOWN,
    }
    return {"weeks": n, "mean": mean, "sd": sd, "ci": (low, high), "win_rate": sum(r > 0 for r in rs) / n,
            "segments": segments, "drawdown": drawdown, "checks": checks, "passed": all(checks.values()),
            "costs": sum(w.costs for w in weeks) / n, "funding": sum(w.funding for w in weeks) / n,
            "gross": sum(w.gross for w in weeks) / n}


def load(conn, symbols: Sequence[str], start_ms: int, end_ms: int) -> Dict:
    data = {}
    for s in symbols:
        bars = history.load_candles(conn, s, "4h", start_ms - 40 * DAY_MS, end_ms + H4_MS)
        rows = [(int(ts), float(r)) for ts, r in conn.execute("SELECT ts, rate FROM funding WHERE symbol = ? ORDER BY ts", (s,))]
        data[s] = {"c4": {int(b[0]): float(b[4]) for b in bars}, "o4": {int(b[0]): float(b[1]) for b in bars},
                   "t4": {int(b[0]): float(b[6]) for b in bars if b[6] is not None},
                   "fund": {"ts": [r[0] for r in rows], "rate": [r[1] for r in rows]}}
    return data


def render(factor: str, stats: Dict, alpha: float = ALPHA) -> str:
    title = {"liquidity": "ликвидность", "lowvol": "низкая волатильность"}[factor]
    if not stats["weeks"]:
        return f"{title}: недель нет"
    low, high = stats["ci"]
    seg = " / ".join("—" if m is None else f"{100 * m:+.2f}" for m in stats["segments"])
    failed = [k for k, ok in stats["checks"].items() if not ok]
    level = f"{100 * (1 - alpha):.1f}".replace(".", ",")
    return (f"{title}: {stats['weeks']} нед., средняя {100 * stats['mean']:+.3f} % капитала "
            f"[{100 * low:+.3f}; {100 * high:+.3f}] ({level} %), разброс {100 * stats['sd']:.2f} %, "
            f"плюсовых недель {100 * stats['win_rate']:.0f} %, отрезки {seg} %, просадка {100 * stats['drawdown']:.1f} %; "
            f"в неделю: до издержек {100 * stats['gross']:+.3f} %, издержки {100 * stats['costs']:.3f} %, "
            f"фандинг {100 * stats['funding']:+.3f} % → {'ПРОХОДИТ' if stats['passed'] else 'не проходит: ' + ', '.join(failed)}")


def main(argv=None) -> int:
    import config
    parser = argparse.ArgumentParser(description="И5: факторы между монетами — ликвидность и низкая волатильность")
    parser.add_argument("--db", default=str(history.DEFAULT_DB))
    parser.add_argument("--symbols", default=",".join(config.SYMBOLS))
    parser.add_argument("--holdout", action="store_true", help="показать отложенный конец (смотреть один раз)")
    args = parser.parse_args(argv)

    conn = history.connect(args.db)
    _, end_ms = fe.period(conn, 12)
    start_ms = mx.day_ms(START)
    last = end_ms if args.holdout else end_ms - HOLDOUT_DAYS * DAY_MS
    weeks_t = mx.mondays(start_ms, last)
    symbols = args.symbols.split(",")
    data = load(conn, symbols, start_ms, last)
    conn.close()

    print(f"И5, ребалансировки {datetime.fromtimestamp(weeks_t[0] / 1000, UTC):%d.%m.%Y}–"
          f"{datetime.fromtimestamp(weeks_t[-1] / 1000, UTC):%d.%m.%Y} "
          f"({'с отложенным концом' if args.holdout else 'без отложенного конца'}), лонг/шорт по {TOP}, {100 * WEIGHT:.0f} % на позицию")
    passed = False
    for factor in FACTORS:
        weeks = simulate(data, symbols, weeks_t, factor)
        stats = evaluate(weeks)
        print(render(factor, stats))
        empty = sum(1 for w in weeks if not w.longs)
        print(f"  недель без позиций {empty}")
        passed = passed or stats["passed"]
    print("Вердикт И5:", "принимается" if passed else "не принимается")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
