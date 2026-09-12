"""
И3 плана трейдера (docs/TRADER_PLAN.md, «Исследование преимущества»): кросс-секционный
моментум. Правило, варианты и критерии записаны в плане ДО прогона — здесь только исполнение.

    py -m backtest.momentum_xs

Раз в неделю (понедельник 00:00 UTC) символы ранжируются по доходности за L дней без
последних суток (закрытые 4h); лонг — 5 сильнейших, шорт — 5 слабейших, по 10 % капитала.
Вход и выход — по open часового бара ребалансировки. Каждую неделю позиции приводятся к
10 %; издержки (taker и проскальзывание) — на весь оборот, включая подгонку весов и закрытие
в конце; фандинг каждой ноги по историческим ставкам. Результат недели — доля капитала.
Период — с 17.09.2024 до отложенных 75 суток текущего года (с --holdout — до конца).
"""
import argparse
import bisect
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from backtest import funding_events as fe
from backtest import history, report
from backtest.portfolio import SLIPPAGE, TAKER_FEE

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
WEEK_MS = 7 * DAY_MS
H4_MS = 4 * HOUR_MS
LOOKBACKS_D = (7, 28)
SKIP_MS = DAY_MS                   # последние сутки не входят в сигнал
TOP = 5
WEIGHT = 0.10
ALPHA = 0.05 / len(LOOKBACKS_D)    # интервал 97,5 %
MIN_WEEKS = 80
MIN_MEAN = 0.003
MAX_DRAWDOWN = 0.15
START = "2024-09-17"
HOLDOUT_DAYS = 75


@dataclass
class Week:
    entry_t: int
    exit_t: int
    r: float            # итог недели после издержек и фандинга — доля капитала
    gross: float
    costs: float
    funding: float
    longs: Tuple[str, ...]
    shorts: Tuple[str, ...]


def day_ms(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def mondays(start_ms: int, end_ms: int) -> List[int]:
    """Понедельники 00:00 UTC в [start_ms, end_ms]."""
    d = datetime.fromtimestamp(start_ms / 1000, UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    d += timedelta(days=(7 - d.weekday()) % 7)
    t = int(d.timestamp() * 1000)
    if t < start_ms:
        t += WEEK_MS
    out = []
    while t <= end_ms:
        out.append(t)
        t += WEEK_MS
    return out


def momentum(closes_4h: Dict[int, float], t: int, lookback_d: int) -> Optional[float]:
    """Доходность за lookback_d дней без последних суток — по 4h-барам, закрывшимся к t − 24 ч."""
    end_bar = t - SKIP_MS - H4_MS
    start_bar = end_bar - lookback_d * DAY_MS
    a, b = closes_4h.get(start_bar), closes_4h.get(end_bar)
    return None if not a or not b else b / a - 1


def baskets(data: Dict, symbols: Sequence[str], t: int, lookback_d: int) -> Optional[Tuple[Tuple[str, ...], Tuple[str, ...]]]:
    """(лонг, шорт) на момент t или None, если символов с данными меньше 2 × TOP."""
    scored = []
    for s in symbols:
        m = momentum(data[s]["c4"], t, lookback_d) if s in data else None
        if m is not None and data[s]["o1"].get(t):
            scored.append((m, s))
    if len(scored) < 2 * TOP:
        return None
    scored.sort()
    return tuple(s for _, s in scored[-TOP:]), tuple(s for _, s in scored[:TOP])


def simulate(data: Dict, symbols: Sequence[str], weeks_t: Sequence[int], lookback_d: int) -> List[Week]:
    out: List[Week] = []
    held: Dict[str, float] = {}  # символ → экспозиция в долях капитала после дрейфа цены (+ лонг, − шорт)
    for t, t_next in zip(weeks_t, weeks_t[1:]):
        pick = baskets(data, symbols, t, lookback_d)
        target = {} if pick is None else {**{s: WEIGHT for s in pick[0]}, **{s: -WEIGHT for s in pick[1]}}
        if any(data[s]["o1"].get(t_next) is None for s in target):
            target, pick = {}, None
        turnover = sum(abs(target.get(s, 0.0) - held.get(s, 0.0)) for s in set(target) | set(held))
        costs = turnover * (TAKER_FEE + SLIPPAGE)
        gross = funding = 0.0
        new_held: Dict[str, float] = {}
        for s, w in target.items():
            p0, p1 = data[s]["o1"][t], data[s]["o1"][t_next]
            gross += w * (p1 / p0 - 1)
            f = data[s]["fund"]
            for i in range(bisect.bisect_right(f["ts"], t), bisect.bisect_right(f["ts"], t_next)):
                funding += w * f["rate"][i] * data[s]["o1"].get(f["ts"][i], p0) / p0  # лонг платит положительную
            new_held[s] = w * p1 / p0
        out.append(Week(t, t_next, gross - costs - funding, gross, costs, funding,
                        pick[0] if pick else (), pick[1] if pick else ()))
        held = new_held
    if out and held:
        exit_cost = sum(abs(v) for v in held.values()) * (TAKER_FEE + SLIPPAGE)
        last = out[-1]
        last.r -= exit_cost
        last.costs += exit_cost
    return out


def evaluate(weeks: Sequence[Week], bootstrap: int = 2000) -> Dict:
    """Критерии И3 из плана; отрезки — три равные части периода недель."""
    n = len(weeks)
    if not n:
        return {"weeks": 0, "passed": False, "checks": {}}
    rs = [w.r for w in weeks]
    mean = sum(rs) / n
    low, high = report.expectancy_ci(weeks, bootstrap, alpha=ALPHA)
    parts, _ = report.splits(weeks[0].entry_t, weeks[-1].exit_t, holdout_days=0)
    segments = []
    for part in parts:
        chunk = report.within(weeks, part)
        segments.append(sum(w.r for w in chunk) / len(chunk) if chunk else None)
    drawdown = report.max_drawdown(rs)
    checks = {
        "weeks": n >= MIN_WEEKS,
        "ci": low > 0,
        "mean": mean >= MIN_MEAN,
        "segments": sum(1 for m in segments if m is not None and m > 0) >= 2,
        "drawdown": drawdown <= MAX_DRAWDOWN,
    }
    return {"weeks": n, "mean": mean, "ci": (low, high), "win_rate": sum(r > 0 for r in rs) / n,
            "segments": segments, "drawdown": drawdown, "checks": checks, "passed": all(checks.values()),
            "costs": sum(w.costs for w in weeks) / n, "funding": sum(w.funding for w in weeks) / n,
            "gross": sum(w.gross for w in weeks) / n}


def load(conn, symbols: Sequence[str], start_ms: int, end_ms: int) -> Dict:
    data = {}
    for s in symbols:
        c4 = {int(b[0]): float(b[4]) for b in history.load_candles(conn, s, "4h", start_ms - 40 * DAY_MS, end_ms)}
        o1 = {int(b[0]): float(b[1]) for b in history.load_candles(conn, s, "1h", start_ms, end_ms + HOUR_MS)}
        rows = [(int(ts), float(r)) for ts, r in conn.execute("SELECT ts, rate FROM funding WHERE symbol = ? ORDER BY ts", (s,))]
        data[s] = {"c4": c4, "o1": o1, "fund": {"ts": [r[0] for r in rows], "rate": [r[1] for r in rows]}}
    return data


def render(lookback_d: int, stats: Dict) -> str:
    title = f"L = {lookback_d:>2} дн"
    if not stats["weeks"]:
        return f"{title}: недель нет"
    low, high = stats["ci"]
    seg = " / ".join("—" if m is None else f"{100 * m:+.2f}" for m in stats["segments"])
    failed = [k for k, ok in stats["checks"].items() if not ok]
    return (f"{title}: {stats['weeks']} нед., средняя {100 * stats['mean']:+.2f} % капитала "
            f"[{100 * low:+.2f}; {100 * high:+.2f}] (97,5 %), плюсовых недель {100 * stats['win_rate']:.0f} %, "
            f"отрезки {seg} %, просадка {100 * stats['drawdown']:.1f} %; в неделю: до издержек "
            f"{100 * stats['gross']:+.2f} %, издержки {100 * stats['costs']:.2f} %, фандинг {100 * stats['funding']:+.3f} % → "
            f"{'ПРОХОДИТ' if stats['passed'] else 'не проходит: ' + ', '.join(failed)}")


def main(argv=None) -> int:
    import config
    parser = argparse.ArgumentParser(description="И3: кросс-секционный моментум")
    parser.add_argument("--db", default=str(history.DEFAULT_DB))
    parser.add_argument("--symbols", default=",".join(config.SYMBOLS))
    parser.add_argument("--holdout", action="store_true", help="показать отложенный конец (смотреть один раз)")
    args = parser.parse_args(argv)

    conn = history.connect(args.db)
    _, end_ms = fe.period(conn, 12)
    start_ms = day_ms(START)
    last = end_ms if args.holdout else end_ms - HOLDOUT_DAYS * DAY_MS
    weeks_t = mondays(start_ms, last)
    symbols = args.symbols.split(",")
    data = load(conn, symbols, start_ms, last)
    conn.close()

    print(f"И3, ребалансировки {datetime.fromtimestamp(weeks_t[0] / 1000, UTC):%d.%m.%Y}–"
          f"{datetime.fromtimestamp(weeks_t[-1] / 1000, UTC):%d.%m.%Y} "
          f"({'с отложенным концом' if args.holdout else 'без отложенного конца'}), лонг/шорт по {TOP}, {100 * WEIGHT:.0f} % на позицию")
    passed = False
    for lookback in LOOKBACKS_D:
        stats = evaluate(simulate(data, symbols, weeks_t, lookback))
        print(render(lookback, stats))
        passed = passed or stats["passed"]
    print("Вердикт И3:", "принимается" if passed else "не принимается")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
