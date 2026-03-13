"""
Централизованное описание состояний рынка.

Это единый источник истины для всех состояний рынка в системе.
Используется вместо магических строк "A", "B", "C", "D" по всему коду.
"""
from enum import Enum
from typing import Optional


class MarketState(str, Enum):
    """
    Состояния рынка (Market States).
    
    Определяются на основе анализа паттернов свечей:
    - A: Импульс (Impulse) - сильное движение в направлении тренда
    - B: Принятие (Acceptance) - узкий диапазон, консолидация
    - C: Потеря контроля (Loss of Control) - большие фитили, волатильность
    - D: Отказ (Rejection) - сильное движение против тренда
    """
    A = "A"  # Импульс (Impulse)
    B = "B"  # Принятие (Acceptance)
    C = "C"  # Потеря контроля (Loss of Control)
    D = "D"  # Отказ (Rejection)
    
    @classmethod
    def is_valid(cls, value: Optional[str]) -> bool:
        """
        Проверяет, является ли значение валидным состоянием рынка.
        
        Args:
            value: Значение для проверки (может быть None)
        
        Returns:
            bool: True если значение валидно, False если нет
        """
        if value is None:
            return False
        return value in cls._value2member_map_
    
    @classmethod
    def from_string(cls, value: Optional[str]) -> Optional['MarketState']:
        """
        Преобразует строку в MarketState enum.
        
        Args:
            value: Строковое значение ("A", "B", "C", "D" или None)
        
        Returns:
            MarketState или None если значение невалидно
        """
        if value is None:
            return None
        try:
            return cls(value)
        except ValueError:
            return None
    
    @classmethod
    def to_string(cls, value: Optional['MarketState']) -> Optional[str]:
        """
        Преобразует MarketState enum в строку.
        
        Args:
            value: MarketState enum или None
        
        Returns:
            str или None
        """
        if value is None:
            return None
        if isinstance(value, MarketState):
            return value.value
        return None
    
    def __str__(self) -> str:
        """Возвращает строковое значение enum"""
        return self.value


# Словарь для текстового представления состояний (для Telegram и логов)
STATE_TEXT = {
    MarketState.A: "Импульс",
    MarketState.B: "Принятие",
    MarketState.C: "Потеря контроля",
    MarketState.D: "Отказ",
    None: "Неопределённость"
}


def get_state_text(state: Optional[MarketState]) -> str:
    """
    Получает текстовое представление состояния рынка.
    
    Args:
        state: MarketState enum или None
    
    Returns:
        str: Текстовое описание состояния или "Неопределённость" для None
    """
    if state is None:
        return STATE_TEXT[None]
    return STATE_TEXT.get(state, "Неопределённость")


def normalize_state(value: Optional[str]) -> Optional[MarketState]:
    """
    Нормализует строковое значение состояния в MarketState enum.
    
    Args:
        value: Строковое значение состояния ("A", "B", "C", "D" или None)
    
    Returns:
        MarketState или None если значение невалидно
    """
    if value is None:
        return None
    
    # Пробуем преобразовать
    state = MarketState.from_string(value)
    if state is not None:
        return state
    
    # Если не получилось, возвращаем None (невалидное значение)
    return None


def state_to_string(state: Optional[MarketState]) -> str:
    """
    Преобразует MarketState в строку для записи в CSV/БД.
    
    Args:
        state: MarketState enum или None
    
    Returns:
        str: Строковое значение или пустая строка для None
    """
    if state is None:
        return ""
    return state.value


def validate_state(state, context: str = "") -> Optional[MarketState]:
    """
    Проверяет инвариант: state должен быть MarketState enum или None.
    
    КРИТИЧНО: MarketState - ключевая доменная сущность.
    Ошибка типа (str вместо enum) - архитектурная ошибка, которая может
    привести к неправильным торговым решениям.
    
    Args:
        state: Значение для проверки
        context: Контекст проверки (для логирования)
    
    Returns:
        MarketState или None (безопасный fallback при невалидном типе)
    """
    import logging
    logger = logging.getLogger(__name__)
    
    # Валидные значения: None или MarketState enum
    if state is None:
        return None
    
    if isinstance(state, MarketState):
        return state
    
    # НЕВАЛИДНЫЙ ТИП - это архитектурная ошибка
    # Логируем ERROR, но не падаем (fail-safe)
    error_msg = (
        f"Invalid market state type detected: {state} (type: {type(state).__name__})"
    )
    if context:
        error_msg += f" in context: {context}"
    
    logger.error(error_msg, exc_info=False)
    
    # Безопасный fallback: пытаемся нормализовать, если это строка
    if isinstance(state, str):
        normalized = normalize_state(state)
        if normalized is not None:
            logger.warning(f"Auto-normalized string '{state}' to MarketState enum (should be normalized earlier)")
            return normalized
    
    # Если не удалось нормализовать - возвращаем None (безопасный fallback)
    logger.warning(f"Could not normalize state '{state}', using None as fallback")
    return None


def normalize_states_dict(states: dict) -> dict:
    """
    Нормализует словарь состояний, преобразуя все строковые значения в MarketState enum.
    
    Используется на границе системы для преобразования данных из IO-слоя (CSV, БД)
    в формат, пригодный для runtime-логики.
    
    После нормализации ВСЕ значения должны быть MarketState enum или None.
    Если обнаружены невалидные типы - они логируются и заменяются на None.
    
    Args:
        states: Словарь состояний, где значения могут быть:
            - MarketState enum
            - Строки ("A", "B", "C", "D")
            - None
    
    Returns:
        dict: Словарь с нормализованными значениями (только MarketState enum или None)
    
    Examples:
        >>> states = {"15m": "D", "30m": MarketState.A, "1h": None}
        >>> normalized = normalize_states_dict(states)
        >>> normalized["15m"]  # MarketState.D
        >>> normalized["30m"]  # MarketState.A
        >>> normalized["1h"]  # None
    """
    normalized = {}
    for key, value in states.items():
        # Используем validate_state для проверки инварианта
        # Это защищает от некорректных типов, которые могли проскочить
        validated = validate_state(value, context=f"normalize_states_dict[{key}]")
        normalized[key] = validated
    return normalized

