"""
Метрики прогона по истории (Ф1 плана трейдера, шаг 4).

Главная мера — ожидание в R (результат сделки / риск при входе) с 95 % доверительным
интервалом. Интервал — блочным бутстрепом по дням: сделки одного дня коррелируют (одно
движение рынка выбивает несколько позиций), и обычный интервал вышел бы обманчиво узким.
"""
import random
from collections import defaultdict
from datetime import UTC, datetime
from typing import Dict, Iterable, List, Sequence, Tuple

DAY_MS = 86_400_000


def summary(trades: Sequence, bootstrap: int = 2000, seed: int = 7) -> Dict:
    """Сделок, доля прибыльных, ожидание в R с ДИ, профит-фактор, макс. просадка в R."""
    n = len(trades)
    if not n:
        return {"trades": 0}
    rs = [t.r for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    low, high = expectancy_ci(trades, bootstrap, seed)
    return {
        "trades": n,
        "win_rate": len(wins) / n,
        "expectancy_r": sum(rs) / n,
        "ci95_r": (low, high),
        "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else float("inf"),
        "max_drawdown_r": max_drawdown(rs),
        "total_r": sum(rs),
        "pnl": sum(t.pnl for t in trades),
        "fees": sum(t.fees for t in trades),
        "funding": sum(t.funding for t in trades),
    }


def expectancy_ci(trades: Sequence, bootstrap: int = 2000, seed: int = 7, alpha: float = 0.05) -> Tuple[float, float]:
    """Блочный бутстреп по дням закрытия: день целиком — одна единица выборки."""
    days: Dict[int, List[float]] = defaultdict(list)
    for t in trades:
        days[t.exit_t // DAY_MS].append(t.r)
    blocks = list(days.values())
    if len(blocks) < 2:
        mean = sum(t.r for t in trades) / len(trades)
        return mean, mean
    rng = random.Random(seed)
    means = []
    for _ in range(bootstrap):
        sample = [r for _ in range(len(blocks)) for r in rng.choice(blocks)]
        means.append(sum(sample) / len(sample))
    means.sort()
    return means[int(alpha / 2 * bootstrap)], means[int((1 - alpha / 2) * bootstrap) - 1]


def max_drawdown(rs: Iterable[float]) -> float:
    peak = equity = drawdown = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def by_key(trades: Sequence, key) -> Dict[str, Dict]:
    groups: Dict[str, list] = defaultdict(list)
    for t in trades:
        groups[key(t)].append(t)
    return {k: summary(v) for k, v in sorted(groups.items())}


def month_of(t) -> str:
    return datetime.fromtimestamp(t.exit_t / 1000, UTC).strftime("%Y-%m")


def splits(start_ms: int, end_ms: int, parts: int = 3, holdout_days: int = 75) -> Tuple[List[Tuple[int, int]], Tuple[int, int]]:
    """Отрезки для проверки вперёд по времени и отложенный конец (смотреть один раз)."""
    holdout_start = end_ms - holdout_days * DAY_MS
    step = (holdout_start - start_ms) // parts
    return [(start_ms + i * step, start_ms + (i + 1) * step) for i in range(parts)], (holdout_start, end_ms)


def within(trades: Sequence, window: Tuple[int, int]) -> List:
    return [t for t in trades if window[0] <= t.entry_t < window[1]]


def render(title: str, stats: Dict) -> str:
    if not stats.get("trades"):
        return f"{title}: сделок нет"
    low, high = stats["ci95_r"]
    return (f"{title}: {stats['trades']} сд., прибыльных {100 * stats['win_rate']:.0f} %, "
            f"ожидание {stats['expectancy_r']:+.3f} R [{low:+.3f}; {high:+.3f}], "
            f"PF {stats['profit_factor']:.2f}, просадка {stats['max_drawdown_r']:.1f} R")
