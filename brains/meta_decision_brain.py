"""
Meta Decision Brain - верхнеуровневый модуль для решения WHEN NOT TO TRADE.

MetaDecisionBrain НЕ работает с рынком напрямую.
Он работает ТОЛЬКО с агрегированными метриками системы.

Это мета-уровень принятия решений, который анализирует состояние системы
в целом и решает, можно ли торговать в данный момент.
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple
from enum import Enum
from core.decision_core import MarketRegime


class SystemHealthStatus(str, Enum):
    """Состояние здоровья системы"""
    OK = "OK"
    DEGRADED = "DEGRADED"


class BlockLevel(str, Enum):
    """Уровень блокировки"""
    HARD = "HARD"  # Жёсткая блокировка - торговля полностью запрещена
    SOFT = "SOFT"  # Мягкая блокировка - торговля не рекомендуется, но возможна


class TimeContext(str, Enum):
    """Контекст времени"""
    SESSION_START = "SESSION_START"  # Начало торговой сессии
    SESSION_MID = "SESSION_MID"  # Середина сессии
    SESSION_END = "SESSION_END"  # Конец сессии
    AFTER_HOURS = "AFTER_HOURS"  # Вне торговых часов
    UNKNOWN = "UNKNOWN"  # Неизвестный контекст


@dataclass
class MetaDecisionResult:
    """
    Результат мета-решения о возможности торговли.
    
    Attributes:
        allow_trading: True если торговля разрешена, False если заблокирована
        reason: Объяснение решения (explainable)
        block_level: Уровень блокировки (HARD или SOFT), None если allow_trading=True
        cooldown_minutes: Время ожидания в минутах перед следующей проверкой
    """
    allow_trading: bool
    reason: str
    block_level: Optional[BlockLevel] = None
    cooldown_minutes: int = 0
    
    def __post_init__(self):
        """Проверка инвариантов"""
        if not self.allow_trading and self.block_level is None:
            raise ValueError("block_level must be set when allow_trading=False")
        if self.cooldown_minutes < 0:
            raise ValueError("cooldown_minutes must be >= 0")


class MetaDecisionBrain:
    """
    Верхнеуровневый модуль для решения WHEN NOT TO TRADE.
    
    MetaDecisionBrain анализирует агрегированные метрики системы и решает,
    можно ли торговать в данный момент. Это мета-уровень принятия решений,
    который работает с общим состоянием системы, а не с отдельными сигналами.
    
    Принципы:
    - НЕ работает с рынком напрямую
    - Работает ТОЛЬКО с агрегированными метриками
    - Детерминированный код без состояния
    - Лёгкий, быстрый, без внешних зависимостей
    """
    
    def __init__(self):
        """
        Инициализация MetaDecisionBrain.
        
        Класс не хранит состояние - все методы детерминированы.
        """
        pass
    
    def evaluate(
        self,
        market_regime: Optional[MarketRegime] = None,
        confidence_score: float = 0.5,
        entropy_score: float = 0.5,
        portfolio_exposure: float = 0.0,
        recent_outcomes: Optional[List[float]] = None,
        signals_count_recent: int = 0,
        system_health: SystemHealthStatus = SystemHealthStatus.OK,
        time_context: TimeContext = TimeContext.UNKNOWN,
    ) -> MetaDecisionResult:
        """
        Оценивает возможность торговли на основе агрегированных метрик.
        
        Args:
            market_regime: Режим рынка (опционально)
            confidence_score: Средняя уверенность системы (0.0 - 1.0)
            entropy_score: Средняя энтропия системы (0.0 - 1.0)
            portfolio_exposure: Экспозиция портфеля (0.0 - 1.0, где 1.0 = 100%)
            recent_outcomes: Список последних результатов (PnL или win rate)
            signals_count_recent: Количество сигналов за последний период
            system_health: Состояние здоровья системы
            time_context: Контекст времени
        
        Returns:
            MetaDecisionResult: Результат мета-решения
        
        Логика:
        - HARD BLOCK если выполнено любое критическое условие
        - SOFT BLOCK если выполнены предупреждающие условия
        - ALLOW только если НЕТ ни одного блокирующего условия
        """
        # Нормализуем входные данные
        confidence_score = max(0.0, min(1.0, confidence_score))
        entropy_score = max(0.0, min(1.0, entropy_score))
        portfolio_exposure = max(0.0, min(1.0, portfolio_exposure))
        
        # ========== HARD BLOCK - Критические условия ==========
        hard_block_reason = self._check_hard_block_conditions(
            entropy_score=entropy_score,
            confidence_score=confidence_score,
            portfolio_exposure=portfolio_exposure,
            system_health=system_health
        )
        
        if hard_block_reason:
            return MetaDecisionResult(
                allow_trading=False,
                reason=hard_block_reason,
                block_level=BlockLevel.HARD,
                cooldown_minutes=30  # Жёсткая блокировка - ждём 30 минут
            )
        
        # ========== SOFT BLOCK - Предупреждающие условия ==========
        soft_block_reason, cooldown = self._check_soft_block_conditions(
            confidence_score=confidence_score,
            entropy_score=entropy_score,
            signals_count_recent=signals_count_recent,
            recent_outcomes=recent_outcomes,
            portfolio_exposure=portfolio_exposure,
            time_context=time_context
        )
        
        if soft_block_reason:
            return MetaDecisionResult(
                allow_trading=False,
                reason=soft_block_reason,
                block_level=BlockLevel.SOFT,
                cooldown_minutes=cooldown
            )
        
        # ========== ALLOW - Нет блокирующих условий ==========
        return MetaDecisionResult(
            allow_trading=True,
            reason="No blocking conditions detected. System is ready for trading.",
            block_level=None,
            cooldown_minutes=0
        )
    
    def _check_hard_block_conditions(
        self,
        entropy_score: float,
        confidence_score: float,
        portfolio_exposure: float,
        system_health: SystemHealthStatus
    ) -> Optional[str]:
        """
        Проверяет условия для HARD BLOCK (жёсткой блокировки).
        
        HARD BLOCK если:
        1. entropy > 0.7 AND confidence < 0.4
        2. portfolio_exposure > 0.8
        3. system_health == DEGRADED
        
        Args:
            entropy_score: Средняя энтропия системы
            confidence_score: Средняя уверенность системы
            portfolio_exposure: Экспозиция портфеля
            system_health: Состояние здоровья системы
        
        Returns:
            str: Причина блокировки или None
        """
        # 1. entropy > 0.7 AND confidence < 0.4
        if entropy_score > 0.7 and confidence_score < 0.4:
            return (
                f"HARD BLOCK: High entropy ({entropy_score:.2f}) combined with low confidence "
                f"({confidence_score:.2f}) indicates system uncertainty. Trading is too risky."
            )
        
        # 2. portfolio_exposure > 0.8
        if portfolio_exposure > 0.8:
            return (
                f"HARD BLOCK: Portfolio exposure ({portfolio_exposure * 100:.1f}%) exceeds safe limit (80%). "
                f"Risk of overexposure."
            )
        
        # 3. system_health == DEGRADED
        if system_health == SystemHealthStatus.DEGRADED:
            return (
                f"HARD BLOCK: System health is DEGRADED. System is experiencing issues. "
                f"Trading is disabled until system recovers."
            )
        
        return None
    
    def _check_soft_block_conditions(
        self,
        confidence_score: float,
        entropy_score: float,
        signals_count_recent: int,
        recent_outcomes: Optional[List[float]],
        portfolio_exposure: float,
        time_context: TimeContext
    ) -> Tuple[Optional[str], int]:
        """
        Проверяет условия для SOFT BLOCK (мягкой блокировки).
        
        SOFT BLOCK если:
        1. signals_count_recent слишком высок (>10 за период)
        2. confidence средний, entropy средний (неопределённость)
        3. Плохие результаты в recent_outcomes
        
        Args:
            confidence_score: Средняя уверенность системы
            entropy_score: Средняя энтропия системы
            signals_count_recent: Количество сигналов за период
            recent_outcomes: Список последних результатов
            portfolio_exposure: Экспозиция портфеля
            time_context: Контекст времени
        
        Returns:
            tuple: (причина блокировки, cooldown_minutes) или (None, 0)
        """
        # 1. signals_count_recent слишком высок
        if signals_count_recent > 10:
            return (
                f"SOFT BLOCK: Too many signals in recent period ({signals_count_recent}). "
                f"Risk of overtrading. Recommend cooldown.",
                15  # 15 минут cooldown
            )
        
        # 2. confidence средний, entropy средний (неопределённость)
        if 0.4 <= confidence_score <= 0.6 and 0.4 <= entropy_score <= 0.6:
            # Средние значения указывают на неопределённость
            if portfolio_exposure > 0.5:
                return (
                    f"SOFT BLOCK: Medium confidence ({confidence_score:.2f}) and entropy ({entropy_score:.2f}) "
                    f"with high exposure ({portfolio_exposure * 100:.1f}%) indicate uncertainty. "
                    f"Recommend caution.",
                    10  # 10 минут cooldown
                )
        
        # 3. Плохие результаты в recent_outcomes
        if recent_outcomes and len(recent_outcomes) >= 3:
            # Вычисляем средний результат
            avg_outcome = sum(recent_outcomes) / len(recent_outcomes)
            negative_count = sum(1 for outcome in recent_outcomes if outcome < 0)
            
            # Если большинство результатов отрицательные
            if negative_count > len(recent_outcomes) * 0.6:
                return (
                    f"SOFT BLOCK: Recent outcomes show {negative_count}/{len(recent_outcomes)} negative results "
                    f"(avg: {avg_outcome:.2f}). System may need recalibration.",
                    20  # 20 минут cooldown
                )
        
        # 4. Высокая экспозиция с низкой уверенностью
        if portfolio_exposure > 0.6 and confidence_score < 0.5:
            return (
                f"SOFT BLOCK: High exposure ({portfolio_exposure * 100:.1f}%) with low confidence "
                f"({confidence_score:.2f}). Recommend reducing exposure before new trades.",
                15  # 15 минут cooldown
            )
        
        # 5. Контекст времени (например, конец сессии)
        if time_context == TimeContext.SESSION_END:
            # В конце сессии может быть повышенная волатильность
            if entropy_score > 0.6:
                return (
                    f"SOFT BLOCK: End of trading session with high entropy ({entropy_score:.2f}). "
                    f"Market conditions may be unstable.",
                    5  # 5 минут cooldown
                )
        
        return (None, 0)
    
    def explain_decision(self, result: MetaDecisionResult) -> str:
        """
        Генерирует подробное объяснение решения.
        
        Args:
            result: MetaDecisionResult для объяснения
        
        Returns:
            str: Подробное объяснение решения
        """
        if result.allow_trading:
            return (
                f"✅ TRADING ALLOWED\n\n"
                f"Reason: {result.reason}\n"
                f"System is ready for trading. No blocking conditions detected."
            )
        else:
            block_type = "HARD" if result.block_level == BlockLevel.HARD else "SOFT"
            return (
                f"🚫 TRADING BLOCKED ({block_type})\n\n"
                f"Reason: {result.reason}\n"
                f"Cooldown: {result.cooldown_minutes} minutes\n\n"
                f"{'⚠️ Trading is completely disabled until conditions improve.' if result.block_level == BlockLevel.HARD else '💡 Trading is not recommended but may be possible with caution.'}"
            )


# ========== ТОЧКА РАСШИРЕНИЯ ДЛЯ FUTURE BRAINS ==========

class MetaDecisionBrainExtension:
    """
    Базовый класс для расширений MetaDecisionBrain.
    
    Позволяет добавлять дополнительные проверки без изменения основного класса.
    """
    
    def check_extension_conditions(
        self,
        market_regime: Optional[MarketRegime] = None,
        confidence_score: float = 0.5,
        entropy_score: float = 0.5,
        portfolio_exposure: float = 0.0,
        recent_outcomes: Optional[List[float]] = None,
        signals_count_recent: int = 0,
        system_health: SystemHealthStatus = SystemHealthStatus.OK,
        time_context: TimeContext = TimeContext.UNKNOWN,
    ) -> Optional[MetaDecisionResult]:
        """
        Проверяет дополнительные условия (переопределяется в подклассах).
        
        Returns:
            MetaDecisionResult или None (если нет дополнительных условий)
        """
        return None

