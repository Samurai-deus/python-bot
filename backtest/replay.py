"""
Прогон сетапов по истории (Ф1 плана трейдера, шаг 3, фазы 0 и A).

Фаза 0 — контекст рынка по времени: режим (MarketRegimeBrain по 15m/30m/4h) и
корреляции (15m). По закрытым свечам они меняются только на закрытии 15m, поэтому
считаются раз в 15 минут — это совпадает с живым циклом.

Фаза A — сетапы по символу: на каждом закрытии 5m та же функция, что у живого бота
(strategies.setup.evaluate_setup), по окнам из 120 закрытых свечей каждого таймфрейма
и последнему контексту на этот момент; плюс вердикт микроструктуры (фандинг и
открытый интерес — по истории). От портфеля не зависит, поэтому считается параллельно
по символам. Портфель, новизна сигнала, размер и исполнение — фаза B.

Без подглядывания вперёд: в окне момента t — только свечи, чей бар закончился к t.
"""
import bisect
from dataclasses import asdict, dataclass
from types import SimpleNamespace
from typing import Dict, Iterable, List, Optional, Tuple

from backtest import history

BARS = 120
FIVE_MS = history.INTERVALS["5m"][1]
FIFTEEN_MS = history.INTERVALS["15m"][1]
TIMEFRAMES = ("5m", "15m", "30m", "1h", "4h")
CONTEXT_TIMEFRAMES = ("15m", "30m", "4h")


class Series:
    """Свечи одного символа и таймфрейма с быстрым окном «закрытые к моменту t»."""

    def __init__(self, rows: List[list], step_ms: int):
        self.rows = rows
        self.step = step_ms
        self.starts = [int(r[0]) for r in rows]

    def window(self, t_ms: int, bars: int = BARS) -> List[list]:
        end = bisect.bisect_right(self.starts, t_ms - self.step)  # бар закончился к t
        return self.rows[max(0, end - bars):end]


def load_series(conn, symbols: Iterable[str], timeframes: Iterable[str] = TIMEFRAMES) -> Dict[Tuple[str, str], Series]:
    out = {}
    for symbol in symbols:
        for tf in timeframes:
            rows = history.load_candles(conn, symbol, tf, 0, 10 ** 15)
            out[(symbol, tf)] = Series(rows, history.INTERVALS[tf][1])
    return out


def candles_at(series: Dict[Tuple[str, str], Series], symbols: Iterable[str], t_ms: int,
               timeframes: Iterable[str] = TIMEFRAMES) -> Dict[str, Dict[str, List[list]]]:
    return {s: {tf: series[(s, tf)].window(t_ms) for tf in timeframes if (s, tf) in series} for s in symbols}


# ---------------------------------------------------------------------------
# Фаза 0 — контекст
# ---------------------------------------------------------------------------

@dataclass
class Context:
    t_ms: int
    trend_type: Optional[str]
    correlations: Dict


def build_context(series, symbols: List[str], start_ms: int, end_ms: int) -> List[Context]:
    """Контекст на каждом закрытии 15m в [start_ms, end_ms)."""
    from brains.market_regime_brain import MarketRegimeBrain
    from correlation_analysis import analyze_market_correlations
    brain = MarketRegimeBrain()
    out = []
    t = start_ms - start_ms % FIFTEEN_MS
    while t < end_ms:
        market = candles_at(series, symbols, t, CONTEXT_TIMEFRAMES)
        regime = brain.analyze(symbols, market, None)
        out.append(Context(t, getattr(regime, "trend_type", None), analyze_market_correlations(symbols, market, "15m")))
        t += FIFTEEN_MS
    return out


def context_at(contexts: List[Context], t_ms: int, keys: Optional[List[int]] = None) -> Optional[Context]:
    """
    Последний контекст не позже t (контекст — по уже закрытым 15m). keys — готовый список
    t_ms контекстов: за год их ~35 тыс., и строить его на каждом шаге — пятая часть прогона.
    """
    i = bisect.bisect_right(keys if keys is not None else [c.t_ms for c in contexts], t_ms)
    return contexts[i - 1] if i else None


# ---------------------------------------------------------------------------
# Фаза A — сетапы по символу
# ---------------------------------------------------------------------------

@dataclass
class SetupEvent:
    t_ms: int
    symbol: str
    side: str
    entry: float
    stop: float
    target: float
    strategy: str
    risk: str
    score: float
    mode: str
    state_15m: Optional[str]
    atr_15m: float
    block_long: bool = False
    block_short: bool = False


def _state_text(value):
    return None if value is None else str(getattr(value, "value", value))


def _micro(conn, symbol: str, t_ms: int, candles_15m) -> Dict:
    """Вердикт микроструктуры на момент t по истории — как живой (10 последних OI 5min, 5 ставок)."""
    from market_data.microstructure_analyzer import analyze_microstructure
    oi = [{"openInterest": str(v), "timestamp": str(ts)} for ts, v in conn.execute(
        "SELECT ts, value FROM open_interest WHERE symbol = ? AND interval = '5min' AND ts <= ? "
        "ORDER BY ts DESC LIMIT 50", (symbol, t_ms))]
    funding = [{"fundingRate": str(r), "fundingRateTimestamp": str(ts)} for ts, r in conn.execute(
        "SELECT ts, rate FROM funding WHERE symbol = ? AND ts <= ? ORDER BY ts DESC LIMIT 5", (symbol, t_ms))]
    return analyze_microstructure(symbol, candles_15m, oi, funding)


def replay_symbol(conn, series, symbol: str, start_ms: int, end_ms: int, contexts: Optional[List[Context]] = None,
                  with_micro: bool = True, every_ms: int = FIVE_MS) -> Tuple[List[SetupEvent], Dict[str, int]]:
    """
    Сетапы символа на каждом закрытии 5m (или раз в every_ms) в [start_ms, end_ms).
    contexts=None — без режима рынка и корреляций (как у сверки с живым генератором без
    SystemState). Возвращает события-сетапы и счётчик отсевов по коду.
    """
    from strategies.setup import Setup, evaluate_setup
    events, skips = [], {}
    keys = [c.t_ms for c in contexts] if contexts else None
    t = start_ms - start_ms % FIVE_MS
    while t < end_ms:
        market = candles_at(series, [symbol], t)[symbol]
        ctx = context_at(contexts, t, keys) if contexts else None
        regime = SimpleNamespace(trend_type=ctx.trend_type) if ctx and ctx.trend_type else None
        result = evaluate_setup(symbol, market, market_correlations=ctx.correlations if ctx else {},
                                good_time=True, market_regime=regime)
        if isinstance(result, Setup):
            micro = _micro(conn, symbol, t, market.get("15m", [])) if with_micro else {}
            events.append(SetupEvent(t, symbol, result.side, result.entry, result.stop, result.target,
                                     result.strategy_name, result.risk, result.score, result.mode,
                                     _state_text(result.states.get("15m")), result.atr_15m,
                                     bool(micro.get("block_long")), bool(micro.get("block_short"))))
        else:
            skips[result.code] = skips.get(result.code, 0) + 1
        t += every_ms
    return events, skips


def events_as_dicts(events: List[SetupEvent]) -> List[Dict]:
    return [asdict(e) for e in events]
