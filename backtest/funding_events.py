"""
И1 плана трейдера (docs/TRADER_PLAN.md, «Исследование преимущества»): отскок после сильно
отрицательного фандинга. Правило, варианты и критерии записаны в плане ДО прогона — здесь
только их исполнение.

    py -m backtest.funding_events --months 12

Событие — выплата со ставкой, приведённой к 8 ч, не выше −0,03 %. LONG по open 5m-бара,
который открывается через 5 минут после выплаты; выход по времени через 8, 24 или 72 ч
(по open бара через 5 минут после выплаты на этом сроке). Одна позиция на символ. Вариант с
защитой — плюс шорт BTC (для BTC — ETH) на тот же номинал. Результат — доля номинала одной
ноги после издержек: taker и проскальзывание на каждую сторону каждой ноги, фандинг за
удержание по историческим ставкам. Отложенный конец (75 суток) — только с --holdout.
"""
import argparse
import bisect
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from backtest import history, report
from backtest.portfolio import SLIPPAGE, TAKER_FEE

HOUR_MS = 3_600_000
FIVE_MS = history.INTERVALS["5m"][1]
THRESHOLD_8H = -0.0003
HORIZONS_H = (8, 24, 72)
HEDGE_FOR = {"BTCUSDT": "ETHUSDT"}
HEDGE_DEFAULT = "BTCUSDT"
VARIANTS = len(HORIZONS_H) * 2
ALPHA = 0.05 / VARIANTS            # интервал 99,2 % — поправка Бонферрони на 6 вариантов
MIN_EVENTS = 150
MIN_MEAN = 0.003                   # +0,3 % номинала — запас на ошибку в издержках
NOTIONAL_SHARE = 0.10              # номинал события — 10 % капитала
MAX_DRAWDOWN = 0.15                # доля капитала


@dataclass
class Event:
    symbol: str
    t_ms: int           # выплата фандинга
    rate_8h: float
    entry_t: int
    exit_t: int
    r: float            # итог на номинал ноги после издержек (доля)
    long_leg: float
    hedge_leg: float
    hedged: bool
    fees: float
    funding: float

    @property
    def pnl(self) -> float:  # для report.summary
        return self.r


def rates_8h(rows: Sequence[Tuple[int, float]]) -> List[Tuple[int, float, float]]:
    """
    (ts, ставка, ставка к 8 ч). Интервал — от предыдущей выплаты (у первой — до следующей);
    дыры длиннее 8 ч считаются 8 ч, интервалы короче часа — часом.
    """
    out = []
    for i, (ts, rate) in enumerate(rows):
        if i:
            gap = (ts - rows[i - 1][0]) / HOUR_MS
        elif len(rows) > 1:
            gap = (rows[1][0] - ts) / HOUR_MS
        else:
            gap = 8.0
        gap = min(max(gap, 1.0), 8.0)
        out.append((ts, rate, rate * 8.0 / gap))
    return out


def leg(bars: Dict[int, float], fund: Dict, entry_t: int, exit_t: int, side: str) -> Optional[Tuple[float, float, float]]:
    """
    Нога от open бара entry_t до open бара exit_t: (итог, комиссии, фандинг) в долях номинала
    на входе. Фандинг — выплаты в (entry_t, exit_t]: лонг платит положительную ставку и получает
    отрицательную.
    """
    o_in, o_out = bars.get(entry_t), bars.get(exit_t)
    if o_in is None or o_out is None:
        return None
    d = 1 if side == "LONG" else -1
    fill_in = o_in * (1 + d * SLIPPAGE)
    fill_out = o_out * (1 - d * SLIPPAGE)
    gross = d * (fill_out / fill_in - 1)
    fees = TAKER_FEE * (1 + fill_out / fill_in)
    funding = 0.0
    stamps = fund["ts"]
    for i in range(bisect.bisect_right(stamps, entry_t), bisect.bisect_right(stamps, exit_t)):
        price = bars.get(stamps[i], o_in)
        funding += d * fund["rate"][i] * price / fill_in
    return gross - fees - funding, fees, funding


def run_variant(symbols: Sequence[str], fund: Dict, bars: Dict, start_ms: int, end_ms: int, horizon_h: int,
                hedged: bool) -> Tuple[List[Event], Dict[str, int]]:
    events: List[Event] = []
    skipped = {"overlap": 0, "no_bar": 0}
    for symbol in symbols:
        f = fund.get(symbol)
        if not f:
            continue
        busy_until = None
        for ts, _, rate8 in f["rows8"]:
            if not start_ms <= ts < end_ms or rate8 > THRESHOLD_8H:
                continue
            entry_t = ts + FIVE_MS
            exit_t = entry_t + horizon_h * HOUR_MS
            if busy_until is not None and entry_t < busy_until:
                skipped["overlap"] += 1
                continue
            main = leg(bars.get(symbol, {}), f, entry_t, exit_t, "LONG")
            hedge = None
            if hedged:
                other = HEDGE_FOR.get(symbol, HEDGE_DEFAULT)
                hedge = leg(bars.get(other, {}), fund.get(other, {"ts": [], "rate": []}), entry_t, exit_t, "SHORT")
            if main is None or (hedged and hedge is None):
                skipped["no_bar"] += 1
                continue
            busy_until = exit_t
            h = hedge or (0.0, 0.0, 0.0)
            events.append(Event(symbol, ts, rate8, entry_t, exit_t, main[0] + h[0], main[0], h[0], hedged,
                                main[1] + h[1], main[2] + h[2]))
    events.sort(key=lambda e: (e.exit_t, e.symbol))
    return events, skipped


def evaluate(events: Sequence[Event], start_ms: int, end_ms: int, holdout: bool = False, bootstrap: int = 2000) -> Dict:
    """Критерии И1 из плана. Видимое — события, закрытые до отложенного конца."""
    parts, (hold_start, _) = report.splits(start_ms, end_ms)
    visible = list(events) if holdout else [e for e in events if e.exit_t < hold_start]
    n = len(visible)
    if not n:
        return {"events": 0, "passed": False, "checks": {}}
    mean = sum(e.r for e in visible) / n
    low, high = report.expectancy_ci(visible, bootstrap, alpha=ALPHA)
    segments = []
    for part in parts:
        chunk = report.within(visible, part)
        segments.append(sum(e.r for e in chunk) / len(chunk) if chunk else None)
    drawdown = report.max_drawdown(NOTIONAL_SHARE * e.r for e in visible)
    checks = {
        "events": n >= MIN_EVENTS,
        "ci": low > 0,
        "mean": mean >= MIN_MEAN,
        "segments": sum(1 for m in segments if m is not None and m > 0) >= 2,
        "drawdown": drawdown <= MAX_DRAWDOWN,
    }
    return {"events": n, "mean": mean, "ci": (low, high), "win_rate": sum(e.r > 0 for e in visible) / n,
            "segments": segments, "drawdown": drawdown, "checks": checks, "passed": all(checks.values())}


def verdict(results: Dict[Tuple[int, bool], Dict]) -> bool:
    """Гипотеза принимается, только если проходит вариант с защитой от рынка."""
    return any(stats["passed"] for (_, hedged), stats in results.items() if hedged)


def load(conn, symbols: Sequence[str], start_ms: int, end_ms: int) -> Tuple[Dict, Dict]:
    fund, bars = {}, {}
    tail = (max(HORIZONS_H) + 1) * HOUR_MS
    for s in sorted(set(symbols) | {HEDGE_DEFAULT, *HEDGE_FOR.values()}):
        rows = [(int(ts), float(rate)) for ts, rate in
                conn.execute("SELECT ts, rate FROM funding WHERE symbol = ? ORDER BY ts", (s,))]
        fund[s] = {"ts": [r[0] for r in rows], "rate": [r[1] for r in rows], "rows8": rates_8h(rows)}
        bars[s] = {int(b[0]): float(b[1]) for b in history.load_candles(conn, s, "5m", start_ms, end_ms + tail)}
    return fund, bars


def render(horizon_h: int, hedged: bool, stats: Dict, skipped: Dict[str, int]) -> str:
    title = f"{horizon_h:>2} ч {'с защитой' if hedged else 'без защиты'}"
    if not stats["events"]:
        return f"{title}: событий нет"
    low, high = stats["ci"]
    seg = " / ".join("—" if m is None else f"{100 * m:+.2f}" for m in stats["segments"])
    failed = [k for k, ok in stats["checks"].items() if not ok]
    return (f"{title}: {stats['events']} соб., среднее {100 * stats['mean']:+.2f} % "
            f"[{100 * low:+.2f}; {100 * high:+.2f}] (99,2 %), плюсовых {100 * stats['win_rate']:.0f} %, "
            f"отрезки {seg} %, просадка {100 * stats['drawdown']:.1f} % капитала, "
            f"пропущено {skipped} → {'ПРОХОДИТ' if stats['passed'] else 'не проходит: ' + ', '.join(failed)}")


def main(argv=None) -> int:
    import config
    parser = argparse.ArgumentParser(description="И1: отскок после сильно отрицательного фандинга")
    parser.add_argument("--db", default=str(history.DEFAULT_DB))
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--symbols", default=",".join(config.SYMBOLS))
    parser.add_argument("--holdout", action="store_true", help="показать отложенный конец (смотреть один раз)")
    args = parser.parse_args(argv)

    conn = history.connect(args.db)
    last = conn.execute("SELECT MIN(mx) FROM (SELECT MAX(ts) AS mx FROM candles WHERE interval = '5m'"
                        " GROUP BY symbol)").fetchone()[0]
    end_ms = int(last) + FIVE_MS
    # тот же период, что у backtest.run: первые 20 суток там — разгон окон
    start_ms = int((datetime.fromtimestamp(end_ms / 1000, UTC) - timedelta(days=30 * args.months - 20)).timestamp() * 1000)
    symbols = args.symbols.split(",")
    fund, bars = load(conn, symbols, start_ms, end_ms)
    conn.close()

    print(f"И1, период {datetime.fromtimestamp(start_ms / 1000, UTC):%d.%m.%Y}–"
          f"{datetime.fromtimestamp(end_ms / 1000, UTC):%d.%m.%Y} "
          f"({'с отложенным концом' if args.holdout else 'без отложенного конца'}), порог {100 * THRESHOLD_8H:.2f} % за 8 ч")
    results = {}
    for hedged in (False, True):
        for horizon in HORIZONS_H:
            events, skipped = run_variant(symbols, fund, bars, start_ms, end_ms, horizon, hedged)
            results[(horizon, hedged)] = stats = evaluate(events, start_ms, end_ms, holdout=args.holdout)
            print(render(horizon, hedged, stats, skipped))
    print("Вердикт И1:", "принимается" if verdict(results) else "не принимается")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
