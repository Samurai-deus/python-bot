"""
И6 плана трейдера (docs/TRADER_PLAN.md, «Исследование преимущества»): арбитраж пар. Правило и
критерии записаны в плане ДО прогона — здесь только исполнение.

    py -m backtest.pairs_xs

Раз в неделю (понедельник 00:00 UTC) по закрытым 4h за 60 дней: 5 пар с наибольшей корреляцией
4h-доходностей (не ниже 0,7, без общих монет), β — наклон МНК ln A на ln B в (0; 2], среднее и
разброс спреда ln A − β·ln B. Внутри недели по закрытым 4h: вход при 2 ≤ |z| < 4 по open
следующего бара (спред выше среднего — шорт A, лонг B), выход при |z| ≤ 0,5, стоп при |z| ≥ 4 —
после стопа пара до конца недели не торгуется, в понедельник 00:00 всё закрывается. Нога A — 10 % капитала, нога B — 10 % × β; издержки на каждое исполнение,
фандинг обеих ног. Результат недели — доля капитала.
"""
import argparse
import bisect
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import combinations
from typing import Dict, List, Optional, Sequence, Tuple

from backtest import funding_events as fe
from backtest import history, report
from backtest import momentum_xs as mx
from backtest.portfolio import SLIPPAGE, TAKER_FEE

DAY_MS, H4_MS, WEEK_MS = mx.DAY_MS, mx.H4_MS, mx.WEEK_MS
WINDOW_D = 60
BARS = WINDOW_D * 6
PAIRS = 5
MIN_CORR = 0.7
MAX_BETA = 2.0
Z_IN, Z_OUT, Z_STOP = 2.0, 0.5, 4.0
LEG = 0.10
ALPHA = 0.05                       # один вариант — интервал 95 %
MIN_WEEKS = 150
MIN_MEAN = 0.0015
MAX_DRAWDOWN = 0.15
START = "2022-10-03"
HOLDOUT_DAYS = 75


@dataclass
class Pair:
    a: str
    b: str
    corr: float
    beta: float
    mean: float
    sd: float


def ols_slope(x: Sequence[float], y: Sequence[float]) -> float:
    """Наклон МНК y = α + β·x."""
    n = len(x)
    mx_, my = sum(x) / n, sum(y) / n
    sxx = sum((v - mx_) ** 2 for v in x)
    return sum((u - mx_) * (v - my) for u, v in zip(x, y)) / sxx if sxx else 0.0


def corr(x: Sequence[float], y: Sequence[float]) -> float:
    n = len(x)
    mx_, my = sum(x) / n, sum(y) / n
    sx = math.sqrt(sum((v - mx_) ** 2 for v in x))
    sy = math.sqrt(sum((v - my) ** 2 for v in y))
    return sum((u - mx_) * (v - my) for u, v in zip(x, y)) / (sx * sy) if sx and sy else 0.0


def formation(data: Dict, symbols: Sequence[str], t: int) -> List[Pair]:
    """Пары на неделю с момента t — по 4h-барам, закрывшимся к t."""
    logs = {}
    for s in symbols:
        d = data.get(s)
        if not d:
            continue
        closes = [d["c4"].get(t - k * H4_MS) for k in range(BARS, 0, -1)]
        if all(c and c > 0 for c in closes):
            logs[s] = [math.log(c) for c in closes]
    candidates = []
    for a, b in combinations(sorted(logs), 2):
        la, lb = logs[a], logs[b]
        rho = corr([la[k + 1] - la[k] for k in range(BARS - 1)], [lb[k + 1] - lb[k] for k in range(BARS - 1)])
        if rho < MIN_CORR:
            continue
        beta = ols_slope(lb, la)
        if not 0 < beta <= MAX_BETA:
            continue
        spread = [u - beta * v for u, v in zip(la, lb)]
        mean = sum(spread) / BARS
        sd = math.sqrt(sum((x - mean) ** 2 for x in spread) / (BARS - 1))
        if sd > 0:
            candidates.append(Pair(a, b, rho, beta, mean, sd))
    candidates.sort(key=lambda p: (-p.corr, p.a, p.b))
    used, picked = set(), []
    for p in candidates:
        if p.a in used or p.b in used:
            continue
        picked.append(p)
        used |= {p.a, p.b}
        if len(picked) == PAIRS:
            break
    return picked


def _leg(data: Dict, s: str, notional: float, t_in: int, t_out: int) -> Optional[Tuple[float, float, float]]:
    """Нога с номиналом notional (+ лонг, − шорт) от open t_in до open t_out: (итог, издержки, фандинг)."""
    o_in, o_out = data[s]["o4"].get(t_in), data[s]["o4"].get(t_out)
    if not o_in or not o_out:
        return None
    side = 1 if notional > 0 else -1
    size = abs(notional)
    fill_in = o_in * (1 + side * SLIPPAGE)
    fill_out = o_out * (1 - side * SLIPPAGE)
    gross = size * side * (fill_out / fill_in - 1)
    costs = size * TAKER_FEE * (1 + fill_out / fill_in)
    f = data[s]["fund"]
    funding = 0.0
    for i in range(bisect.bisect_right(f["ts"], t_in), bisect.bisect_right(f["ts"], t_out)):
        funding += notional * f["rate"][i] * data[s]["o4"].get(f["ts"][i], o_in) / o_in
    return gross - costs - funding, costs, funding


def trade_pair(data: Dict, p: Pair, t: int, t_next: int) -> List[Tuple[int, int, float, float, float]]:
    """Сделки пары внутри недели [t, t_next): (вход, выход, итог, издержки, фандинг) в долях капитала."""
    trades = []
    pos, t_in = 0, None  # pos: +1 — лонг спреда (лонг A, шорт B), −1 — шорт спреда
    stopped = False      # после стопа пара до конца недели не торгуется
    ca, cb = data[p.a]["c4"], data[p.b]["c4"]

    def close(t_out):
        a = _leg(data, p.a, LEG * pos, t_in, t_out)
        b = _leg(data, p.b, -LEG * p.beta * pos, t_in, t_out)
        if a and b:
            trades.append((t_in, t_out, a[0] + b[0], a[1] + b[1], a[2] + b[2]))

    for bar in range(t, t_next, H4_MS):
        decide_at = bar + H4_MS          # бар закрылся — решение, исполнение по open следующего бара
        if decide_at >= t_next:
            break
        x, y = ca.get(bar), cb.get(bar)
        if not x or not y:
            continue
        z = (math.log(x) - p.beta * math.log(y) - p.mean) / p.sd
        if pos == 0 and not stopped and Z_IN <= abs(z) < Z_STOP:
            pos, t_in = (-1 if z > 0 else 1), decide_at
        elif pos != 0 and (abs(z) <= Z_OUT or abs(z) >= Z_STOP):
            close(decide_at)
            stopped = abs(z) >= Z_STOP
            pos, t_in = 0, None
    if pos != 0:
        close(t_next)
    return trades


def simulate(data: Dict, symbols: Sequence[str], weeks_t: Sequence[int]) -> List[mx.Week]:
    out = []
    for t, t_next in zip(weeks_t, weeks_t[1:]):
        pairs = formation(data, symbols, t)
        trades = [tr for p in pairs for tr in trade_pair(data, p, t, t_next)]
        r = sum(tr[2] for tr in trades)
        costs = sum(tr[3] for tr in trades)
        funding = sum(tr[4] for tr in trades)
        out.append(mx.Week(t, t_next, r, r + costs + funding, costs, funding,
                           tuple(f"{p.a}/{p.b}" for p in pairs), (str(len(trades)),)))  # пары и число сделок
    return out


def evaluate(weeks: Sequence[mx.Week], bootstrap: int = 2000, alpha: float = ALPHA) -> Dict:
    """Критерии И6 из плана; отрезки — три равные части периода недель."""
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
        bars = history.load_candles(conn, s, "4h", start_ms - (WINDOW_D + 5) * DAY_MS, end_ms + H4_MS)
        rows = [(int(ts), float(r)) for ts, r in conn.execute("SELECT ts, rate FROM funding WHERE symbol = ? ORDER BY ts", (s,))]
        data[s] = {"c4": {int(b[0]): float(b[4]) for b in bars}, "o4": {int(b[0]): float(b[1]) for b in bars},
                   "fund": {"ts": [r[0] for r in rows], "rate": [r[1] for r in rows]}}
    return data


def main(argv=None) -> int:
    import config
    parser = argparse.ArgumentParser(description="И6: арбитраж пар")
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

    weeks = simulate(data, symbols, weeks_t)
    stats = evaluate(weeks)
    trades = sum(int(w.shorts[0]) for w in weeks)
    print(f"И6, недели {datetime.fromtimestamp(weeks_t[0] / 1000, UTC):%d.%m.%Y}–"
          f"{datetime.fromtimestamp(weeks_t[-1] / 1000, UTC):%d.%m.%Y} "
          f"({'с отложенным концом' if args.holdout else 'без отложенного конца'}), 5 пар, вход |z| ≥ 2")
    if not stats["weeks"]:
        print("недель нет")
        return 0
    low, high = stats["ci"]
    seg = " / ".join("—" if m is None else f"{100 * m:+.2f}" for m in stats["segments"])
    failed = [k for k, ok in stats["checks"].items() if not ok]
    print(f"{stats['weeks']} нед., сделок {trades}, средняя {100 * stats['mean']:+.3f} % капитала "
          f"[{100 * low:+.3f}; {100 * high:+.3f}] (95 %), разброс {100 * stats['sd']:.2f} %, плюсовых недель "
          f"{100 * stats['win_rate']:.0f} %, отрезки {seg} %, просадка {100 * stats['drawdown']:.1f} %; в неделю: "
          f"до издержек {100 * stats['gross']:+.3f} %, издержки {100 * stats['costs']:.3f} %, фандинг "
          f"{100 * stats['funding']:+.3f} %; недель без пар {sum(1 for w in weeks if not w.longs)}")
    print("Вердикт И6:", "принимается" if stats["passed"] else "не принимается: " + ", ".join(failed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
