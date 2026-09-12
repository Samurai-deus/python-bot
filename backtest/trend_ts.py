"""
И4 плана трейдера (docs/TRADER_PLAN.md, «Исследование преимущества»): трендследование по
времени — каждая монета в сторону своего тренда, размер по волатильности. Правило и критерии
записаны в плане ДО прогона — здесь только исполнение.

    py -m backtest.trend_ts

Раз в неделю (понедельник 00:00 UTC) по каждой из 10 крупных монет: направление — знак
доходности за N дней по закрытым 4h; вес = 0,15 / (годовая волатильность × K), не больше 30 %
капитала, где K — число монет с данными на этот момент; волатильность — по 30 дневным
доходностям (закрытия 4h-баров в 00:00 UTC). Вход и выход — по open 4h-бара ребалансировки;
веса каждую неделю заново, издержки на весь оборот, фандинг ног по историческим ставкам.
Результат недели — доля капитала.
"""
import argparse
import bisect
import math
from datetime import UTC, datetime
from typing import Dict, List, Optional, Sequence

from backtest import funding_events as fe
from backtest import history, report
from backtest import momentum_xs as mx
from backtest.portfolio import SLIPPAGE, TAKER_FEE

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT", "BNBUSDT")
LOOKBACKS_D = (30, 90)
VOL_DAYS = 30
TARGET = 0.15                      # вклад в риск на монету = TARGET / K
CAP = 0.30                         # не больше 30 % капитала на монету
ALPHA = 0.05 / len(LOOKBACKS_D)    # интервал 97,5 %
MIN_WEEKS = 150
MIN_MEAN = 0.0015                  # +0,15 % капитала в неделю
MAX_DRAWDOWN = 0.15
START = "2020-07-06"
HOLDOUT_DAYS = 75
DAY_MS, H4_MS, WEEK_MS = mx.DAY_MS, mx.H4_MS, mx.WEEK_MS


def trend(closes_4h: Dict[int, float], t: int, lookback_d: int) -> Optional[float]:
    """Доходность за lookback_d дней по 4h-барам, закрывшимся к t."""
    end_bar = t - H4_MS
    a, b = closes_4h.get(end_bar - lookback_d * DAY_MS), closes_4h.get(end_bar)
    return None if not a or not b else b / a - 1


def annual_vol(closes_4h: Dict[int, float], t: int) -> Optional[float]:
    """Годовая волатильность по VOL_DAYS дневным лог-доходностям (закрытия 4h-баров в 00:00 UTC до t)."""
    day0 = t - t % DAY_MS
    closes = [closes_4h.get(day0 - k * DAY_MS - H4_MS) for k in range(VOL_DAYS + 1)]
    if any(not c for c in closes):
        return None
    rets = [math.log(closes[k] / closes[k + 1]) for k in range(VOL_DAYS)]
    mean = sum(rets) / VOL_DAYS
    var = sum((r - mean) ** 2 for r in rets) / (VOL_DAYS - 1)
    return math.sqrt(var * 365) if var > 0 else None


def targets(data: Dict, symbols: Sequence[str], t: int, t_next: int, lookback_d: int) -> Dict[str, float]:
    """Веса на неделю [t, t_next): знак тренда × min(CAP, TARGET / (волатильность × K))."""
    ready = {}
    for s in symbols:
        d = data.get(s)
        if not d or not d["o4"].get(t) or not d["o4"].get(t_next):
            continue
        r, vol = trend(d["c4"], t, lookback_d), annual_vol(d["c4"], t)
        if r is None or vol is None or r == 0:
            continue
        ready[s] = (1 if r > 0 else -1, vol)
    k = len(ready)
    return {s: sign * min(CAP, TARGET / (vol * k)) for s, (sign, vol) in ready.items()}


def simulate(data: Dict, symbols: Sequence[str], weeks_t: Sequence[int], lookback_d: int) -> List[mx.Week]:
    out: List[mx.Week] = []
    held: Dict[str, float] = {}  # символ → экспозиция в долях капитала после дрейфа цены (+ лонг, − шорт)
    for t, t_next in zip(weeks_t, weeks_t[1:]):
        target = targets(data, symbols, t, t_next, lookback_d)
        turnover = sum(abs(target.get(s, 0.0) - held.get(s, 0.0)) for s in set(target) | set(held))
        costs = turnover * (TAKER_FEE + SLIPPAGE)
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
                           tuple(s for s, w in target.items() if w > 0), tuple(s for s, w in target.items() if w < 0)))
        held = new_held
    if out and held:
        exit_cost = sum(abs(v) for v in held.values()) * (TAKER_FEE + SLIPPAGE)
        out[-1].r -= exit_cost
        out[-1].costs += exit_cost
    return out


def evaluate(weeks: Sequence[mx.Week], bootstrap: int = 2000, alpha: float = ALPHA) -> Dict:
    """Критерии И4 из плана; отрезки — три равные части периода недель."""
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
        bars = history.load_candles(conn, s, "4h", start_ms - 120 * DAY_MS, end_ms + H4_MS)
        rows = [(int(ts), float(r)) for ts, r in conn.execute("SELECT ts, rate FROM funding WHERE symbol = ? ORDER BY ts", (s,))]
        data[s] = {"c4": {int(b[0]): float(b[4]) for b in bars}, "o4": {int(b[0]): float(b[1]) for b in bars},
                   "fund": {"ts": [r[0] for r in rows], "rate": [r[1] for r in rows]}}
    return data


def render(lookback_d: int, stats: Dict, weeks: Sequence[mx.Week], alpha: float = ALPHA) -> str:
    title = f"N = {lookback_d:>2} дн"
    if not stats["weeks"]:
        return f"{title}: недель нет"
    low, high = stats["ci"]
    seg = " / ".join("—" if m is None else f"{100 * m:+.2f}" for m in stats["segments"])
    failed = [k for k, ok in stats["checks"].items() if not ok]
    coins = [len(w.longs) + len(w.shorts) for w in weeks]
    level = f"{100 * (1 - alpha):.1f}".replace(".", ",")
    return (f"{title}: {stats['weeks']} нед., средняя {100 * stats['mean']:+.3f} % капитала "
            f"[{100 * low:+.3f}; {100 * high:+.3f}] ({level} %), плюсовых недель {100 * stats['win_rate']:.0f} %, "
            f"отрезки {seg} %, просадка {100 * stats['drawdown']:.1f} %; в неделю: до издержек "
            f"{100 * stats['gross']:+.3f} %, издержки {100 * stats['costs']:.3f} %, фандинг {100 * stats['funding']:+.3f} %; "
            f"монет в портфеле {min(coins)}–{max(coins)} → "
            f"{'ПРОХОДИТ' if stats['passed'] else 'не проходит: ' + ', '.join(failed)}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="И4: трендследование по времени")
    parser.add_argument("--db", default=str(history.DEFAULT_DB))
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
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

    print(f"И4, ребалансировки {datetime.fromtimestamp(weeks_t[0] / 1000, UTC):%d.%m.%Y}–"
          f"{datetime.fromtimestamp(weeks_t[-1] / 1000, UTC):%d.%m.%Y} "
          f"({'с отложенным концом' if args.holdout else 'без отложенного конца'}), {len(symbols)} монет, "
          f"вес 0,15 / (волатильность × K), не больше {100 * CAP:.0f} %")
    passed = False
    for lookback in LOOKBACKS_D:
        weeks = simulate(data, symbols, weeks_t, lookback)
        stats = evaluate(weeks)
        print(render(lookback, stats, weeks))
        passed = passed or stats["passed"]
    print("Вердикт И4:", "принимается" if passed else "не принимается")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
