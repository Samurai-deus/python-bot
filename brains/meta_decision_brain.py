"""
Meta Decision Brain - верхнеуровневый модуль для решения WHEN NOT TO TRADE.

MetaDecisionBrain НЕ работает с рынком напрямую.
Он работает ТОЛЬКО с агрегированными метриками системы.

Это мета-уровень принятия решений, который анализирует состояние системы
в целом и решает, можно ли торговать в данный момент.

АРХИТЕКТУРА ПЕРЕХОДОВ СОСТОЯНИЙ:
- Явные состояния: ALLOW, HARD_BLOCK, SOFT_BLOCK
- Явные переходы через методы: _transition_to_hard_block, _transition_to_soft_block, _transition_to_allow
- Приоритет проверок: HARD_BLOCK > SOFT_BLOCK > ALLOW (явно заявлен)
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


class DecisionState(str, Enum):
    """
    Явные состояния решения о торговле.
    
    Используется для явного определения текущего состояния системы
    и явных переходов между состояниями.
    """
    ALLOW = "ALLOW"  # Торговля разрешена
    HARD_BLOCK = "HARD_BLOCK"  # Жёсткая блокировка торговли
    SOFT_BLOCK = "SOFT_BLOCK"  # Мягкая блокировка торговли


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
    - Явные переходы состояний через методы _transition_to_*
    
    ПЕРЕХОДЫ СОСТОЯНИЙ (явные, в порядке приоритета):
    1. HARD_BLOCK - проверяется первым (высший приоритет)
    2. SOFT_BLOCK - проверяется вторым (средний приоритет)
    3. ALLOW - состояние по умолчанию (низший приоритет)
    """
    
    # Явные константы для приоритетности проверок
    CHECK_PRIORITY_HARD_BLOCK = 1  # Высший приоритет
    CHECK_PRIORITY_SOFT_BLOCK = 2  # Средний приоритет
    CHECK_PRIORITY_ALLOW = 3  # Низший приоритет (по умолчанию)
    
    # Явные константы для cooldown
    HARD_BLOCK_COOLDOWN_MINUTES = 30
    SOFT_BLOCK_COOLDOWN_OVERTRADING = 15
    SOFT_BLOCK_COOLDOWN_UNCERTAINTY = 10
    SOFT_BLOCK_COOLDOWN_BAD_OUTCOMES = 20
    SOFT_BLOCK_COOLDOWN_HIGH_EXPOSURE = 15
    SOFT_BLOCK_COOLDOWN_SESSION_END = 5
    ALLOW_COOLDOWN_MINUTES = 0
    
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
        
        Явный порядок проверок (по приоритету):
        1. HARD_BLOCK (приоритет 1) - проверяется первым
        2. SOFT_BLOCK (приоритет 2) - проверяется вторым
        3. ALLOW (приоритет 3) - состояние по умолчанию
        
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
        
        Переходы состояний:
        - Если выполнено условие HARD_BLOCK → переход в HARD_BLOCK
        - Если выполнено условие SOFT_BLOCK → переход в SOFT_BLOCK
        - Иначе → переход в ALLOW
        """
        # Явная нормализация входных данных
        normalized_inputs = self._normalize_inputs(
            confidence_score=confidence_score,
            entropy_score=entropy_score,
            portfolio_exposure=portfolio_exposure
        )
        
        # ЯВНЫЙ ПЕРЕХОД 1: Проверка HARD_BLOCK (приоритет 1)
        hard_block_result = self._evaluate_hard_block_transition(
            entropy_score=normalized_inputs['entropy_score'],
            confidence_score=normalized_inputs['confidence_score'],
            portfolio_exposure=normalized_inputs['portfolio_exposure'],
            system_health=system_health
        )
        if hard_block_result is not None:
            return hard_block_result
        
        # ЯВНЫЙ ПЕРЕХОД 2: Проверка SOFT_BLOCK (приоритет 2)
        soft_block_result = self._evaluate_soft_block_transition(
            confidence_score=normalized_inputs['confidence_score'],
            entropy_score=normalized_inputs['entropy_score'],
            signals_count_recent=signals_count_recent,
            recent_outcomes=recent_outcomes,
            portfolio_exposure=normalized_inputs['portfolio_exposure'],
            time_context=time_context
        )
        if soft_block_result is not None:
            return soft_block_result
        
        # ЯВНЫЙ ПЕРЕХОД 3: Переход в ALLOW (приоритет 3, по умолчанию)
        return self._transition_to_allow()
    
    def _normalize_inputs(
        self,
        confidence_score: float,
        entropy_score: float,
        portfolio_exposure: float
    ) -> dict:
        """
        Явная нормализация входных данных.
        
        Обеспечивает, что все значения находятся в допустимом диапазоне [0.0, 1.0].
        
        Args:
            confidence_score: Уверенность системы
            entropy_score: Энтропия системы
            portfolio_exposure: Экспозиция портфеля
        
        Returns:
            dict: Словарь с нормализованными значениями
        """
        return {
            'confidence_score': max(0.0, min(1.0, confidence_score)),
            'entropy_score': max(0.0, min(1.0, entropy_score)),
            'portfolio_exposure': max(0.0, min(1.0, portfolio_exposure))
        }
    
    def _evaluate_hard_block_transition(
        self,
        entropy_score: float,
        confidence_score: float,
        portfolio_exposure: float,
        system_health: SystemHealthStatus
    ) -> Optional[MetaDecisionResult]:
        """
        Явная проверка перехода в состояние HARD_BLOCK.
        
        Проверяет критические условия для жёсткой блокировки торговли.
        Если условие выполнено, выполняет явный переход в HARD_BLOCK.
        
        Args:
            entropy_score: Средняя энтропия системы
            confidence_score: Средняя уверенность системы
            portfolio_exposure: Экспозиция портфеля
            system_health: Состояние здоровья системы
        
        Returns:
            MetaDecisionResult с HARD_BLOCK или None (если переход не требуется)
        """
        hard_block_reason = self._check_hard_block_conditions(
            entropy_score=entropy_score,
            confidence_score=confidence_score,
            portfolio_exposure=portfolio_exposure,
            system_health=system_health
        )
        
        if hard_block_reason is not None:
            return self._transition_to_hard_block(reason=hard_block_reason)
        
        return None
    
    def _evaluate_soft_block_transition(
        self,
        confidence_score: float,
        entropy_score: float,
        signals_count_recent: int,
        recent_outcomes: Optional[List[float]],
        portfolio_exposure: float,
        time_context: TimeContext
    ) -> Optional[MetaDecisionResult]:
        """
        Явная проверка перехода в состояние SOFT_BLOCK.
        
        Проверяет предупреждающие условия для мягкой блокировки торговли.
        Если условие выполнено, выполняет явный переход в SOFT_BLOCK.
        
        Args:
            confidence_score: Средняя уверенность системы
            entropy_score: Средняя энтропия системы
            signals_count_recent: Количество сигналов за период
            recent_outcomes: Список последних результатов
            portfolio_exposure: Экспозиция портфеля
            time_context: Контекст времени
        
        Returns:
            MetaDecisionResult с SOFT_BLOCK или None (если переход не требуется)
        """
        soft_block_reason, cooldown = self._check_soft_block_conditions(
            confidence_score=confidence_score,
            entropy_score=entropy_score,
            signals_count_recent=signals_count_recent,
            recent_outcomes=recent_outcomes,
            portfolio_exposure=portfolio_exposure,
            time_context=time_context
        )
        
        if soft_block_reason is not None:
            return self._transition_to_soft_block(
                reason=soft_block_reason,
                cooldown_minutes=cooldown
            )
        
        return None
    
    def _transition_to_hard_block(self, reason: str) -> MetaDecisionResult:
        """
        Явный переход в состояние HARD_BLOCK.
        
        Создаёт результат с жёсткой блокировкой торговли.
        
        Args:
            reason: Причина блокировки
        
        Returns:
            MetaDecisionResult с allow_trading=False, block_level=HARD
        """
        return MetaDecisionResult(
            allow_trading=False,
            reason=reason,
            block_level=BlockLevel.HARD,
            cooldown_minutes=self.HARD_BLOCK_COOLDOWN_MINUTES
        )
    
    def _transition_to_soft_block(
        self,
        reason: str,
        cooldown_minutes: int
    ) -> MetaDecisionResult:
        """
        Явный переход в состояние SOFT_BLOCK.
        
        Создаёт результат с мягкой блокировкой торговли.
        
        Args:
            reason: Причина блокировки
            cooldown_minutes: Время ожидания в минутах
        
        Returns:
            MetaDecisionResult с allow_trading=False, block_level=SOFT
        """
        return MetaDecisionResult(
            allow_trading=False,
            reason=reason,
            block_level=BlockLevel.SOFT,
            cooldown_minutes=cooldown_minutes
        )
    
    def _transition_to_allow(self) -> MetaDecisionResult:
        """
        Явный переход в состояние ALLOW.
        
        Создаёт результат с разрешением торговли.
        
        Returns:
            MetaDecisionResult с allow_trading=True, block_level=None
        """
        return MetaDecisionResult(
            allow_trading=True,
            reason="No blocking conditions detected. System is ready for trading.",
            block_level=None,
            cooldown_minutes=self.ALLOW_COOLDOWN_MINUTES
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
        2. (экспозиция — предел Risk Core, здесь не проверяется)
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
        
        # 2. Экспозиция жёстким блоком не является (11.09.2026): это тот же предел номинала,
        #    что у Risk Core, только на 20 % строже — capital.position_size подбирал размер
        #    под предел, а мета-мозг его отклонял. Предел держит Risk Core; экспозиция
        #    (доля разрешённого номинала) участвует в мягких правилах вместе с уверенностью.
        
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
                self.SOFT_BLOCK_COOLDOWN_OVERTRADING
            )
        
        # 2. confidence средний, entropy средний (неопределённость)
        if 0.4 <= confidence_score <= 0.6 and 0.4 <= entropy_score <= 0.6:
            # Средние значения указывают на неопределённость
            if portfolio_exposure > 0.5:
                return (
                    f"SOFT BLOCK: Medium confidence ({confidence_score:.2f}) and entropy ({entropy_score:.2f}) "
                    f"with high exposure ({portfolio_exposure * 100:.1f}%) indicate uncertainty. "
                    f"Recommend caution.",
                    self.SOFT_BLOCK_COOLDOWN_UNCERTAINTY
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
                    self.SOFT_BLOCK_COOLDOWN_BAD_OUTCOMES
                )
        
        # 4. Высокая экспозиция с низкой уверенностью
        if portfolio_exposure > 0.6 and confidence_score < 0.5:
            return (
                f"SOFT BLOCK: High exposure ({portfolio_exposure * 100:.1f}%) with low confidence "
                f"({confidence_score:.2f}). Recommend reducing exposure before new trades.",
                self.SOFT_BLOCK_COOLDOWN_HIGH_EXPOSURE
            )
        
        # 5. Контекст времени (например, конец сессии)
        if time_context == TimeContext.SESSION_END:
            # В конце сессии может быть повышенная волатильность
            if entropy_score > 0.6:
                return (
                    f"SOFT BLOCK: End of trading session with high entropy ({entropy_score:.2f}). "
                    f"Market conditions may be unstable.",
                    self.SOFT_BLOCK_COOLDOWN_SESSION_END
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

