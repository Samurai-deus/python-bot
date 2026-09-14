"""
И16 плана трейдера (docs/TRADER_PLAN.md): правила по барам — свечные паттерны, тренд/пробой,
откат к среднему × таймфреймы 1h/4h/1d × удержание × стоп × тейк. Правило, сетка, отбор и
критерии записаны в плане ДО прогона — здесь только исполнение.

    py -m backtest.bar_rules --db data/history_wide.db [--tf 1h,4h,1d] [--holdout] [--out report.json]

Сигнал — на закрытии бара, вход по open следующего; одна позиция на контракт; номинал 10 %
капитала. Выходы: удержание {1, 6, 24} бара, стоп и тейк {нет, 1, 2} × ATR(14) от входа —
проверяются по high/low каждого бара, при обоих в одном баре считается стоп. Издержки на
сторону, фандинг за удержание. Принимаются не варианты (их 1 296), а 9 склеенных серий
проверки вперёд по годам (семейство × таймфрейм), α = 0,05 / 9. Отложенный конец — тот же,
что у И15 (последние 26 недель), только с --holdout.
"""
import argparse
import bisect
import json
import math
import sys
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from backtest import history, report
from backtest import momentum_xs as mx
from backtest import wide_search as ws
from backtest.portfolio import SLIPPAGE, TAKER_FEE

DAY_MS, WEEK_MS, H4_MS = mx.DAY_MS, mx.WEEK_MS, mx.H4_MS
HOUR_MS = mx.HOUR_MS
TIMEFRAMES = {"1h": HOUR_MS, "4h": H4_MS, "1d": DAY_MS}
HOLDOUT_WEEKS = ws.HOLDOUT_WEEKS
START = ws.START
HORIZONS = (1, 6, 24)
STOPS = (0, 1, 2)
TAKES = (0, 1, 2)
ATR_N = 14
LOOK5 = 5                       # «после падения/роста» — закрытие ниже/выше закрытия 5 баров назад
FAMILIES = ("candle", "trend", "revert")
RULES = {
    "candle": ("bull_engulf", "bear_engulf", "hammer", "shooting_star", "doji_bull", "doji_bear",
               "soldiers", "crows", "morning_star", "evening_star"),
    "trend": ("donchian20", "donchian55", "macross_10_50", "macross_20_100"),
    "revert": ("rsi_30_70", "bollinger_20_2"),
}
N_SERIES = len(FAMILIES) * len(TIMEFRAMES)
ALPHA = 0.05 / N_SERIES
NOTIONAL = 0.10
MIN_TRADES = 300
MIN_MEAN = 0.002
MAX_DRAWDOWN = 0.25
FIRST_SELECTION_YEAR = ws.FIRST_SELECTION_YEAR
COST_SIDE = TAKER_FEE + SLIPPAGE


@dataclass
class Bars:
    ts: List[int]
    o: List[float]
    h: List[float]
    l: List[float]  # noqa: E741 — low
    c: List[float]
    fund_ts: List[int]
    fund_rate: List[float]
    first_day: int


@dataclass
class Trade:
    symbol: str
    entry_t: int
    exit_t: int
    r: float            # результат на номинал после издержек и фандинга
    side: int
    exit_kind: str      # stop / take / time

    @property
    def pnl(self) -> float:
        return self.r


# ---------------------------------------------------------------------------
# Индикаторы (простые средние; определения зафиксированы здесь)
# ---------------------------------------------------------------------------

def sma(x: Sequence[float], n: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(x)
    s = 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def atr(b: Bars, n: int = ATR_N) -> List[Optional[float]]:
    tr = [b.h[0] - b.l[0]] + [max(b.h[i] - b.l[i], abs(b.h[i] - b.c[i - 1]), abs(b.l[i] - b.c[i - 1])) for i in range(1, len(b.c))]
    return sma(tr, n)


def rsi(c: Sequence[float], n: int = 14) -> List[Optional[float]]:
    """RSI по простым средним приростов и потерь за n баров (вариант Катлера)."""
    gains = [0.0] + [max(c[i] - c[i - 1], 0.0) for i in range(1, len(c))]
    losses = [0.0] + [max(c[i - 1] - c[i], 0.0) for i in range(1, len(c))]
    g, lo = sma(gains, n), sma(losses, n)
    out: List[Optional[float]] = [None] * len(c)
    for i in range(len(c)):
        if g[i] is None or lo[i] is None:
            continue
        out[i] = 100.0 if lo[i] == 0 else 100.0 - 100.0 / (1 + g[i] / lo[i])
    return out


def rolling_max_prev(x: Sequence[float], n: int) -> List[Optional[float]]:
    """Максимум предыдущих n значений (без текущего)."""
    out: List[Optional[float]] = [None] * len(x)
    dq: deque = deque()
    for i in range(len(x)):
        if i >= n:
            out[i] = x[dq[0]]
        while dq and x[dq[-1]] <= x[i]:
            dq.pop()
        dq.append(i)
        while dq[0] <= i - n:
            dq.popleft()
    return out


def rolling_min_prev(x: Sequence[float], n: int) -> List[Optional[float]]:
    return [None if v is None else -v for v in rolling_max_prev([-v for v in x], n)]


def bollinger(c: Sequence[float], n: int = 20, k: float = 2.0) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    m = sma(c, n)
    up: List[Optional[float]] = [None] * len(c)
    low: List[Optional[float]] = [None] * len(c)
    for i in range(n - 1, len(c)):
        win = c[i - n + 1:i + 1]
        sd = math.sqrt(sum((v - m[i]) ** 2 for v in win) / n)
        up[i], low[i] = m[i] + k * sd, m[i] - k * sd
    return up, low


# ---------------------------------------------------------------------------
# Сигналы: +1 лонг, −1 шорт, 0 — нет, на закрытии бара i
# ---------------------------------------------------------------------------

def _body(b: Bars, i: int) -> float:
    return abs(b.c[i] - b.o[i])


def _range(b: Bars, i: int) -> float:
    return b.h[i] - b.l[i]


def _after_decline(b: Bars, i: int) -> bool:
    return i >= LOOK5 and b.c[i] < b.c[i - LOOK5]


def _after_rise(b: Bars, i: int) -> bool:
    return i >= LOOK5 and b.c[i] > b.c[i - LOOK5]


def candle_signal(rule: str, b: Bars, i: int) -> int:
    if i < 2:
        return 0
    o, h, low, c = b.o, b.h, b.l, b.c
    body, rng = _body(b, i), _range(b, i)
    if rule == "bull_engulf":
        return 1 if c[i - 1] < o[i - 1] and c[i] > o[i] and o[i] <= c[i - 1] and c[i] >= o[i - 1] else 0
    if rule == "bear_engulf":
        return -1 if c[i - 1] > o[i - 1] and c[i] < o[i] and o[i] >= c[i - 1] and c[i] <= o[i - 1] else 0
    lower = min(o[i], c[i]) - low[i]
    upper = h[i] - max(o[i], c[i])
    if rule == "hammer":
        return 1 if body > 0 and lower >= 2 * body and upper <= 0.5 * body and _after_decline(b, i) else 0
    if rule == "shooting_star":
        return -1 if body > 0 and upper >= 2 * body and lower <= 0.5 * body and _after_rise(b, i) else 0
    if rule == "doji_bull":
        return 1 if rng > 0 and body <= 0.1 * rng and _after_decline(b, i) else 0
    if rule == "doji_bear":
        return -1 if rng > 0 and body <= 0.1 * rng and _after_rise(b, i) else 0
    if rule in ("soldiers", "crows"):
        sign = 1 if rule == "soldiers" else -1
        for k in (i - 2, i - 1, i):
            if sign * (c[k] - o[k]) <= 0 or _range(b, k) <= 0 or _body(b, k) < 0.5 * _range(b, k):
                return 0
        ok = sign * (c[i] - c[i - 1]) > 0 and sign * (c[i - 1] - c[i - 2]) > 0
        return sign if ok else 0
    if rule in ("morning_star", "evening_star"):
        sign = 1 if rule == "morning_star" else -1
        first, mid = i - 2, i - 1
        if _range(b, first) <= 0 or sign * (o[first] - c[first]) <= 0 or _body(b, first) < 0.5 * _range(b, first):
            return 0
        if _body(b, mid) > 0.3 * _body(b, first):
            return 0
        midpoint = (o[first] + c[first]) / 2
        return sign if sign * (c[i] - o[i]) > 0 and sign * (c[i] - midpoint) > 0 else 0
    raise ValueError(rule)


class Indicators:
    def __init__(self, b: Bars):
        self.atr = atr(b)
        self.sma = {n: sma(b.c, n) for n in (10, 20, 50, 100)}
        self.rsi = rsi(b.c)
        self.bb_up, self.bb_low = bollinger(b.c)
        self.don_hi = {n: rolling_max_prev(b.h, n) for n in (20, 55)}
        self.don_lo = {n: rolling_min_prev(b.l, n) for n in (20, 55)}


def signal(rule: str, b: Bars, ind: Indicators, i: int) -> int:
    if rule in RULES["candle"]:
        return candle_signal(rule, b, i)
    if rule.startswith("donchian"):
        n = int(rule[len("donchian"):])
        hi, lo = ind.don_hi[n][i], ind.don_lo[n][i]
        if hi is None or lo is None:
            return 0
        return 1 if b.c[i] > hi else -1 if b.c[i] < lo else 0
    if rule.startswith("macross"):
        f, s = (int(x) for x in rule.split("_")[1:])
        fa, sa = ind.sma[f], ind.sma[s]
        if i < 1 or fa[i - 1] is None or sa[i - 1] is None or fa[i] is None or sa[i] is None:
            return 0
        if fa[i - 1] <= sa[i - 1] and fa[i] > sa[i]:
            return 1
        return -1 if fa[i - 1] >= sa[i - 1] and fa[i] < sa[i] else 0
    if rule == "rsi_30_70":
        r0, r1 = (ind.rsi[i - 1], ind.rsi[i]) if i >= 1 else (None, None)
        if r0 is None or r1 is None:
            return 0
        return 1 if r0 >= 30 > r1 else -1 if r0 <= 70 < r1 else 0
    if rule == "bollinger_20_2":
        up, low = ind.bb_up[i], ind.bb_low[i]
        if up is None:
            return 0
        return 1 if b.c[i] < low else -1 if b.c[i] > up else 0
    raise ValueError(rule)


# ---------------------------------------------------------------------------
# Сделки
# ---------------------------------------------------------------------------

def exit_key(horizon: int, stop: int, take: int) -> str:
    return f"h{horizon}_s{stop}_t{take}"


def exits_grid() -> List[Tuple[int, int, int]]:
    return [(hz, s, t) for hz in HORIZONS for s in STOPS for t in TAKES]


def outcome(b: Bars, i: int, side: int, atr_v: float) -> Dict[Tuple[int, int, int], Tuple[int, float, str]]:
    """
    Для сигнала на баре i: по каждому варианту выхода — (индекс бара выхода, цена выхода, вид).
    Вход по open бара i + 1. Стоп/тейк — по high/low баров входа и далее; оба в одном баре — стоп.
    """
    entry_i = i + 1
    entry = b.o[entry_i]
    max_h = max(HORIZONS)
    res: Dict[Tuple[int, int, int], Tuple[int, float, str]] = {}
    open_combos = {(s, t) for s in STOPS for t in TAKES}
    done: Dict[Tuple[int, int], Tuple[int, float, str]] = {}
    for k in range(max_h):
        j = entry_i + k
        if j >= len(b.c):
            break
        for combo in list(open_combos):
            s, t = combo
            stop_px = entry - side * s * atr_v if s else None
            take_px = entry + side * t * atr_v if t else None
            hit_stop = stop_px is not None and (b.l[j] <= stop_px if side > 0 else b.h[j] >= stop_px)
            hit_take = take_px is not None and (b.h[j] >= take_px if side > 0 else b.l[j] <= take_px)
            if hit_stop:
                done[combo] = (j, stop_px, "stop")
                open_combos.discard(combo)
            elif hit_take:
                done[combo] = (j, take_px, "take")
                open_combos.discard(combo)
        for hz in HORIZONS:
            if k + 1 == hz:
                for s in STOPS:
                    for t in TAKES:
                        if (s, t) in done and done[(s, t)][0] <= j:
                            res[(hz, s, t)] = done[(s, t)]
                        elif j + 1 < len(b.c):
                            res[(hz, s, t)] = (j + 1, b.o[j + 1], "time")
    return res


def funding_paid(b: Bars, side: int, t_in: int, t_out: int) -> float:
    lo, hi = bisect.bisect_right(b.fund_ts, t_in), bisect.bisect_right(b.fund_ts, t_out)
    return side * sum(b.fund_rate[lo:hi])


def trades_for_rule(symbol: str, b: Bars, ind: Indicators, rule: str, allowed: Optional[Iterable[int]] = None
                    ) -> Dict[Tuple[int, int, int], List[Trade]]:
    """Сделки по правилу для всех 27 выходов; одна позиция на контракт (сигнал при открытой — пропуск)."""
    allowed_set = None if allowed is None else set(allowed)
    grid = exits_grid()
    out: Dict[Tuple[int, int, int], List[Trade]] = {g: [] for g in grid}
    busy = {g: -1 for g in grid}
    for i in range(len(b.c) - 2):
        if allowed_set is not None and i not in allowed_set:
            continue
        side = signal(rule, b, ind, i)
        if not side or ind.atr[i] is None or ind.atr[i] <= 0:
            continue
        if all(busy[g] >= i + 1 for g in grid):
            continue
        res = outcome(b, i, side, ind.atr[i])
        entry = b.o[i + 1]
        for g, (j, px, kind) in res.items():
            if busy[g] >= i + 1:
                continue
            r = side * (px / entry - 1) - 2 * COST_SIDE - funding_paid(b, side, b.ts[i + 1], b.ts[j])
            out[g].append(Trade(symbol, b.ts[i + 1], b.ts[j], r, side, kind))
            busy[g] = j
    return out


# ---------------------------------------------------------------------------
# Данные
# ---------------------------------------------------------------------------

def bars_from_rows(rows: Sequence[Sequence[float]], fund: Tuple[List[int], List[float]]) -> Bars:
    ts = [int(r[0]) for r in rows]
    return Bars(ts, [float(r[1]) for r in rows], [float(r[2]) for r in rows], [float(r[3]) for r in rows],
                [float(r[4]) for r in rows], fund[0], fund[1], ts[0] // DAY_MS if ts else 0)


def daily_from_4h(rows: Sequence[Sequence[float]]) -> List[List[float]]:
    """Дневные бары из шести 4h-баров (UTC); неполные дни пропускаются."""
    days: Dict[int, List[Sequence[float]]] = {}
    for r in rows:
        days.setdefault(int(r[0]) // DAY_MS, []).append(r)
    out = []
    for d in sorted(days):
        bs = sorted(days[d], key=lambda r: int(r[0]))
        if len(bs) != 6:
            continue
        out.append([d * DAY_MS, float(bs[0][1]), max(float(x[2]) for x in bs), min(float(x[3]) for x in bs), float(bs[-1][4])])
    return out


def load_bars(conn, tf: str, symbols: Optional[Sequence[str]] = None) -> Dict[str, Bars]:
    src = "1h" if tf == "1h" else "4h"
    if symbols is None:
        symbols = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM candles WHERE interval = ? ORDER BY symbol", (src,))]
    data: Dict[str, Bars] = {}
    for sym in symbols:
        if not ws.is_crypto(sym):
            continue
        rows = conn.execute("SELECT ts, open, high, low, close FROM candles WHERE symbol = ? AND interval = ? ORDER BY ts",
                            (sym, src)).fetchall()
        if tf == "1d":
            rows = daily_from_4h(rows)
        if len(rows) < 200:
            continue
        f = [(int(a), float(b)) for a, b in conn.execute("SELECT ts, rate FROM funding WHERE symbol = ? ORDER BY ts", (sym,))]
        data[sym] = bars_from_rows(rows, ([x[0] for x in f], [x[1] for x in f]))
    return data


def weekly_universe(daily: Dict[str, ws.Series], weeks_t: Sequence[int]) -> Dict[int, set]:
    """Понедельник → пригодные контракты (правило И15) на неделю от него."""
    return {t: set(ws.eligible(daily, t, t_next)) for t, t_next in zip(weeks_t, weeks_t[1:])}


def allowed_bars(b: Bars, universe: Optional[Dict[int, set]], symbol: str, weeks_t: Sequence[int]) -> List[int]:
    """Индексы баров, на которых контракт входит во вселенную своей недели (и есть 100 дней истории)."""
    out = []
    first_ok = b.ts[0] + ws.MIN_HISTORY_D * DAY_MS
    for i, t in enumerate(b.ts):
        if t < first_ok or t < weeks_t[0]:
            continue
        if universe is not None:
            k = (t - weeks_t[0]) // WEEK_MS
            if k >= len(weeks_t) - 1 or symbol not in universe.get(weeks_t[k], ()):
                continue
        out.append(i)
    return out


# ---------------------------------------------------------------------------
# Отбор и критерии
# ---------------------------------------------------------------------------

def year_of(t: int) -> int:
    return datetime.fromtimestamp(t / 1000, UTC).year


def yearly_stats(trades: Iterable[Trade]) -> Dict[int, List[float]]:
    """год входа → [n, сумма, сумма квадратов]."""
    out: Dict[int, List[float]] = {}
    for tr in trades:
        y = out.setdefault(year_of(tr.entry_t), [0, 0.0, 0.0])
        y[0] += 1
        y[1] += tr.r
        y[2] += tr.r * tr.r
    return out


def prior_score(stats: Dict[int, List[float]], year: int) -> Optional[float]:
    n = sum(v[0] for y, v in stats.items() if y < year)
    if n < MIN_TRADES:
        return None
    s1 = sum(v[1] for y, v in stats.items() if y < year)
    s2 = sum(v[2] for y, v in stats.items() if y < year)
    mean = s1 / n
    var = (s2 - n * mean * mean) / (n - 1)
    return mean / math.sqrt(var) if var > 0 else None


def walk_forward(stats_by_cfg: Dict[str, Dict[int, List[float]]], last_year: int) -> Dict[int, str]:
    """Год → вариант, лучший по сделкам строго до 1 января этого года."""
    chosen: Dict[int, str] = {}
    for year in range(FIRST_SELECTION_YEAR, last_year + 1):
        best, best_sc = None, None
        for cfg, st in stats_by_cfg.items():
            sc = prior_score(st, year)
            if sc is not None and (best_sc is None or sc > best_sc):
                best, best_sc = cfg, sc
        if best is not None:
            chosen[year] = best
    return chosen


def evaluate(trades: Sequence[Trade], alpha: float = ALPHA, bootstrap: int = 2000) -> Dict:
    n = len(trades)
    if not n:
        return {"trades": 0, "passed": False, "checks": {}}
    trades = sorted(trades, key=lambda t: t.entry_t)
    rs = [t.r for t in trades]
    mean = sum(rs) / n
    low, high = report.expectancy_ci(trades, bootstrap, alpha=alpha)
    parts, _ = report.splits(trades[0].entry_t, trades[-1].exit_t, holdout_days=0)
    segments = []
    for part in parts:
        chunk = report.within(trades, part)
        segments.append(sum(t.r for t in chunk) / len(chunk) if chunk else None)
    drawdown = report.max_drawdown([NOTIONAL * r for r in rs])
    sd = math.sqrt(sum((r - mean) ** 2 for r in rs) / (n - 1)) if n > 1 else 0.0
    checks = {"trades": n >= MIN_TRADES, "ci": low > 0, "mean": mean >= MIN_MEAN,
              "segments": sum(1 for m in segments if m is not None and m > 0) >= 2, "drawdown": drawdown <= MAX_DRAWDOWN}
    return {"trades": n, "mean": mean, "sd": sd, "ci": (low, high), "win_rate": sum(r > 0 for r in rs) / n,
            "segments": segments, "drawdown": drawdown, "checks": checks, "passed": all(checks.values()),
            "stops": sum(t.exit_kind == "stop" for t in trades) / n, "takes": sum(t.exit_kind == "take" for t in trades) / n}


def holdout_check(visible_mean: float, holdout: Sequence[Trade]) -> Dict:
    n = len(holdout)
    if n < 2:
        return {"trades": n, "passed": False}
    rs = [t.r for t in holdout]
    mean = sum(rs) / n
    se = math.sqrt(sum((r - mean) ** 2 for r in rs) / (n - 1) / n)
    dd = report.max_drawdown([NOTIONAL * r for r in rs])
    return {"trades": n, "mean": mean, "se": se, "threshold": visible_mean - 2 * se, "drawdown": dd,
            "passed": mean >= visible_mean - 2 * se and dd <= MAX_DRAWDOWN}


# ---------------------------------------------------------------------------
# Прогон
# ---------------------------------------------------------------------------

def cfg_name(tf: str, rule: str, g: Tuple[int, int, int]) -> str:
    return f"{tf}:{rule}:{exit_key(*g)}"


def run_timeframe(tf: str, data: Dict[str, Bars], allowed: Dict[str, List[int]], holdout_start: int,
                  show_holdout: bool, log=None) -> Dict:
    """Все правила таймфрейма: годовая статистика по вариантам, проверка вперёд по семействам."""
    families = {fam: {} for fam in FAMILIES}
    table = {}
    keep: Dict[str, List[Trade]] = {}      # сделки хранятся только для вариантов, которые понадобятся
    all_stats: Dict[str, Dict[int, List[float]]] = {}
    for fam, rules in RULES.items():
        for rule in rules:
            per_cfg: Dict[str, List[Trade]] = {}
            for sym, b in data.items():
                ind = Indicators(b)
                for g, trades in trades_for_rule(sym, b, ind, rule, allowed[sym]).items():
                    per_cfg.setdefault(cfg_name(tf, rule, g), []).extend(trades)
            for name, trades in per_cfg.items():
                visible = [t for t in trades if t.entry_t < holdout_start]
                st = yearly_stats(visible)
                all_stats[name] = st
                families[fam][name] = st
                n = sum(v[0] for v in st.values())
                mean = sum(v[1] for v in st.values()) / n if n else None
                table[name] = {"trades": n, "mean": mean}
                keep[name] = trades
            if log:
                log(f"  {tf} {rule}: {sum(len(v) for v in per_cfg.values())} сделок по 27 вариантам")
    out = {"table": table, "series": {}}
    last_year = year_of(holdout_start)
    for fam in FAMILIES:
        chosen = walk_forward(families[fam], last_year)
        series: List[Trade] = []
        for year, name in chosen.items():
            series += [t for t in keep[name] if year_of(t.entry_t) == year and t.entry_t < holdout_start]
        st = evaluate(series)
        entry = {"chosen": chosen, "stats": st}
        if st["passed"] and show_holdout:
            hold_all = walk_forward(families[fam], datetime.now(UTC).year)
            hold = [t for y, name in hold_all.items() for t in keep[name] if year_of(t.entry_t) == y and t.entry_t >= holdout_start]
            entry["holdout"] = holdout_check(st["mean"], hold)
        out["series"][f"{fam}@{tf}"] = entry
    return out


def fmt_stats(st: Dict, alpha: float = ALPHA) -> str:
    if not st["trades"]:
        return "сделок нет"
    low, high = st["ci"]
    seg = " / ".join("—" if m is None else f"{100 * m:+.2f}" for m in st["segments"])
    failed = [k for k, ok in st["checks"].items() if not ok]
    return (f"{st['trades']} сделок, средняя {100 * st['mean']:+.3f} % номинала [{100 * low:+.3f}; {100 * high:+.3f}] "
            f"({100 * (1 - alpha):.2f} %), плюсовых {100 * st['win_rate']:.0f} %, отрезки {seg} %, просадка портфеля "
            f"{100 * st['drawdown']:.1f} %, по стопу {100 * st['stops']:.0f} %, по тейку {100 * st['takes']:.0f} % → "
            f"{'ПРОХОДИТ' if st['passed'] else 'не проходит: ' + ', '.join(failed)}")


def render(out: Dict) -> str:
    lines = []
    for tf, res in out["timeframes"].items():
        rows = sorted(((v["mean"], v["trades"], k) for k, v in res["table"].items() if v["trades"]), reverse=True)
        lines.append(f"{tf}: {len(res['table'])} вариантов; лучшие 5 по средней (описание, без поправки):")
        for mean, n, k in rows[:5]:
            lines.append(f"    {k:40} {n:6} сделок, {100 * mean:+.3f} %")
        lines.append("  худшие 3:")
        for mean, n, k in rows[-3:]:
            lines.append(f"    {k:40} {n:6} сделок, {100 * mean:+.3f} %")
    lines.append("")
    lines.append(f"Проверка вперёд по годам, 9 серий (интервал {100 * (1 - ALPHA):.2f} %):")
    for tf, res in out["timeframes"].items():
        for key, e in res["series"].items():
            lines.append(f"  {key}: {fmt_stats(e['stats'])}")
            lines.append("    выбор по годам: " + ", ".join(f"{y}: {c}" for y, c in e["chosen"].items()))
            if "holdout" in e:
                h = e["holdout"]
                lines.append(f"    отложенный конец: {h['trades']} сделок, средняя {100 * h.get('mean', 0):+.3f} % "
                             f"(порог {100 * h.get('threshold', 0):+.3f} %), просадка {100 * h.get('drawdown', 0):.1f} % → "
                             f"{'не противоречит' if h['passed'] else 'ПРОТИВОРЕЧИЕ'}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="И16: правила по барам")
    parser.add_argument("--db", default=str(history.DEFAULT_DB.with_name("history_wide.db")))
    parser.add_argument("--tf", default="1h,4h,1d")
    parser.add_argument("--holdout", action="store_true")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    conn = history.connect(args.db)
    start_ms = mx.day_ms(START)
    last = conn.execute("SELECT MAX(ts) FROM candles WHERE interval = '4h' AND symbol = 'BTCUSDT'").fetchone()[0]
    end_ms = int(last) + H4_MS
    weeks_all = mx.mondays(start_ms, end_ms)
    holdout_start = weeks_all[-1] - HOLDOUT_WEEKS * WEEK_MS
    daily = ws.load(conn, start_ms, end_ms)
    universe = weekly_universe(daily, weeks_all)
    log = lambda s: print(s, file=sys.stderr, flush=True)  # noqa: E731
    out = {"holdout_opened": args.holdout, "holdout_start": holdout_start, "timeframes": {}}
    for tf in args.tf.split(","):
        data = load_bars(conn, tf)
        allowed = {sym: allowed_bars(b, None if tf == "1h" else universe, sym, weeks_all) for sym, b in data.items()}
        log(f"{tf}: контрактов {len(data)}")
        out["timeframes"][tf] = run_timeframe(tf, data, allowed, holdout_start, args.holdout, log)
    conn.close()
    print(f"И16: недели {datetime.fromtimestamp(weeks_all[0] / 1000, UTC):%d.%m.%Y}–{datetime.fromtimestamp(weeks_all[-1] / 1000, UTC):%d.%m.%Y}, "
          f"отложенный конец с {datetime.fromtimestamp(holdout_start / 1000, UTC):%d.%m.%Y} ({'открыт' if args.holdout else 'закрыт'})")
    print(render(out))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
