"""
Чистая логика И18 «альты истекают к BTC»: лонг BTC 50 % / шорт равновзвешенных 30 самых ликвидных
альтов 50 % (правило docs/TRADER_PLAN.md И18). Вселенная — как у ноги И17а: кандидаты с биржи,
из них top30 по среднему дневному обороту за 30 дней среди старше 100 дней. Ордера, окно
ребалансировки, стоп — из portfolio.engine.
"""
from typing import Dict, Mapping, Sequence

from portfolio import engine as pe

BTC = "BTCUSDT"
ALTS = 30
MIN_ALTS = 10
BTC_WEIGHT = 0.5
LEVERAGE = 5
MAX_DRAWDOWN = 0.25                 # стоп по правилу — доля капитала; критерий проверки строже (15 %)


def weights(data: Mapping[str, dict], candidates: Sequence[str]) -> Dict[str, float]:
    """{BTC: +0,5; каждый альт: −0,5/n}; меньше MIN_ALTS альтов или нет BTC — позиций нет."""
    alts = pe.continuation_universe(data, [s for s in candidates if s != BTC])[:ALTS]
    if len(alts) < MIN_ALTS or BTC not in data:
        return {}
    return {BTC: BTC_WEIGHT, **{s: -(1 - BTC_WEIGHT) / len(alts) for s in alts}}


def drawdown_halt(peak_equity: float, equity: float, capital: float) -> bool:
    return peak_equity - equity > MAX_DRAWDOWN * capital
