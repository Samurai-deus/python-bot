"""
Модуль для определения состояния рынка на основе свечей.
"""
from typing import Optional, List, Any
from states import impulse, acceptance, loss_of_control, rejection
from core.market_state import MarketState


def determine_state(candles: List[List[Any]], atr_val: float) -> Optional[MarketState]:
    """
    Определяет состояние рынка на основе анализа свечей и ATR.
    
    Функция анализирует паттерны свечей в порядке приоритета:
    1. Rejection (D) - сильное движение против тренда
    2. Loss of Control (C) - потеря контроля, большие фитили
    3. Acceptance (B) - принятие, узкий диапазон
    4. Impulse (A) - импульс, сильное движение
    
    Args:
        candles: Список свечей в формате Bybit API.
                 Каждая свеча: [timestamp, open, high, low, close, volume, ...]
                 Минимум 7 свечей для корректного анализа.
        atr_val: Значение ATR (Average True Range) для нормализации движений.
                 Должно быть > 0.
    
    Returns:
        Optional[MarketState]: Состояние рынка:
            - MarketState.A - Импульс (Impulse): сильное движение в направлении тренда
            - MarketState.B - Принятие (Acceptance): узкий диапазон, консолидация
            - MarketState.C - Потеря контроля (Loss of Control): большие фитили, волатильность
            - MarketState.D - Отказ (Rejection): сильное движение против тренда
            - None - Неопределённое состояние:
                * Данных недостаточно для анализа
                * Ни одно из условий не выполнено
                * Рынок в переходном состоянии
    
    Note:
        None является валидным и осознанным результатом.
        Вызывающая сторона должна обрабатывать None, используя:
        - states.get("15m") для безопасного доступа
        - states.get("15m") is None для явной проверки
    
    Examples:
        >>> candles = [[...], [...], ...]  # 7+ свечей
        >>> atr_val = 100.0
        >>> state = determine_state(candles, atr_val)
        >>> if state:
        ...     print(f"Состояние: {state.value}")  # "A", "B", "C" или "D"
        ... else:
        ...     print("Состояние не определено")
    
    See Also:
        - core.market_state.MarketState: Enum состояний рынка
        - states.impulse(): Проверка импульса
        - states.acceptance(): Проверка принятия
        - states.loss_of_control(): Проверка потери контроля
        - states.rejection(): Проверка отказа
    """
    if rejection(candles, atr_val):
        return MarketState.D
    if loss_of_control(candles):
        return MarketState.C
    if acceptance(candles, atr_val):
        return MarketState.B
    if impulse(candles, atr_val):
        return MarketState.A
    return None
