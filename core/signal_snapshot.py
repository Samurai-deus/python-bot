"""
SignalSnapshot - immutable доменный объект для представления одного сигнала.

Это атомарная единица мышления системы, которая:
- Не зависит от Telegram, CSV, UI
- Используется ТОЛЬКО в runtime
- Передаётся между brain'ами
- Готова для логирования, статистики, портфельной логики, backtest/replay
"""
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Dict, Optional
from enum import Enum
from types import MappingProxyType
from core.market_state import MarketState, normalize_states_dict
from core.decision_core import MarketRegime


class SignalDecision(str, Enum):
    """Решение по сигналу"""
    ENTER = "ENTER"      # Вход разрешён
    SKIP = "SKIP"        # Пропустить (недостаточно качества)
    OBSERVE = "OBSERVE"  # Наблюдать (среднее качество)
    BLOCK = "BLOCK"      # Заблокирован (высокий риск, конфликт)


class RiskLevel(str, Enum):
    """Уровень риска"""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class VolatilityLevel(str, Enum):
    """Уровень волатильности"""
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    EXTREME = "EXTREME"
    UNKNOWN = "UNKNOWN"


class TrendType(str, Enum):
    """Тип тренда"""
    TREND = "TREND"
    RANGE = "RANGE"
    UNKNOWN = "UNKNOWN"


class RiskSentiment(str, Enum):
    """Рыночный риск-сентимент"""
    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SignalSnapshot:
    """
    Immutable snapshot одного торгового сигнала.
    
    Это атомарная единица мышления системы, которая содержит всю информацию
    о сигнале в момент его генерации. Snapshot не изменяется после создания.
    
    Используется для:
    - Передачи между brain'ами
    - Логирования и статистики
    - Портфельной логики
    - Backtest и replay
    """
    
    # ========== ИДЕНТИФИКАЦИЯ ==========
    timestamp: datetime
    symbol: str
    timeframe_anchor: str  # Основной таймфрейм (например "15m")
    
    # ========== РЫНОЧНОЕ СОСТОЯНИЕ ==========
    states: Dict[str, Optional[MarketState]]  # ТОЛЬКО enum, не строки
    market_regime: Optional[MarketRegime] = None
    volatility_level: Optional[VolatilityLevel] = None
    correlation_level: Optional[float] = None  # Средняя корреляция с рынком (0-1)
    
    # ========== ОЦЕНКИ ==========
    score: int = 0
    score_max: int = 125  # Максимальный возможный score
    confidence: float = 0.0  # Уверенность системы в сигнале (0-1, обязательное)
    entropy: float = 0.0  # Когнитивная неопределённость рынка (0-1, обязательное)
    
    # ========== РИСК ==========
    risk_level: RiskLevel = RiskLevel.MEDIUM
    recommended_leverage: Optional[float] = None
    tp: Optional[float] = None  # Take profit
    sl: Optional[float] = None  # Stop loss
    entry: Optional[float] = None  # Entry price
    
    # ========== РЕШЕНИЕ ==========
    decision: SignalDecision = SignalDecision.SKIP
    decision_reason: str = ""
    
    # ========== ДОПОЛНИТЕЛЬНАЯ ИНФОРМАЦИЯ ==========
    directions: Dict[str, str] = field(default_factory=dict)  # Направления трендов
    score_details: Dict = field(default_factory=dict)  # Детали скоринга
    reasons: list = field(default_factory=list)  # Причины решения
    
    def __post_init__(self):
        """
        Проверка инвариантов после создания.
        
        Все проверки должны быть явными - ValueError при нарушении.
        """
        # Инвариант 1: states содержит только MarketState enum или None
        # Проверяем, что states уже нормализованы (должны быть нормализованы до создания)
        for key, state in self.states.items():
            if state is not None and not isinstance(state, MarketState):
                raise ValueError(
                    f"Invalid state type in states[{key}]: {state} (type: {type(state).__name__}), "
                    f"expected MarketState or None. States must be normalized before creating SignalSnapshot."
                )
        
        # Инвариант 2: score <= score_max
        if self.score > self.score_max:
            raise ValueError(
                f"Score {self.score} exceeds maximum {self.score_max}"
            )
        
        # Инвариант 3: confidence ∈ [0, 1] (обязательное)
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"Confidence {self.confidence} must be in range [0, 1]"
            )
        
        # Инвариант 4: entropy ∈ [0, 1] (обязательное)
        if not (0.0 <= self.entropy <= 1.0):
            raise ValueError(
                f"Entropy {self.entropy} must be in range [0, 1]"
            )
        
        # Инвариант 5: tp > 0, sl > 0, entry > 0 если заданы
        if self.tp is not None and self.tp <= 0:
            raise ValueError(f"Take profit {self.tp} must be > 0")
        
        if self.sl is not None and self.sl <= 0:
            raise ValueError(f"Stop loss {self.sl} must be > 0")
        
        if self.entry is not None and self.entry <= 0:
            raise ValueError(f"Entry price {self.entry} must be > 0")
        
        # Инвариант 6: recommended_leverage > 0 если задан
        if self.recommended_leverage is not None and self.recommended_leverage <= 0:
            raise ValueError(f"Recommended leverage {self.recommended_leverage} must be > 0")
        
        # Инвариант 7: correlation_level ∈ [0, 1] если задан
        if self.correlation_level is not None:
            if not (0.0 <= self.correlation_level <= 1.0):
                raise ValueError(
                    f"Correlation level {self.correlation_level} must be in range [0, 1]"
                )
        
        # Инвариант 8: states должен быть глубоко неизменяемым (deep immutability)
        # Оборачиваем states в MappingProxyType для обеспечения read-only доступа
        # Используем object.__setattr__ потому что dataclass frozen=True блокирует обычное присваивание
        object.__setattr__(
            self,
            "states",
            MappingProxyType(dict(self.states))
        )
    
    @property
    def score_pct(self) -> float:
        """Процент от максимального score"""
        if self.score_max == 0:
            return 0.0
        return (self.score / self.score_max) * 100
    
    @property
    def has_entry_zone(self) -> bool:
        """Есть ли зона входа (entry, tp, sl)"""
        return self.entry is not None and self.tp is not None and self.sl is not None
    
    @property
    def rr_ratio(self) -> Optional[float]:
        """Risk/Reward ratio"""
        if not self.has_entry_zone:
            return None
        
        if self.entry is None or self.tp is None or self.sl is None:
            return None
        
        # Определяем направление по соотношению цен
        if self.tp > self.entry:
            # LONG
            risk = abs(self.entry - self.sl)
            reward = abs(self.tp - self.entry)
        else:
            # SHORT
            risk = abs(self.sl - self.entry)
            reward = abs(self.entry - self.tp)
        
        if risk == 0:
            return None
        
        return reward / risk
    
    @property
    def is_tradeable(self) -> bool:
        """Можно ли торговать по этому сигналу"""
        return (
            self.decision == SignalDecision.ENTER and
            self.risk_level != RiskLevel.HIGH and
            self.has_entry_zone
        )


def mode_to_decision(mode: str) -> SignalDecision:
    """
    Преобразует режим рынка (market_mode) в решение по сигналу.
    
    Args:
        mode: Режим рынка ("TRADE", "OBSERVE", "CAUTION", "STOP")
    
    Returns:
        SignalDecision: Решение по сигналу
    """
    mapping = {
        "TRADE": SignalDecision.ENTER,
        "OBSERVE": SignalDecision.OBSERVE,
        "CAUTION": SignalDecision.SKIP,
        "STOP": SignalDecision.BLOCK
    }
    return mapping.get(mode, SignalDecision.SKIP)


def risk_string_to_enum(risk: str) -> RiskLevel:
    """
    Преобразует строковое значение риска в RiskLevel enum.
    
    Args:
        risk: Уровень риска ("LOW", "MEDIUM", "HIGH")
    
    Returns:
        RiskLevel: Enum уровня риска
    """
    mapping = {
        "LOW": RiskLevel.LOW,
        "MEDIUM": RiskLevel.MEDIUM,
        "HIGH": RiskLevel.HIGH
    }
    return mapping.get(risk, RiskLevel.MEDIUM)


def volatility_string_to_enum(volatility: str) -> Optional[VolatilityLevel]:
    """
    Преобразует строковое значение волатильности в VolatilityLevel enum.
    
    Args:
        volatility: Уровень волатильности ("LOW", "NORMAL", "HIGH", "EXTREME", "UNKNOWN")
    
    Returns:
        VolatilityLevel или None
    """
    if not volatility:
        return None
    
    mapping = {
        "LOW": VolatilityLevel.LOW,
        "NORMAL": VolatilityLevel.NORMAL,
        "HIGH": VolatilityLevel.HIGH,
        "EXTREME": VolatilityLevel.EXTREME,
        "UNKNOWN": VolatilityLevel.UNKNOWN
    }
    return mapping.get(volatility.upper(), VolatilityLevel.UNKNOWN)

