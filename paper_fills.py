"""
Какие экстремумы свечи появились после прошлой проверки бумажного монитора.

Монитор раз в 15 с берёт текущую 5-минутную свечу. Её минимум и максимум
копятся с начала свечи, и часть из них прошлые проверки уже учли, когда стоп
стоял в другом месте. До 10.09.2026 минимум, случившийся до того, как трейлинг
подтянул стоп выше, на следующей проверке той же свечи «пробивал» новый стоп,
и сделка закрывалась по стопу задним числом.

Новые экстремумы — вся свеча, если она новая (или символ ещё не встречался:
после рестарта стоп из базы действует и во время простоя). Иначе — только
обновившийся минимум (ниже прежнего) и максимум (выше прежнего).
"""
from typing import Dict, Optional, Tuple


class CandleWatermark:
    def __init__(self) -> None:
        self._seen: Dict[str, Tuple[object, float, float]] = {}

    def fresh_extremes(self, symbol: str, candle_start, low: float, high: float
                       ) -> Tuple[Optional[float], Optional[float]]:
        """(новый минимум или None, новый максимум или None) для этой проверки."""
        prev = self._seen.get(symbol)
        self._seen[symbol] = (candle_start, low, high)
        if prev is None or prev[0] != candle_start:
            return low, high
        _, prev_low, prev_high = prev
        return (low if low < prev_low else None), (high if high > prev_high else None)
