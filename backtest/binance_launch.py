"""
И9б плана трейдера (docs/TRADER_PLAN.md, пункт 3): анонс Binance о запуске бессрочного
контракта на монету → LONG бессрочного контракта этой монеты на Bybit. Правило, варианты и
критерии записаны в плане ДО прогона — здесь только исполнение.

    py -m backtest.binance_launch --catalog binance-catalogs.json

Каталог — выгрузка объявлений Binance (каталог 48, «New Cryptocurrency Listing»): список
{releaseDate, title}. Событие — анонс «Binance Futures Will Launch/List … Margined … Perpetual»
с тикерами в заголовке; монета должна была торговаться на Bybit за 10 минут до анонса (по свече
1m — Bybit отдаёт свечи и снятых контрактов). Одна монета — одно событие в сутки. Вход — open
первой минуты, начинающейся не раньше чем через 2 минуты после анонса; выход — close минуты,
закрывающейся через H; если свечи кончились раньше (контракт сняли) — последний close.
"""
import argparse
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Dict, List, Optional, Sequence, Tuple

from backtest import history, report

MIN_MS = 60_000
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
PRE_MIN = 10                      # контракт торговался за 10 минут до анонса
DELAY_MS = 2 * MIN_MS             # вход не раньше чем через 2 минуты после анонса
HORIZONS_H = (1, 4, 24)
FEE = 0.00055                     # taker на сторону
SLIPPAGE = 0.0015                 # на сторону: минута новости, рынок тонкий
COST = 2 * (FEE + SLIPPAGE)       # 0,41 % на сделку
ALPHA = 0.05 / len(HORIZONS_H)    # интервал 98,3 %
MIN_EVENTS = 120
MIN_MEAN = 0.005                  # +0,5 % номинала
NOTIONAL_SHARE = 0.10
MAX_DRAWDOWN = 0.15
END = datetime(2026, 9, 11, tzinfo=UTC)
HOLDOUT_START = datetime(2025, 9, 11, tzinfo=UTC)

_LAUNCH = re.compile(r"^Binance Futures Will (Launch|List)\b.*\bPerpetual\b")
_BETWEEN = re.compile(r"Margined\s+(.*?)\s+Perpetual")
_TOKEN = re.compile(r"[A-Z0-9]{2,20}")


@dataclass
class Trade:
    symbol: str
    t_ms: int           # момент анонса
    entry_t: int
    exit_t: int
    entry: float
    exit: float
    r: float            # итог на номинал после издержек (доля)


def bases(title: str) -> List[str]:
    """Монеты из заголовка анонса: всё между «…Margined» и «Perpetual» (XYZUSDT, XYZ, перечисления)."""
    if not _LAUNCH.match(title):
        return []
    m = _BETWEEN.search(title)
    if not m:
        return []
    out = []
    for part in re.split(r",|\band\b|&", m.group(1)):
        words = part.split()
        tok = re.sub(r"(USDT|USDC|USD)$", "", words[-1]) if words else ""
        if _TOKEN.fullmatch(tok):
            out.append(tok)
    return list(dict.fromkeys(out))


def entry_minute(t_ms: int) -> int:
    """Первая минута, начинающаяся не раньше чем через DELAY_MS после анонса."""
    return -(-(t_ms + DELAY_MS) // MIN_MS) * MIN_MS


def candles(api, symbol: str, start_ms: int, end_ms: int) -> List[Tuple[int, float, float]]:
    """1m свечи [start_ms, end_ms] по возрастанию: (ts, open, close)."""
    out, cursor = {}, start_ms
    while cursor <= end_ms:
        res = api.get("/v5/market/kline", {"category": "linear", "symbol": symbol, "interval": "1",
                                           "start": cursor, "end": min(end_ms, cursor + (history.KLINE_LIMIT - 1) * MIN_MS),
                                           "limit": history.KLINE_LIMIT})
        rows = res.get("list") or []
        for r in rows:
            out[int(r[0])] = (int(r[0]), float(r[1]), float(r[4]))
        cursor += history.KLINE_LIMIT * MIN_MS
    return [out[k] for k in sorted(out)]


def bybit_symbol(api, base: str, t_ms: int) -> Optional[str]:
    """Контракт Bybit, торговавшийся за PRE_MIN минут до анонса, или None."""
    pre = (t_ms // MIN_MS - PRE_MIN) * MIN_MS
    for sym in dict.fromkeys((f"{base}USDT", f"1000{base}USDT", f"{base.removeprefix('1000')}USDT")):
        if candles(api, sym, pre, pre):
            return sym
    return None


def events(api, catalog: Sequence[Dict], end_ms: int) -> List[Tuple[str, int]]:
    """(символ Bybit, момент анонса) по возрастанию времени; одна монета — одно событие в сутки."""
    out, last = [], {}
    for a in sorted(catalog, key=lambda a: int(a["releaseDate"])):
        t = int(a["releaseDate"])
        if t >= end_ms:
            continue
        for base in bases(a["title"]):
            sym = bybit_symbol(api, base, t)
            if sym is None or t - last.get(sym, -DAY_MS) < DAY_MS:
                continue
            last[sym] = t
            out.append((sym, t))
    return out


def trade(symbol: str, t_ms: int, bars: Sequence[Tuple[int, float, float]], horizon_h: int) -> Optional[Trade]:
    """Сделка по свечам окна; None — нет свечи входа."""
    entry_t = entry_minute(t_ms)
    exit_t = entry_t + horizon_h * HOUR_MS
    by_ts = {b[0]: b for b in bars}
    if entry_t not in by_ts:
        return None
    entry = by_ts[entry_t][1]
    held = [b for b in bars if entry_t <= b[0] < exit_t]
    last = held[-1]
    exit_price = last[2]
    r = exit_price / entry - 1 - COST
    return Trade(symbol, t_ms, entry_t, min(exit_t, last[0] + MIN_MS), entry, exit_price, r)


def evaluate(trades: Sequence[Trade], start_ms: int, end_ms: int, holdout_start: int,
             holdout: bool = False, bootstrap: int = 2000) -> Dict:
    """Критерии И9б из плана. Видимое — сделки, закрытые до отложенного конца."""
    holdout_days = (end_ms - holdout_start) // DAY_MS
    parts, _ = report.splits(start_ms, end_ms, holdout_days=holdout_days)
    visible = list(trades) if holdout else [t for t in trades if t.exit_t < holdout_start]
    n = len(visible)
    if not n:
        return {"events": 0, "passed": False, "checks": {}}
    mean = sum(t.r for t in visible) / n
    low, high = report.expectancy_ci(visible, bootstrap, alpha=ALPHA)
    segments = []
    for part in parts:
        chunk = report.within(visible, part)
        segments.append(sum(t.r for t in chunk) / len(chunk) if chunk else None)
    drawdown = report.max_drawdown(NOTIONAL_SHARE * t.r for t in visible)
    checks = {
        "events": n >= MIN_EVENTS,
        "ci": low > 0,
        "mean": mean >= MIN_MEAN,
        "segments": sum(1 for m in segments if m is not None and m > 0) >= 2,
        "drawdown": drawdown <= MAX_DRAWDOWN,
    }
    return {"events": n, "mean": mean, "ci": (low, high), "win_rate": sum(t.r > 0 for t in visible) / n,
            "segments": segments, "drawdown": drawdown, "checks": checks, "passed": all(checks.values())}


def holdout_mean(trades: Sequence[Trade], holdout_start: int) -> Optional[float]:
    """Отложенный конец: противоречие, если средняя после издержек не выше 0."""
    tail = [t for t in trades if t.entry_t >= holdout_start]
    return sum(t.r for t in tail) / len(tail) if tail else None


def render(horizon_h: int, stats: Dict) -> str:
    title = f"{horizon_h:>2} ч"
    if not stats["events"]:
        return f"{title}: событий нет"
    low, high = stats["ci"]
    seg = " / ".join("—" if m is None else f"{100 * m:+.2f}" for m in stats["segments"])
    failed = [k for k, ok in stats["checks"].items() if not ok]
    return (f"{title}: {stats['events']} соб., среднее {100 * stats['mean']:+.2f} % "
            f"[{100 * low:+.2f}; {100 * high:+.2f}] (98,3 %), плюсовых {100 * stats['win_rate']:.0f} %, "
            f"отрезки {seg} %, просадка {100 * stats['drawdown']:.1f} % капитала → "
            f"{'ПРОХОДИТ' if stats['passed'] else 'не проходит: ' + ', '.join(failed)}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="И9б: анонс фьючерсов Binance → LONG контракта на Bybit")
    parser.add_argument("--catalog", required=True, help="JSON: список {releaseDate, title} или {'48': [...]} ")
    parser.add_argument("--holdout", action="store_true", help="вскрыть отложенный конец (один раз, после видимого)")
    args = parser.parse_args(argv)

    raw = json.loads(open(args.catalog, encoding="utf-8").read())
    catalog = raw["48"] if isinstance(raw, dict) else raw
    api = history.BybitHistory()
    end_ms = int(END.timestamp() * 1000)
    hold_ms = int(HOLDOUT_START.timestamp() * 1000)
    evs = events(api, catalog, end_ms)
    if not evs:
        print("событий нет")
        return 0
    start_ms = evs[0][1] // DAY_MS * DAY_MS
    windows = {(s, t): candles(api, s, entry_minute(t), entry_minute(t) + max(HORIZONS_H) * HOUR_MS)
               for s, t in evs}
    print(f"И9б: {len(evs)} событий {datetime.fromtimestamp(start_ms / 1000, UTC):%d.%m.%Y}–{END:%d.%m.%Y}, "
          f"отложенный конец с {HOLDOUT_START:%d.%m.%Y} {'ВСКРЫТ' if args.holdout else 'не смотрится'}, "
          f"издержки {100 * COST:.2f} %")
    passed = False
    for h in HORIZONS_H:
        trades = [x for x in (trade(s, t, windows[(s, t)], h) for s, t in evs) if x is not None]
        stats = evaluate(trades, start_ms, end_ms, hold_ms)
        print(render(h, stats))
        passed = passed or stats["passed"]
        if args.holdout:
            m = holdout_mean(trades, hold_ms)
            n = sum(t.entry_t >= hold_ms for t in trades)
            print(f"    отложенный конец: {n} соб., среднее "
                  + ("—" if m is None else f"{100 * m:+.2f} % → {'противоречие' if m <= 0 else 'без противоречия'}"))
    print("Вердикт видимого периода И9б:", "проходит" if passed else "не проходит")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
