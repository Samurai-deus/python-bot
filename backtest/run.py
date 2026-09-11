"""
Проверка на истории целиком (Ф1 плана трейдера): контекст → сетапы → портфель → отчёт.

    py -m backtest.run --months 12 --workers 6

Фаза 0 (контекст) и фаза A (сетапы) считаются в процессах — по частям времени и по
символам; фаза B (портфель) — последовательно. Риск на сделку по умолчанию — 1 %, как
на демо-счёте (локальный config по умолчанию даёт 2 % — это не тот режим, что торгует).
Отложенный конец истории (последние 75 суток) в отчёт не попадает без --holdout:
его смотрят один раз, после выбора стратегий.
"""
import argparse
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from backtest import history, portfolio, replay, report

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data"


def _context_chunk(args):
    db, symbols, start, end = args
    conn = history.connect(db)
    series = replay.load_series(conn, symbols, replay.CONTEXT_TIMEFRAMES)
    out = replay.build_context(series, symbols, start, end)
    conn.close()
    return out


def _symbol_setups(args):
    db, symbol, start, end, contexts, with_micro = args
    conn = history.connect(db)
    series = replay.load_series(conn, [symbol])
    events, skips = replay.replay_symbol(conn, series, symbol, start, end, contexts=contexts, with_micro=with_micro)
    conn.close()
    return symbol, events, skips


def run(db, symbols, start_ms, end_ms, workers, equity, risk_pct, with_micro=True, log=print):
    started = time.monotonic()
    chunk = max(replay.FIFTEEN_MS, (end_ms - start_ms) // (workers * 4))
    chunk -= chunk % replay.FIFTEEN_MS
    pieces = [(db, symbols, s, min(s + chunk, end_ms)) for s in range(start_ms, end_ms, chunk)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        contexts = [c for part in pool.map(_context_chunk, pieces) for c in part]
    contexts.sort(key=lambda c: c.t_ms)
    log(f"контекст: {len(contexts)} моментов за {time.monotonic() - started:.0f} с")

    setups, skips = [], {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        jobs = [(db, s, start_ms, end_ms, contexts, with_micro) for s in symbols]
        for symbol, events, symbol_skips in pool.map(_symbol_setups, jobs):
            setups.extend(events)
            for code, n in symbol_skips.items():
                skips[code] = skips.get(code, 0) + n
            log(f"{symbol}: сетапов {len(events)}")
    log(f"сетапы: {len(setups)} за {time.monotonic() - started:.0f} с")

    conn = history.connect(db)
    bars = {s: history.load_candles(conn, s, "5m", start_ms, end_ms + 30 * history.INTERVALS["5m"][1] * 12)
            for s in symbols}
    funding = {s: [(ts, rate) for ts, rate in conn.execute(
        "SELECT ts, rate FROM funding WHERE symbol = ? AND ts >= ? ORDER BY ts", (s, start_ms))] for s in symbols}
    conn.close()
    limits = portfolio.Limits.from_config()
    limits.risk_pct = risk_pct
    result = portfolio.simulate(setups, bars, funding, symbols, equity=equity, limits=limits)
    log(f"портфель: {len(result.trades)} сделок за {time.monotonic() - started:.0f} с")
    return SimpleNamespace(setups=setups, skips=skips, result=result, contexts=len(contexts))


def main(argv=None):
    import config
    parser = argparse.ArgumentParser(description="Проверка стратегий на истории")
    parser.add_argument("--db", default=str(history.DEFAULT_DB))
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--symbols", default=",".join(config.SYMBOLS))
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--equity", type=float, default=1000.0)
    parser.add_argument("--risk-pct", type=float, default=1.0)
    parser.add_argument("--no-micro", action="store_true", help="без фильтра фандинга и открытого интереса")
    parser.add_argument("--holdout", action="store_true", help="показать отложенный конец (смотреть один раз)")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    conn = history.connect(args.db)
    last = conn.execute("SELECT MIN(mx) FROM (SELECT MAX(ts) AS mx FROM candles WHERE interval = '5m' GROUP BY symbol)").fetchone()[0]
    conn.close()
    end_ms = int(last) + history.INTERVALS["5m"][1]
    # первые 20 суток — разгон: окнам 4h нужно 120 закрытых баров
    start_ms = int((datetime.fromtimestamp(end_ms / 1000, UTC) - timedelta(days=30 * args.months - 20)).timestamp() * 1000)
    symbols = args.symbols.split(",")
    out = run(args.db, symbols, start_ms, end_ms, args.workers, args.equity, args.risk_pct, with_micro=not args.no_micro)

    trades = out.result.trades
    if end_ms - start_ms >= 150 * report.DAY_MS:
        parts, holdout = report.splits(start_ms, end_ms)
    else:
        # короткий период (проверка конвейера) — без отложенного конца и отрезков
        parts, holdout = [], (end_ms, end_ms)
    visible = [t for t in trades if t.entry_t < holdout[0]] if not args.holdout else trades
    lines = [f"Период {datetime.fromtimestamp(start_ms / 1000, UTC):%d.%m.%Y}–{datetime.fromtimestamp(end_ms / 1000, UTC):%d.%m.%Y}"
             f" ({'с отложенным концом' if args.holdout else 'без отложенного конца'}), риск {args.risk_pct} %",
             report.render("Всего", report.summary(visible))]
    for name, stats in report.by_key(visible, lambda t: t.strategy).items():
        lines.append(report.render(f"  {name}", stats))
    for i, part in enumerate(parts, 1):
        lines.append(report.render(f"Отрезок {i}", report.summary(report.within(trades, part))))
    if args.holdout:
        lines.append(report.render("Отложенный конец", report.summary(report.within(trades, holdout))))
    lines.append(f"Отказы портфеля: {out.result.refused}")
    lines.append(f"Отсев сетапов: {out.skips}")
    text = "\n".join(lines)
    print(text)
    target = Path(args.out) if args.out else DEFAULT_OUT / f"backtest-{datetime.now(UTC):%Y%m%d-%H%M}.json"
    target.write_text(json.dumps({"report": text, "trades": [asdict(t) for t in visible],
                                  "refused": out.result.refused, "skips": out.skips}, ensure_ascii=False), encoding="utf-8")
    print("сохранено:", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
