"""
И11 плана трейдера (docs/TRADER_PLAN.md, пункт 3): объявление Binance о делистинге монеты →
SHORT бессрочного контракта этой монеты на Bybit. Правило, варианты и критерии записаны в плане
ДО прогона — здесь только исполнение.

    py -m backtest.binance_delist --catalog binance-catalogs.json

Каталог — выгрузка объявлений Binance (каталог 161, «Delisting»): список {releaseDate, title}
или {"161": [...]}. Событие — «Binance Will Delist A, B, C on …»; монета должна была торговаться
на Bybit за 10 минут до объявления. Вход, выход, издержки и контракт — как в И9б
(backtest.binance_launch); сторона SHORT и фандинг за держание по истории ставок Bybit.
Отложенного конца нет: решение по всей выборке, планка поднята под малое число событий.
"""
import argparse
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import List, Optional, Sequence

from backtest import binance_launch as bl

MIN_EVENTS = 45
MIN_MEAN = 0.015                  # +1,5 % номинала: на ~50 событиях пройти может только крупный эффект
EVAL_TAIL_MS = 2 * bl.DAY_MS      # последние события закрываются после END — период оценки чуть длиннее
STABLE = {"USDT", "USDC", "BUSD", "FDUSD", "TUSD"}
_DELIST = re.compile(r"^Binance Will Delist\s+(.*?)\s+on\s+\S+")


@dataclass
class Trade:
    symbol: str
    t_ms: int
    entry_t: int
    exit_t: int
    entry: float
    exit: float
    funding: float      # сумма ставок за держание в пользу шорта (доля номинала)
    r: float            # итог на номинал после издержек и фандинга (доля)


def bases(title: str) -> List[str]:
    """Монеты из заголовка «Binance Will Delist A, B, C on …» — до даты, без стейблкоинов."""
    m = _DELIST.match(title)
    if not m:
        return []
    return list(dict.fromkeys(t for t in re.findall(r"\b[A-Z0-9]{2,15}\b", m.group(1)) if t not in STABLE))


def funding(api, symbol: str, start_ms: int, end_ms: int) -> float:
    """Сумма ставок фандинга с отметкой в (start_ms, end_ms]: положительная ставка — доход шорта."""
    res = api.get("/v5/market/funding/history", {"category": "linear", "symbol": symbol,
                                                 "startTime": start_ms, "endTime": end_ms, "limit": 200})
    return sum(float(r["fundingRate"]) for r in res.get("list") or []
               if start_ms < int(r["fundingRateTimestamp"]) <= end_ms)


def trade(api, symbol: str, t_ms: int, bars, horizon_h: int) -> Optional[Trade]:
    """SHORT по свечам окна: цены входа и выхода — как в И9б; None — нет свечи входа."""
    lt = bl.trade(symbol, t_ms, bars, horizon_h)
    if lt is None:
        return None
    f = funding(api, symbol, lt.entry_t, lt.exit_t)
    r = 1 - lt.exit / lt.entry - bl.COST + f
    return Trade(symbol, t_ms, lt.entry_t, lt.exit_t, lt.entry, lt.exit, f, r)


def evaluate(trades: Sequence[Trade], start_ms: int, end_ms: int) -> dict:
    """Критерии И11: вся выборка — проверочная (отложенного конца нет)."""
    return bl.evaluate(trades, start_ms, end_ms, end_ms, min_events=MIN_EVENTS, min_mean=MIN_MEAN)


def render(horizon_h: int, stats: dict, trades: Sequence[Trade]) -> str:
    line = bl.render(horizon_h, stats)
    if trades:
        line += f"; фандинг в среднем {100 * sum(t.funding for t in trades) / len(trades):+.3f} %"
    return line


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="И11: делистинг на Binance → SHORT контракта на Bybit")
    parser.add_argument("--catalog", required=True, help="JSON: список {releaseDate, title} или {'161': [...]}")
    args = parser.parse_args(argv)

    raw = json.loads(open(args.catalog, encoding="utf-8").read())
    catalog = raw["161"] if isinstance(raw, dict) else raw
    api = bl.history.BybitHistory()
    end_ms = int(bl.END.timestamp() * 1000)
    evs = bl.events(api, catalog, end_ms, parse=bases)
    if not evs:
        print("событий нет")
        return 0
    start_ms = evs[0][1] // bl.DAY_MS * bl.DAY_MS
    windows = {(s, t): bl.candles(api, s, bl.entry_minute(t), bl.entry_minute(t) + max(bl.HORIZONS_H) * bl.HOUR_MS)
               for s, t in evs}
    print(f"И11: {len(evs)} событий {datetime.fromtimestamp(start_ms / 1000, UTC):%d.%m.%Y}–{bl.END:%d.%m.%Y}, "
          f"SHORT, издержки {100 * bl.COST:.2f} % + фандинг, отложенного конца нет")
    passed = False
    for h in bl.HORIZONS_H:
        trades = [x for x in (trade(api, s, t, windows[(s, t)], h) for s, t in evs) if x is not None]
        stats = evaluate(trades, start_ms, end_ms + EVAL_TAIL_MS)
        print(render(h, stats, trades))
        passed = passed or stats["passed"]
    print("Вердикт И11:", "проходит — дальше подтверждение вперёд" if passed else "не проходит")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
