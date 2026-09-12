"""
И2 плана трейдера (docs/TRADER_PLAN.md, «Исследование преимущества»): отскок против каскада
ликвидаций. Правило, варианты и критерии записаны в плане ДО прогона — здесь только исполнение.

    py -m backtest.cascade_events --months 12

Истории ликвидаций у Bybit в открытом доступе нет — каскад по следу: открытый интерес за
15 минут упал на 2 % и больше, цена за те же 15 минут сдвинулась на 2 % и больше. Момент
решения T — закрытие 5m-бара; цена — close баров, закрывшихся в T и T − 15 мин; OI — снимки
на T − 5 мин и T − 20 мин (на шаг раньше решения). Сделка против движения по open бара,
открывающегося в T; выход по времени через 1, 4 или 24 ч; одна позиция на символ.
Проскальзывание 0,1 % на сторону (вход в каскад), taker, фандинг за удержание.
"""
import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Dict, List, Sequence, Tuple

from backtest import funding_events as fe
from backtest import history

FIVE_MS = fe.FIVE_MS
HOUR_MS = fe.HOUR_MS
WINDOW_MS = 3 * FIVE_MS
OI_DROP = -0.02
PRICE_MOVE = 0.02
HORIZONS_H = (1, 4, 24)
SLIPPAGE = 0.001
ALPHA = 0.05 / len(HORIZONS_H)     # интервал 98,3 %


@dataclass
class Cascade:
    symbol: str
    t_ms: int
    side: str           # сторона сделки — против движения
    oi_change: float
    move: float
    entry_t: int
    exit_t: int
    r: float            # итог на номинал после издержек (доля)
    fees: float
    funding: float


def detect(oi: Dict[int, float], closes: Dict[int, float], start_ms: int, end_ms: int) -> List[Tuple[int, str, float, float]]:
    """Моменты T (закрытия 5m) в [start_ms, end_ms) с каскадом: (T, сторона сделки, изменение OI, движение цены)."""
    out = []
    t = start_ms - start_ms % FIVE_MS
    while t < end_ms:
        oi_now, oi_then = oi.get(t - FIVE_MS), oi.get(t - FIVE_MS - WINDOW_MS)
        c_now, c_then = closes.get(t - FIVE_MS), closes.get(t - FIVE_MS - WINDOW_MS)
        if oi_now and oi_then and c_now and c_then:
            doi, move = oi_now / oi_then - 1, c_now / c_then - 1
            if doi <= OI_DROP and abs(move) >= PRICE_MOVE:
                out.append((t, "LONG" if move < 0 else "SHORT", doi, move))
        t += FIVE_MS
    return out


def run_variant(symbols: Sequence[str], data: Dict, horizon_h: int) -> Tuple[List[Cascade], Dict[str, int]]:
    events: List[Cascade] = []
    skipped = {"overlap": 0, "no_bar": 0}
    for symbol in symbols:
        d = data.get(symbol)
        if not d:
            continue
        busy_until = None
        for t, side, doi, move in d["moments"]:
            entry_t, exit_t = t, t + horizon_h * HOUR_MS
            if busy_until is not None and entry_t < busy_until:
                skipped["overlap"] += 1
                continue
            result = fe.leg(d["opens"], d["fund"], entry_t, exit_t, side, slippage=SLIPPAGE)
            if result is None:
                skipped["no_bar"] += 1
                continue
            busy_until = exit_t
            events.append(Cascade(symbol, t, side, doi, move, entry_t, exit_t, *result))
    events.sort(key=lambda e: (e.exit_t, e.symbol))
    return events, skipped


def load(conn, symbols: Sequence[str], start_ms: int, end_ms: int) -> Dict:
    data = {}
    tail = (max(HORIZONS_H) + 1) * HOUR_MS
    for s in symbols:
        oi = {int(ts): float(v) for ts, v in conn.execute(
            "SELECT ts, value FROM open_interest WHERE symbol = ? AND interval = '5min'", (s,))}
        bars = history.load_candles(conn, s, "5m", start_ms - HOUR_MS, end_ms + tail)
        rows = [(int(ts), float(r)) for ts, r in conn.execute("SELECT ts, rate FROM funding WHERE symbol = ? ORDER BY ts", (s,))]
        data[s] = {"opens": {int(b[0]): float(b[1]) for b in bars},
                   "fund": {"ts": [r[0] for r in rows], "rate": [r[1] for r in rows]},
                   "moments": detect(oi, {int(b[0]): float(b[4]) for b in bars}, start_ms, end_ms)}
    return data


def render(horizon_h: int, stats: Dict, skipped: Dict[str, int], events: Sequence[Cascade]) -> str:
    title = f"{horizon_h:>2} ч"
    if not stats["events"]:
        return f"{title}: событий нет"
    low, high = stats["ci"]
    seg = " / ".join("—" if m is None else f"{100 * m:+.2f}" for m in stats["segments"])
    failed = [k for k, ok in stats["checks"].items() if not ok]
    sides = []
    for side in ("LONG", "SHORT"):
        rs = [e.r for e in events if e.side == side]
        if rs:
            sides.append(f"{side} {len(rs)} {100 * sum(rs) / len(rs):+.2f} %")
    return (f"{title}: {stats['events']} соб., среднее {100 * stats['mean']:+.2f} % "
            f"[{100 * low:+.2f}; {100 * high:+.2f}] (98,3 %), плюсовых {100 * stats['win_rate']:.0f} %, "
            f"отрезки {seg} %, просадка {100 * stats['drawdown']:.1f} % капитала, пропущено {skipped}, "
            f"для справки: {', '.join(sides)} → {'ПРОХОДИТ' if stats['passed'] else 'не проходит: ' + ', '.join(failed)}")


def main(argv=None) -> int:
    import config
    parser = argparse.ArgumentParser(description="И2: отскок против каскада ликвидаций")
    parser.add_argument("--db", default=str(history.DEFAULT_DB))
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--symbols", default=",".join(config.SYMBOLS))
    parser.add_argument("--holdout", action="store_true", help="показать отложенный конец (смотреть один раз)")
    args = parser.parse_args(argv)

    conn = history.connect(args.db)
    start_ms, end_ms = fe.period(conn, args.months)
    symbols = args.symbols.split(",")
    data = load(conn, symbols, start_ms, end_ms)
    conn.close()

    print(f"И2, период {datetime.fromtimestamp(start_ms / 1000, UTC):%d.%m.%Y}–"
          f"{datetime.fromtimestamp(end_ms / 1000, UTC):%d.%m.%Y} "
          f"({'с отложенным концом' if args.holdout else 'без отложенного конца'}), "
          f"OI за 15 мин ≤ {100 * OI_DROP:.0f} % и |цена| ≥ {100 * PRICE_MOVE:.0f} %")
    passed = False
    for horizon in HORIZONS_H:
        events, skipped = run_variant(symbols, data, horizon)
        stats = fe.evaluate(events, start_ms, end_ms, holdout=args.holdout, alpha=ALPHA)
        visible = events if args.holdout else [e for e in events if e.exit_t < fe.report.splits(start_ms, end_ms)[1][0]]
        print(render(horizon, stats, skipped, visible))
        passed = passed or stats["passed"]
    print("Вердикт И2:", "принимается" if passed else "не принимается")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
