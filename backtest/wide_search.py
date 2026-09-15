"""
И15 плана трейдера (docs/TRADER_PLAN.md): широкий поиск — шесть семейств недельных правил ×
вся вселенная бессрочных USDT-контрактов Bybit × сетки параметров (70 вариантов). Правило,
сетки, отбор и критерии записаны в плане ДО прогона — здесь только исполнение.

    py -m backtest.wide_search --db data/history_wide.db [--holdout] [--out report.json]

Принимается не лучший вариант таблицы, а склеенная серия проверки вперёд по времени: на
каждый год (с 2023) вариант семейства выбирается по всем предыдущим неделям — по отношению
средней к разбросу после издержек. Шесть проверок, интервал с поправкой α = 0,05 / 6.
Вселенная в момент t — контракты с ≥ 100 днями истории и средним дневным оборотом за 30 дней
≥ 2 млн USDT на тот момент (не по сегодняшнему дню). Результат недели — доля капитала при
валовой экспозиции 1. Фандинг считается на номинал входа (цена в момент выплаты ≈ цена входа —
приближение, как в И12). Отложенный конец — последние 26 недель, только с --holdout.
"""
import argparse
import bisect
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Dict, List, Optional, Sequence, Tuple

from backtest import history, report
from backtest import momentum_xs as mx
from backtest.portfolio import SLIPPAGE, TAKER_FEE

DAY_MS, WEEK_MS = mx.DAY_MS, mx.WEEK_MS
START = "2021-01-04"
HOLDOUT_WEEKS = 26
MIN_HISTORY_D = 100
MIN_TURNOVER = 2_000_000.0          # средний дневной оборот за TURNOVER_D дней, USDT
TURNOVER_D = 30
TOPS = {"top10": 10, "top30": 30, "all": None}
MIN_BASKET = 5
FIRST_SELECTION_YEAR = 2023
MIN_SELECTION_WEEKS = 52
MIN_WEEKS = 150
MIN_MEAN = 0.0015
MAX_DRAWDOWN = 0.25
FAMILIES = ("trend", "momentum", "reversal", "funding", "lowvol", "breakout")
ALPHA = 0.05 / len(FAMILIES)
TREND_TARGET, TREND_CAP, TREND_VOL_D = 0.15, 0.30, 30
BREAKOUT_VOLUME = 2.0               # оборот за неделю не меньше 2 × средненедельного за 30 дней
SLIPPAGE_STRESS = 0.0015
# Не крипто-риск — исключаются по именам инструментов (список зафиксирован до прогона, 14.09.2026):
# золото/серебро/нефть/акции/индексы (на Bybit с 2025 года — в вселенной старше года их нет, список на будущее),
# стейблкоины и токены на золото (USDC, USDE, USD1, PAXG, XAUT), кросс-курс ETHBTC. SPX (SPX6900) и STRK
# (Starknet) — крипта, в список не входят.
NON_CRYPTO_BASES = frozenset({
    "XAU", "XAG", "CL", "SOXL", "SOXS", "TSLA", "GOOGL", "INTC", "META", "NVDA", "AAPL", "MSFT", "AMZN", "COIN",
    "MSTR", "SAMSUNG", "DRAM", "SNDK", "KORU", "NBIS", "4STOCK", "LAB", "HOOD", "PLTR", "AMD", "NFLX", "CRCL",
    "NDX", "DJI", "SPY", "QQQ", "TQQQ", "SQQQ", "GLD", "SLV", "USO", "BRENT", "WTI", "NG", "COPPER", "HG",
    "MAG7", "AI7", "US30", "US500", "US100", "DXY", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "ORCL", "AVGO",
    "TSM", "BABA", "PDD", "NIO", "GME", "AMC", "UNH", "JPM", "XOM", "LLY", "BRK", "GOOG", "NVO", "ASML",
    "SMCI", "ARM", "MU", "QCOM", "IBM", "CSCO", "ADBE", "CRM", "PYPL", "UBER", "ABNB", "SHOP", "SNOW", "RIOT",
    "MARA", "CLSK", "HUT", "BITF", "IREN", "CIFR", "WULF", "BTBT", "SBET", "BMNR", "DFDV", "UPXI", "NAKA", "STRC",
    "STRF", "STRD", "TSLL", "NVDL", "GGLL", "GOLD", "SILVER", "OIL",
    "SKHYNIX", "TSMC", "TENCENT", "ALIBABA", "XIAOMI", "BYD", "SONY", "TOYOTA", "HYUNDAI", "NINTENDO", "SOFTBANK",
    "USDC", "USDE", "USD1", "PAXG", "XAUT", "ETHBTC",
})


def base_of(symbol: str) -> str:
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    while base and base[0].isdigit():
        base = base[1:]
    return base


def is_crypto(symbol: str) -> bool:
    return base_of(symbol) not in NON_CRYPTO_BASES


@dataclass
class Series:
    close: Dict[int, float] = field(default_factory=dict)     # день (t // DAY_MS) → закрытие последнего 4h-бара дня
    turnover: Dict[int, float] = field(default_factory=dict)  # день → оборот за день, USDT
    open: Dict[int, float] = field(default_factory=dict)      # ts бара 00:00 UTC → open
    fund_ts: List[int] = field(default_factory=list)
    fund_rate: List[float] = field(default_factory=list)
    first_day: Optional[int] = None


def grid() -> List[dict]:
    """70 вариантов, зафиксированных в плане."""
    g: List[dict] = []
    for L in (10, 20, 30, 60, 90, 180):
        for u in ("top10", "top30", "all"):
            g.append({"family": "trend", "L": L, "universe": u})
    for L in (7, 14, 28, 56, 90):
        for f in (0.1, 0.2):
            for u in ("top30", "all"):
                g.append({"family": "momentum", "L": L, "frac": f, "universe": u})
    for L in (1, 3, 7):
        for f in (0.1, 0.2):
            for u in ("top30", "all"):
                g.append({"family": "reversal", "L": L, "frac": f, "universe": u})
    for W in (3, 7, 14):
        for f in (0.1, 0.2):
            for u in ("top30", "all"):
                g.append({"family": "funding", "W": W, "frac": f, "universe": u})
    for W in (30, 90):
        for f in (0.1, 0.2):
            g.append({"family": "lowvol", "W": W, "frac": f, "universe": "all"})
    for N in (20, 55):
        for u in ("top30", "all"):
            g.append({"family": "breakout", "N": N, "universe": u})
    return g


def label(cfg: dict) -> str:
    keys = [k for k in ("L", "W", "N", "frac") if k in cfg]
    parts = [f"{k}={cfg[k]:.0%}" if k == "frac" else f"{k}={cfg[k]}" for k in keys]
    return f"{cfg['family']}({', '.join(parts)}, {cfg['universe']})"


# ---------------------------------------------------------------------------
# Признаки по дневным рядам
# ---------------------------------------------------------------------------

def ret(s: Series, d: int, lookback_d: int) -> Optional[float]:
    """Доходность за lookback_d дней к закрытию дня d."""
    a, b = s.close.get(d - lookback_d), s.close.get(d)
    return None if not a or not b else b / a - 1


def vol(s: Series, d: int, window_d: int) -> Optional[float]:
    """Годовая волатильность по window_d дневным лог-доходностям к закрытию дня d."""
    closes = [s.close.get(d - k) for k in range(window_d + 1)]
    if any(not c for c in closes):
        return None
    rets = [math.log(closes[k] / closes[k + 1]) for k in range(window_d)]
    mean = sum(rets) / window_d
    var = sum((r - mean) ** 2 for r in rets) / (window_d - 1)
    return math.sqrt(var * 365) if var > 0 else None


def mean_turnover(s: Series, d: int, window_d: int = TURNOVER_D) -> Optional[float]:
    """Средний дневной оборот за window_d дней до дня d включительно; нужно не меньше 2/3 дней."""
    vals = [s.turnover[d - k] for k in range(window_d) if (d - k) in s.turnover]
    return sum(vals) / len(vals) if len(vals) * 3 >= window_d * 2 else None


def funding_mean(s: Series, t: int, window_d: int) -> Optional[float]:
    lo, hi = bisect.bisect_left(s.fund_ts, t - window_d * DAY_MS), bisect.bisect_left(s.fund_ts, t)
    return sum(s.fund_rate[lo:hi]) / (hi - lo) if hi > lo else None


def at_extreme(s: Series, d: int, n_d: int) -> Optional[int]:
    """+1 — закрытие дня d на максимуме n_d дней, −1 — на минимуме, 0 — ни то ни другое."""
    closes = [s.close.get(d - k) for k in range(n_d + 1)]
    if any(not c for c in closes):
        return None
    return 1 if closes[0] >= max(closes) else -1 if closes[0] <= min(closes) else 0


def volume_surge(s: Series, d: int) -> bool:
    week = [s.turnover.get(d - k) for k in range(7)]
    avg = mean_turnover(s, d)
    return avg is not None and all(v is not None for v in week) and sum(week) >= BREAKOUT_VOLUME * avg * 7


# ---------------------------------------------------------------------------
# Вселенная и веса
# ---------------------------------------------------------------------------

def eligible(data: Dict[str, Series], t: int, t_next: int) -> List[str]:
    """Контракты, пригодные на неделю [t, t_next), по обороту за 30 дней по убыванию."""
    d = t // DAY_MS - 1
    scored = []
    for sym, s in data.items():
        if s.first_day is None or d - s.first_day + 1 < MIN_HISTORY_D or not s.close.get(d):
            continue
        if not s.open.get(t) or not s.open.get(t_next):
            continue
        turn = mean_turnover(s, d)
        if turn is None or turn < MIN_TURNOVER:
            continue
        scored.append((turn, sym))
    scored.sort(reverse=True)
    return [sym for _, sym in scored]


def universe(data: Dict[str, Series], t: int, t_next: int, name: str) -> List[str]:
    syms = eligible(data, t, t_next)
    top = TOPS[name]
    return syms if top is None else syms[:top]


def normalised(raw: Dict[str, float]) -> Dict[str, float]:
    gross = sum(abs(w) for w in raw.values())
    return {s: w / gross for s, w in raw.items() if w} if gross > 0 else {}


def long_short(scores: Dict[str, float], frac: float, long_high: bool) -> Dict[str, float]:
    """Верхняя и нижняя доли по значению признака, по 0,5 капитала на сторону."""
    n = max(MIN_BASKET, round(frac * len(scores)))
    if len(scores) < 2 * n:
        return {}
    ranked = sorted(scores, key=scores.get)
    low, high = ranked[:n], ranked[-n:]
    longs, shorts = (high, low) if long_high else (low, high)
    return {**{s: 0.5 / n for s in longs}, **{s: -0.5 / n for s in shorts}}


def weights(data: Dict[str, Series], cfg: dict, t: int, t_next: int) -> Dict[str, float]:
    """Целевые веса на неделю [t, t_next): валовая экспозиция 1 или пусто."""
    d = t // DAY_MS - 1
    syms = universe(data, t, t_next, cfg["universe"])
    fam = cfg["family"]
    if fam == "trend":
        ready = {}
        for s in syms:
            r, v = ret(data[s], d, cfg["L"]), vol(data[s], d, TREND_VOL_D)
            if r is not None and v is not None and r != 0:
                ready[s] = (1 if r > 0 else -1, v)
        k = len(ready)
        return normalised({s: sign * min(TREND_CAP, TREND_TARGET / (v * k)) for s, (sign, v) in ready.items()})
    if fam == "momentum":
        scores = {s: r for s in syms if (r := ret(data[s], d - 1, cfg["L"])) is not None}  # без последней суток
        return long_short(scores, cfg["frac"], long_high=True)
    if fam == "reversal":
        scores = {s: r for s in syms if (r := ret(data[s], d, cfg["L"])) is not None}
        return long_short(scores, cfg["frac"], long_high=False)
    if fam == "continuation":  # И17а: зеркало разворота — доходность за последние L дней без пропуска, лонг лучшим
        scores = {s: r for s in syms if (r := ret(data[s], d, cfg["L"])) is not None}
        return long_short(scores, cfg["frac"], long_high=True)
    if fam == "funding":
        scores = {s: f for s in syms if (f := funding_mean(data[s], t, cfg["W"])) is not None}
        return long_short(scores, cfg["frac"], long_high=False)  # шорт тем, кто платит
    if fam == "lowvol":
        scores = {s: v for s in syms if (v := vol(data[s], d, cfg["W"])) is not None}
        return long_short(scores, cfg["frac"], long_high=False)
    if fam == "breakout":
        longs, shorts = [], []
        for s in syms:
            side = at_extreme(data[s], d, cfg["N"])
            if side and volume_surge(data[s], d):
                (longs if side > 0 else shorts).append(s)
        if longs and shorts:
            return {**{s: 0.5 / len(longs) for s in longs}, **{s: -0.5 / len(shorts) for s in shorts}}
        side = longs or shorts
        return {s: (1.0 if longs else -1.0) / len(side) for s in side} if side else {}
    raise ValueError(fam)


# ---------------------------------------------------------------------------
# Прогон
# ---------------------------------------------------------------------------

def simulate(data: Dict[str, Series], cfg: dict, weeks_t: Sequence[int], slippage: float = SLIPPAGE) -> List[mx.Week]:
    out: List[mx.Week] = []
    held: Dict[str, float] = {}
    for t, t_next in zip(weeks_t, weeks_t[1:]):
        target = weights(data, cfg, t, t_next)
        turnover = sum(abs(target.get(s, 0.0) - held.get(s, 0.0)) for s in set(target) | set(held))
        costs = turnover * (TAKER_FEE + slippage)
        gross = funding = 0.0
        new_held: Dict[str, float] = {}
        for s, w in target.items():
            p0, p1 = data[s].open[t], data[s].open[t_next]
            gross += w * (p1 / p0 - 1)
            f = data[s]
            for i in range(bisect.bisect_right(f.fund_ts, t), bisect.bisect_right(f.fund_ts, t_next)):
                funding += w * f.fund_rate[i]  # лонг платит положительную ставку; на номинал входа
            new_held[s] = w * p1 / p0
        out.append(mx.Week(t, t_next, gross - costs - funding, gross, costs, funding,
                           tuple(s for s, w in target.items() if w > 0), tuple(s for s, w in target.items() if w < 0)))
        held = new_held
    if out and held:
        exit_cost = sum(abs(v) for v in held.values()) * (TAKER_FEE + slippage)
        out[-1].r -= exit_cost
        out[-1].costs += exit_cost
    return out


def score(weeks: Sequence[mx.Week]) -> Optional[float]:
    """Средняя к разбросу недельного результата — мера для выбора варианта по прошлым неделям."""
    n = len(weeks)
    if n < MIN_SELECTION_WEEKS:
        return None
    rs = [w.r for w in weeks]
    mean = sum(rs) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in rs) / (n - 1))
    return mean / sd if sd > 0 else None


def year_start_ms(year: int) -> int:
    return int(datetime(year, 1, 1, tzinfo=UTC).timestamp() * 1000)


def walk_forward(results: Dict[int, List[mx.Week]], members: Sequence[int], last_year: int) -> Tuple[List[mx.Week], Dict[int, int]]:
    """
    Склеенная серия семейства: для каждого года с FIRST_SELECTION_YEAR вариант выбирается
    по неделям строго до 1 января этого года. Возвращает (недели, {год: индекс варианта}).
    """
    series: List[mx.Week] = []
    chosen: Dict[int, int] = {}
    for year in range(FIRST_SELECTION_YEAR, last_year + 1):
        y0, y1 = year_start_ms(year), year_start_ms(year + 1)
        best, best_score = None, None
        for i in members:
            sc = score([w for w in results[i] if w.entry_t < y0])
            if sc is not None and (best_score is None or sc > best_score):
                best, best_score = i, sc
        if best is None:
            continue
        chosen[year] = best
        series += [w for w in results[best] if y0 <= w.entry_t < y1]
    return series, chosen


def evaluate(weeks: Sequence[mx.Week], alpha: float = ALPHA, bootstrap: int = 2000) -> Dict:
    """Критерии И15 на склеенной серии; отрезки — три равные части периода."""
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
    sd = math.sqrt(sum((r - mean) ** 2 for r in rs) / (n - 1)) if n > 1 else 0.0
    checks = {"weeks": n >= MIN_WEEKS, "ci": low > 0, "mean": mean >= MIN_MEAN,
              "segments": sum(1 for m in segments if m is not None and m > 0) >= 2, "drawdown": drawdown <= MAX_DRAWDOWN}
    return {"weeks": n, "mean": mean, "sd": sd, "ci": (low, high), "win_rate": sum(r > 0 for r in rs) / n,
            "segments": segments, "drawdown": drawdown, "checks": checks, "passed": all(checks.values()),
            "costs": sum(w.costs for w in weeks) / n, "funding": sum(w.funding for w in weeks) / n,
            "gross": sum(w.gross for w in weeks) / n}


def holdout_check(visible_mean: float, holdout: Sequence[mx.Week]) -> Dict:
    """Проверка на противоречие: средняя отложенного конца ≥ видимая − 2 SE, просадка ≤ MAX_DRAWDOWN."""
    n = len(holdout)
    if n < 2:
        return {"weeks": n, "passed": False}
    rs = [w.r for w in holdout]
    mean = sum(rs) / n
    se = math.sqrt(sum((r - mean) ** 2 for r in rs) / (n - 1) / n)
    dd = report.max_drawdown(rs)
    return {"weeks": n, "mean": mean, "se": se, "threshold": visible_mean - 2 * se, "drawdown": dd,
            "passed": mean >= visible_mean - 2 * se and dd <= MAX_DRAWDOWN}


def combine(series: Dict[str, List[mx.Week]], stats: Dict[str, Dict]) -> Tuple[List[Tuple[int, float]], Dict[str, float]]:
    """Прошедшие семейства с равным риском: веса ∝ 1 / разброс; результат по неделям (entry_t, r)."""
    inv = {f: 1 / stats[f]["sd"] for f in series if stats[f].get("sd")}
    total = sum(inv.values())
    if not total:
        return [], {}
    w = {f: v / total for f, v in inv.items()}
    by_t: Dict[int, float] = {}
    for f, weeks in series.items():
        for wk in weeks:
            by_t[wk.entry_t] = by_t.get(wk.entry_t, 0.0) + w[f] * wk.r
    return sorted(by_t.items()), w


# ---------------------------------------------------------------------------
# Данные и отчёт
# ---------------------------------------------------------------------------

def load(conn, start_ms: int, end_ms: int, symbols: Optional[Sequence[str]] = None) -> Dict[str, Series]:
    """Дневные ряды из 4h-свечей кэша history_wide (только крипта); фандинг целиком."""
    if symbols is None:
        symbols = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM candles WHERE interval = '4h' ORDER BY symbol")]
    data: Dict[str, Series] = {}
    for sym in symbols:
        if not is_crypto(sym):
            continue
        s = Series()
        for ts, o, c, turn in conn.execute("SELECT ts, open, close, turnover FROM candles WHERE symbol = ? AND interval = '4h'"
                                           " AND ts >= ? AND ts < ? ORDER BY ts", (sym, start_ms - 400 * DAY_MS, end_ms + DAY_MS)):
            ts = int(ts)
            day = ts // DAY_MS
            if s.first_day is None:
                s.first_day = day
            s.turnover[day] = s.turnover.get(day, 0.0) + float(turn or 0.0)
            if ts % DAY_MS == 5 * mx.H4_MS:
                s.close[day] = float(c)
            if ts % DAY_MS == 0:
                s.open[ts] = float(o)
        rows = [(int(ts), float(r)) for ts, r in conn.execute("SELECT ts, rate FROM funding WHERE symbol = ? ORDER BY ts", (sym,))]
        s.fund_ts, s.fund_rate = [r[0] for r in rows], [r[1] for r in rows]
        if s.close:
            data[sym] = s
    return data


def fmt_stats(stats: Dict, alpha: float = ALPHA) -> str:
    if not stats["weeks"]:
        return "недель нет"
    low, high = stats["ci"]
    seg = " / ".join("—" if m is None else f"{100 * m:+.2f}" for m in stats["segments"])
    failed = [k for k, ok in stats["checks"].items() if not ok]
    return (f"{stats['weeks']} нед., средняя {100 * stats['mean']:+.2f} % [{100 * low:+.2f}; {100 * high:+.2f}] "
            f"({100 * (1 - alpha):.1f} %), плюсовых {100 * stats['win_rate']:.0f} %, отрезки {seg} %, "
            f"просадка {100 * stats['drawdown']:.1f} %; до издержек {100 * stats['gross']:+.2f} %, издержки "
            f"{100 * stats['costs']:.2f} %, фандинг {100 * stats['funding']:+.3f} % → "
            f"{'ПРОХОДИТ' if stats['passed'] else 'не проходит: ' + ', '.join(failed)}")


def run(data: Dict[str, Series], weeks_all: Sequence[int], holdout_start: int, show_holdout: bool) -> Dict:
    cfgs = grid()
    results = {i: simulate(data, cfg, weeks_all) for i, cfg in enumerate(cfgs)}
    visible = {i: [w for w in ws if w.entry_t < holdout_start] for i, ws in results.items()}
    out: Dict = {"configs": [], "families": {}, "holdout_opened": show_holdout}
    for i, cfg in enumerate(cfgs):
        st = evaluate(visible[i], alpha=0.05)  # описание: без поправки, ничего не принимается
        out["configs"].append({"label": label(cfg), "weeks": st["weeks"], "mean": st.get("mean"), "ci": st.get("ci"),
                               "drawdown": st.get("drawdown"), "gross": st.get("gross"), "costs": st.get("costs")})
    last_year = datetime.fromtimestamp(holdout_start / 1000, UTC).year
    series_v: Dict[str, List[mx.Week]] = {}
    stats_v: Dict[str, Dict] = {}
    for fam in FAMILIES:
        members = [i for i, cfg in enumerate(cfgs) if cfg["family"] == fam]
        series, chosen = walk_forward(visible, members, last_year)
        st = evaluate(series)
        series_v[fam], stats_v[fam] = series, st
        entry = {"chosen": {y: label(cfgs[i]) for y, i in chosen.items()}, "stats": st}
        if st["passed"]:
            stress = walk_forward({i: [w for w in simulate(data, cfgs[i], weeks_all, SLIPPAGE_STRESS) if w.entry_t < holdout_start]
                                   for i in members}, members, last_year)[0]
            entry["stress_mean"] = sum(w.r for w in stress) / len(stress) if stress else None
            if show_holdout:
                hold_all, _ = walk_forward(results, members, datetime.now(UTC).year)
                hold = [w for w in hold_all if w.entry_t >= holdout_start]
                entry["holdout"] = holdout_check(st["mean"], hold)
        out["families"][fam] = entry
    passed = {f: s for f, s in series_v.items() if stats_v[f]["passed"]}
    if passed:
        combo, w = combine(passed, stats_v)
        rs = [r for _, r in combo]
        out["combination"] = {"weights": w, "weeks": len(rs), "mean": sum(rs) / len(rs), "drawdown": report.max_drawdown(rs)}
    return out


def render(out: Dict) -> str:
    lines = ["И15 — все 70 вариантов на видимом периоде (описание, 95 %; ничего не принимается):"]
    for c in sorted(out["configs"], key=lambda c: -(c["mean"] or -1)):
        if not c["weeks"]:
            lines.append(f"  {c['label']}: недель нет")
            continue
        lines.append(f"  {c['label']:34} {c['weeks']:3} нед., {100 * c['mean']:+.2f} % [{100 * c['ci'][0]:+.2f}; {100 * c['ci'][1]:+.2f}],"
                     f" просадка {100 * c['drawdown']:.1f} %, издержки {100 * c['costs']:.2f} %")
    lines.append("")
    lines.append(f"Проверка вперёд по годам, по семействам (интервал {100 * (1 - ALPHA):.1f} %):")
    for fam, e in out["families"].items():
        lines.append(f"  {fam}: {fmt_stats(e['stats'])}")
        lines.append("    выбор по годам: " + ", ".join(f"{y}: {lab}" for y, lab in e["chosen"].items()))
        if "stress_mean" in e:
            lines.append(f"    при проскальзывании {100 * SLIPPAGE_STRESS:.2f} %: средняя {100 * (e['stress_mean'] or 0):+.2f} %")
        if "holdout" in e:
            h = e["holdout"]
            lines.append(f"    отложенный конец: {h['weeks']} нед., средняя {100 * h.get('mean', 0):+.2f} % (порог {100 * h.get('threshold', 0):+.2f} %), "
                         f"просадка {100 * h.get('drawdown', 0):.1f} % → {'не противоречит' if h['passed'] else 'ПРОТИВОРЕЧИЕ'}")
    if "combination" in out:
        c = out["combination"]
        lines.append(f"Комбинация прошедших (равный риск): веса {c['weights']}, {c['weeks']} нед., средняя {100 * c['mean']:+.2f} %, "
                     f"просадка {100 * c['drawdown']:.1f} %")
    else:
        lines.append("Комбинация: ни одно семейство не прошло.")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="И15: широкий поиск")
    parser.add_argument("--db", default=str(history.DEFAULT_DB.with_name("history_wide.db")))
    parser.add_argument("--holdout", action="store_true", help="открыть отложенный конец (один раз, только для прошедших)")
    parser.add_argument("--out", help="куда записать JSON отчёта")
    parser.add_argument("--end", help="последний выход ГГГГ-ММ-ДД (по умолчанию — последний понедельник в кэше)")
    args = parser.parse_args(argv)
    conn = history.connect(args.db)
    start_ms = mx.day_ms(START)
    if args.end:
        end_ms = mx.day_ms(args.end)
    else:
        last = conn.execute("SELECT MAX(ts) FROM candles WHERE interval = '4h' AND symbol = 'BTCUSDT'").fetchone()[0]
        end_ms = int(last) + mx.H4_MS
    weeks_all = mx.mondays(start_ms, end_ms)
    holdout_start = weeks_all[-1] - HOLDOUT_WEEKS * WEEK_MS
    data = load(conn, start_ms, end_ms)
    conn.close()
    print(f"И15: контрактов {len(data)}, недели {datetime.fromtimestamp(weeks_all[0] / 1000, UTC):%d.%m.%Y}–"
          f"{datetime.fromtimestamp(weeks_all[-1] / 1000, UTC):%d.%m.%Y}, отложенный конец с "
          f"{datetime.fromtimestamp(holdout_start / 1000, UTC):%d.%m.%Y} ({'открыт' if args.holdout else 'закрыт'})")
    out = run(data, weeks_all, holdout_start, args.holdout)
    print(render(out))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
