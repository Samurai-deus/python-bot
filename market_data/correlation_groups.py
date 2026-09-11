"""
Группы коррелирующих символов для Risk Core (решение владельца 11.09.2026).

Лимит «не больше max_correlated_group_pct капитала на группу» в Risk Core был,
но гейткипер передавал ему пустой словарь групп — проверка не работала. Замер
11.09: медианная корреляция 4-часовых доходностей 27 символов за 30 дней — 0,56,
у 17 % пар выше 0,7, ETH к BTC — 0,85. Три SHORT по альтам одновременно — по
сути одна ставка.

Группы считаются из данных раз в сутки: логарифмические доходности 4h-свечей за
30 дней, корреляция Пирсона по общим меткам времени (новый листинг не ломает
расчёт), агломеративная кластеризация со средней связью: группы сливаются, только
если средняя корреляция между всеми их символами не ниже порога. Средняя связь не
даёт «цепочек»: A похож на B, B на C, но A не похож на C — в одну группу не сольются.

Результат хранится в базе. Пересчёт не удался — остаются прежние группы; групп ещё
нет — пусто, как было до 11.09.
"""
import logging
import math
import threading
import time
from datetime import datetime, UTC
from typing import Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

INTERVAL = "240"          # 4h-свечи
LOOKBACK_BARS = 180       # 30 суток
MIN_COMMON_BARS = 60      # меньше общих свечей — корреляцию пары не считаем
THRESHOLD = 0.7
REFRESH_SECONDS = 24 * 3600
_READ_TTL_SECONDS = 600

_lock = threading.Lock()
_cache = {"at": 0.0, "groups": {}}


def clear_cache() -> None:
    with _lock:
        _cache.update(at=0.0, groups={})


def _returns_by_time(candles) -> Dict[int, float]:
    """Логарифмическая доходность по метке времени закрытия свечи (свечи Bybit: [ts, o, h, l, c, v])."""
    closes = sorted((int(c[0]), float(c[4])) for c in candles if float(c[4]) > 0)
    return {t: math.log(price / prev) for (_, prev), (t, price) in zip(closes, closes[1:])}


def _pearson(a: Sequence[float], b: Sequence[float]) -> float:
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return 0.0
    return cov / math.sqrt(va * vb)


def correlation_matrix(candles_by_symbol) -> Dict[tuple, float]:
    """Корреляции пар {(a, b): r} по общим меткам времени; пары с малым пересечением — без значения."""
    rets = {s: _returns_by_time(c) for s, c in candles_by_symbol.items() if c}
    symbols = sorted(rets)
    matrix = {}
    for i, a in enumerate(symbols):
        for b in symbols[i + 1:]:
            common = sorted(rets[a].keys() & rets[b].keys())
            if len(common) < MIN_COMMON_BARS:
                continue
            r = _pearson([rets[a][t] for t in common], [rets[b][t] for t in common])
            matrix[(a, b)] = matrix[(b, a)] = r
    return matrix


def cluster(symbols, matrix, threshold: float = THRESHOLD) -> Dict[str, List[str]]:
    """Группы из двух и более символов: {'corr-1': [...], ...}, крупные первыми. Пара без значения — 0."""
    clusters = [[s] for s in sorted(symbols)]

    def link(x, y):
        values = [matrix.get((a, b), 0.0) for a in x for b in y]
        return sum(values) / len(values)

    while len(clusters) > 1:
        best, pair = None, None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                value = link(clusters[i], clusters[j])
                if value >= threshold and (best is None or value > best):
                    best, pair = value, (i, j)
        if pair is None:
            break
        i, j = pair
        clusters[i] = sorted(clusters[i] + clusters[j])
        del clusters[j]
    groups = sorted((c for c in clusters if len(c) > 1), key=lambda c: (-len(c), c))
    return {f"corr-{k}": g for k, g in enumerate(groups, start=1)}


def refresh(symbols=None, fetch: Optional[Callable] = None) -> Optional[Dict[str, List[str]]]:
    """Пересчитать группы и сохранить в базу. None — данных мало, прежние группы остаются."""
    if symbols is None:
        from config import SYMBOLS
        symbols = SYMBOLS
    if fetch is None:
        from data_loader import get_candles
        fetch = get_candles
    candles = {}
    for symbol in symbols:
        try:
            candles[symbol] = fetch(symbol, INTERVAL, LOOKBACK_BARS)
        except Exception:
            logger.warning("correlation_groups: свечи %s не получены", symbol, exc_info=True)
    usable = [s for s, c in candles.items() if c and len(c) > MIN_COMMON_BARS]
    if len(usable) < max(2, len(symbols) // 2):
        logger.warning("correlation_groups: данных мало (%d из %d символов) — остаются прежние группы",
                       len(usable), len(symbols))
        return None

    groups = cluster(usable, correlation_matrix({s: candles[s] for s in usable}))
    from database import save_correlation_groups
    save_correlation_groups(groups)
    with _lock:
        _cache.update(at=time.time(), groups=groups)
    logger.info("correlation_groups: %d групп из %d символов — %s", len(groups), len(usable),
                "; ".join(f"{name}: {', '.join(members)}" for name, members in groups.items()) or "нет")
    return groups


def get_groups() -> Dict[str, List[str]]:
    """Текущие группы из базы (кэш на 10 минут). Не прочитались — пусто: проверка групп в этом цикле не работает."""
    now = time.time()
    with _lock:
        if now - _cache["at"] < _READ_TTL_SECONDS:
            return _cache["groups"]
    try:
        from database import get_correlation_groups
        stored = get_correlation_groups()
        groups = stored["groups"] if stored else {}
    except Exception:
        logger.warning("correlation_groups: группы не прочитаны из базы — проверка групп в этом цикле пустая",
                       exc_info=True)
        groups = {}
    with _lock:
        _cache.update(at=now, groups=groups)
    return groups


def needs_refresh(now: Optional[datetime] = None) -> bool:
    """Групп нет или они старше суток."""
    from database import get_correlation_groups
    stored = get_correlation_groups()
    if not stored:
        return True
    computed_at = datetime.fromisoformat(stored["computed_at"])
    return ((now or datetime.now(UTC)) - computed_at).total_seconds() >= REFRESH_SECONDS
