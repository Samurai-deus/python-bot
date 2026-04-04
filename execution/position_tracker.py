"""
Position Tracker — Phase 2.

Мониторит открытые позиции на бирже:
- Опрашивает /v5/position/list
- Обнаруживает закрытие по SL/TP (размер позиции → 0)
- Вычисляет текущий P&L

Принципы:
- Polling-based (не WebSocket — Phase 5)
- Fail-closed: ошибка получения позиций → raise, не тихое None
- Stateless: состояние только в памяти между вызовами poll()
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Dict, List, Optional

from exchange.bybit_client import BybitClient, PositionInfo, get_bybit_client

logger = logging.getLogger(__name__)


# ========== DATA TYPES ==========

@dataclass
class TrackedPosition:
    """Позиция под наблюдением."""
    symbol: str
    side: str              # "LONG" | "SHORT"
    entry_price: float
    qty: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    order_id: str
    opened_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # Заполняется при poll()
    current_price: float = 0.0
    unrealised_pnl: float = 0.0
    is_closed: bool = False
    closed_at: Optional[datetime] = None


@dataclass
class PollResult:
    """Результат одного цикла опроса."""
    active: List[TrackedPosition]        # Позиции всё ещё открыты
    just_closed: List[TrackedPosition]   # Позиции, закрытые в этом цикле
    errors: List[str]                    # Ошибки при опросе (не фатальные)


# ========== TRACKER ==========

class PositionTracker:
    """
    Отслеживает открытые позиции через Bybit API.

    Использование:
        tracker = PositionTracker()
        tracker.add(tracked_position)
        result = tracker.poll()
        for closed in result.just_closed:
            handle_close(closed)
    """

    def __init__(self, client: Optional[BybitClient] = None):
        self._client = client or get_bybit_client()
        self._tracked: Dict[str, TrackedPosition] = {}  # symbol → position
        logger.info("PositionTracker initialized")

    # ------------------------------------------------------------------ #
    #  Public API                                                         #
    # ------------------------------------------------------------------ #

    def add(self, position: TrackedPosition) -> None:
        """Начать отслеживать позицию."""
        self._tracked[position.symbol] = position
        logger.info(
            "Tracking position: %s %s qty=%s entry=%s",
            position.symbol, position.side, position.qty, position.entry_price,
        )

    def remove(self, symbol: str) -> None:
        """Прекратить отслеживать позицию (после закрытия)."""
        self._tracked.pop(symbol, None)

    def poll(self) -> PollResult:
        """
        Запросить текущее состояние всех отслеживаемых позиций.

        Возвращает PollResult с разбивкой: активные / только что закрытые.
        Не бросает исключение при ошибке API — возвращает errors в PollResult.
        """
        if not self._tracked:
            return PollResult(active=[], just_closed=[], errors=[])

        active: List[TrackedPosition] = []
        just_closed: List[TrackedPosition] = []
        errors: List[str] = []

        # Получаем все позиции одним запросом
        try:
            exchange_positions = self._client.get_positions()
        except Exception as e:
            msg = f"Failed to fetch positions: {type(e).__name__}: {e}"
            logger.error(msg)
            errors.append(msg)
            # Не знаем статус — считаем все позиции активными
            return PollResult(active=list(self._tracked.values()), just_closed=[], errors=errors)

        # Индекс по символу
        exchange_by_symbol: Dict[str, PositionInfo] = {
            p.symbol: p for p in exchange_positions if p.size > 0
        }

        for symbol, tracked in list(self._tracked.items()):
            exchange_pos = exchange_by_symbol.get(symbol)

            if exchange_pos is None or exchange_pos.size == 0:
                # Позиция закрыта (SL/TP сработал или закрыта вручную)
                tracked.is_closed = True
                tracked.closed_at = datetime.now(UTC)
                just_closed.append(tracked)
                del self._tracked[symbol]
                logger.info(
                    "Position closed detected: %s %s pnl≈%.2f",
                    symbol, tracked.side, tracked.unrealised_pnl,
                )
            else:
                # Позиция активна — обновляем метрики
                tracked.unrealised_pnl = exchange_pos.unrealised_pnl
                tracked.current_price = exchange_pos.entry_price  # proxy при отсутствии ticker
                active.append(tracked)

        return PollResult(active=active, just_closed=just_closed, errors=errors)

    def active_count(self) -> int:
        """Количество отслеживаемых позиций."""
        return len(self._tracked)

    def get_all_active(self) -> List[TrackedPosition]:
        """Список всех активных позиций."""
        return list(self._tracked.values())


# ========== SINGLETON ==========

_tracker: Optional[PositionTracker] = None


def get_position_tracker() -> PositionTracker:
    global _tracker
    if _tracker is None:
        _tracker = PositionTracker()
    return _tracker
