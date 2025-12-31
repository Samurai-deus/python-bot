"""
Position Sizer - расчет допустимого размера позиции.

PositionSizer НЕ принимает решения о входе — только размер.
Рассчитывает допустимый размер позиции на основе:
- Confidence (уверенность системы)
- Entropy (когнитивная неопределённость)
- Состояния портфеля

Архитектура:
- Чистый детерминированный код
- Без singleton и глобального состояния
- Расширяемый для будущих факторов (regime, volatility)
"""
from dataclasses import dataclass
from typing import Optional, Protocol
from config import RISK_PERCENT, INITIAL_BALANCE


# ========== КОНФИГУРАЦИЯ ==========

class PositionSizingConfig:
    """Конфигурация для PositionSizer"""
    
    # Базовый риск на сделку (% от баланса)
    max_risk_per_trade: float = RISK_PERCENT  # 2.0% по умолчанию
    
    # Минимальный порог риска (если итоговый риск меньше — позиция не разрешена)
    min_risk_threshold: float = 0.5  # 0.5% от баланса
    
    # Ограничения для факторов
    confidence_min: float = 0.2  # Минимальная confidence для использования
    confidence_max: float = 1.0  # Максимальная confidence
    entropy_min: float = 0.1  # Минимальный entropy_factor (1 - entropy)
    entropy_max: float = 1.0  # Максимальный entropy_factor


# ========== ПРОТОКОЛ ДЛЯ PORTFOLIO STATE ==========

class PortfolioStateProtocol(Protocol):
    """
    Протокол для состояния портфеля.
    
    Позволяет PositionSizer работать с любым объектом,
    который предоставляет необходимые методы.
    """
    
    def total_exposure(self) -> float:
        """Возвращает суммарную экспозицию портфеля"""
        ...
    
    def available_risk_ratio(self) -> float:
        """
        Возвращает доступный риск-коэффициент (0.0 - 1.0).
        
        Например:
        - 1.0 = весь риск-бюджет доступен
        - 0.5 = половина риск-бюджета использована
        - 0.0 = весь риск-бюджет использован
        """
        ...


# ========== РЕЗУЛЬТАТ РАСЧЁТА ==========

@dataclass
class PositionSizingResult:
    """
    Результат расчёта размера позиции.
    
    Attributes:
        position_allowed: True если позиция разрешена, False если риск слишком мал
        final_risk: Итоговый риск после применения всех факторов (% от баланса)
        base_risk: Базовый риск до применения факторов (% от баланса)
        confidence_factor: Множитель от confidence (0.2 - 1.0)
        entropy_factor: Множитель от entropy (0.1 - 1.0)
        portfolio_factor: Множитель от состояния портфеля (0.0 - 1.0)
        reason: Объяснение результата
        position_size_usd: Размер позиции в USDT (если position_allowed=True)
    """
    position_allowed: bool
    final_risk: float  # % от баланса
    base_risk: float  # % от баланса
    confidence_factor: float
    entropy_factor: float
    portfolio_factor: float
    reason: str
    position_size_usd: Optional[float] = None  # Размер позиции в USDT
    
    def __post_init__(self):
        """Проверка инвариантов"""
        if self.final_risk < 0:
            raise ValueError(f"final_risk {self.final_risk} must be >= 0")
        if not (0.0 <= self.confidence_factor <= 1.0):
            raise ValueError(f"confidence_factor {self.confidence_factor} must be in [0, 1]")
        if not (0.0 <= self.entropy_factor <= 1.0):
            raise ValueError(f"entropy_factor {self.entropy_factor} must be in [0, 1]")
        if not (0.0 <= self.portfolio_factor <= 1.0):
            raise ValueError(f"portfolio_factor {self.portfolio_factor} must be in [0, 1]")


# ========== ОСНОВНОЙ КЛАСС ==========

class PositionSizer:
    """
    Калькулятор размера позиции на основе confidence, entropy и состояния портфеля.
    
    PositionSizer НЕ принимает решения о входе — только размер.
    Если итоговый риск < min_threshold → position_allowed = False.
    
    Логика:
    1. base_risk = config.max_risk_per_trade
    2. confidence_factor = clamp(confidence, 0.2, 1.0)
    3. entropy_factor = clamp(1 - entropy, 0.1, 1.0)
    4. portfolio_factor = portfolio_state.available_risk_ratio()
    5. final_risk = base_risk * confidence_factor * entropy_factor * portfolio_factor
    
    Если final_risk < config.min_risk_threshold:
        position_allowed = False
    Иначе:
        position_allowed = True
    """
    
    def __init__(self, config: Optional[PositionSizingConfig] = None):
        """
        Инициализация PositionSizer.
        
        Args:
            config: Конфигурация (опционально, используется по умолчанию)
        """
        self.config = config or PositionSizingConfig()
    
    def calculate(
        self,
        confidence: float,
        entropy: float,
        portfolio_state: PortfolioStateProtocol,
        symbol: str,
        balance: Optional[float] = None
    ) -> PositionSizingResult:
        """
        Рассчитывает допустимый размер позиции.
        
        Args:
            confidence: Уверенность системы (0.0 - 1.0)
            entropy: Когнитивная неопределённость (0.0 - 1.0)
            portfolio_state: Состояние портфеля (должен реализовывать PortfolioStateProtocol)
            symbol: Торговая пара (для логирования)
            balance: Текущий баланс (опционально, используется INITIAL_BALANCE по умолчанию)
        
        Returns:
            PositionSizingResult: Результат расчёта размера позиции
        
        Примечание:
            Если итоговый риск < min_risk_threshold, position_allowed = False.
        """
        # Нормализуем входные данные
        confidence = max(0.0, min(1.0, confidence))
        entropy = max(0.0, min(1.0, entropy))
        
        if balance is None:
            balance = INITIAL_BALANCE
        
        # ========== БАЗОВЫЙ РИСК ==========
        base_risk = self.config.max_risk_per_trade
        
        # ========== CONFIDENCE FACTOR ==========
        confidence_factor = self._clamp(
            confidence,
            self.config.confidence_min,
            self.config.confidence_max
        )
        
        # ========== ENTROPY FACTOR ==========
        # Высокая entropy = низкая структурированность = меньший размер
        # entropy_factor = 1 - entropy (но с ограничениями)
        entropy_factor = self._clamp(
            1.0 - entropy,
            self.config.entropy_min,
            self.config.entropy_max
        )
        
        # ========== PORTFOLIO FACTOR ==========
        portfolio_factor = portfolio_state.available_risk_ratio()
        portfolio_factor = max(0.0, min(1.0, portfolio_factor))
        
        # ========== ИТОГОВЫЙ РИСК ==========
        final_risk = base_risk * confidence_factor * entropy_factor * portfolio_factor
        
        # ========== ПРОВЕРКА МИНИМАЛЬНОГО ПОРОГА ==========
        if final_risk < self.config.min_risk_threshold:
            return PositionSizingResult(
                position_allowed=False,
                final_risk=final_risk,
                base_risk=base_risk,
                confidence_factor=confidence_factor,
                entropy_factor=entropy_factor,
                portfolio_factor=portfolio_factor,
                reason=f"Risk too small after scaling ({final_risk:.2f}% < {self.config.min_risk_threshold:.2f}%)",
                position_size_usd=None
            )
        
        # ========== РАСЧЁТ РАЗМЕРА ПОЗИЦИИ ==========
        # Размер позиции = (баланс * final_risk) / 100
        position_size_usd = (balance * final_risk) / 100.0
        
        return PositionSizingResult(
            position_allowed=True,
            final_risk=final_risk,
            base_risk=base_risk,
            confidence_factor=confidence_factor,
            entropy_factor=entropy_factor,
            portfolio_factor=portfolio_factor,
            reason=f"Position allowed. Final risk: {final_risk:.2f}% (base: {base_risk:.2f}%, "
                   f"confidence: {confidence_factor:.2f}, entropy: {entropy_factor:.2f}, "
                   f"portfolio: {portfolio_factor:.2f})",
            position_size_usd=position_size_usd
        )
    
    def _clamp(self, value: float, min_val: float, max_val: float) -> float:
        """
        Ограничивает значение в диапазоне [min_val, max_val].
        
        Args:
            value: Значение для ограничения
            min_val: Минимальное значение
            max_val: Максимальное значение
        
        Returns:
            float: Ограниченное значение
        """
        return max(min_val, min(max_val, value))


# ========== АРХИТЕКТУРА ДЛЯ БУДУЩИХ ФАКТОРОВ ==========

class PositionSizingFactor:
    """
    Базовый класс для факторов размера позиции.
    
    Позволяет добавлять новые факторы (regime, volatility) без изменения основного класса.
    """
    
    def calculate_factor(
        self,
        confidence: float,
        entropy: float,
        portfolio_state: PortfolioStateProtocol,
        symbol: str,
        **kwargs
    ) -> float:
        """
        Вычисляет множитель для размера позиции.
        
        Args:
            confidence: Уверенность системы
            entropy: Когнитивная неопределённость
            portfolio_state: Состояние портфеля
            symbol: Торговая пара
            **kwargs: Дополнительные параметры (regime, volatility, и т.д.)
        
        Returns:
            float: Множитель (0.0 - 1.0)
        """
        return 1.0  # По умолчанию не влияет


class RegimeFactor(PositionSizingFactor):
    """
    Фактор размера позиции на основе режима рынка.
    
    Пример: в трендовом режиме можно увеличить размер позиции.
    """
    
    def calculate_factor(
        self,
        confidence: float,
        entropy: float,
        portfolio_state: PortfolioStateProtocol,
        symbol: str,
        market_regime: Optional[object] = None,
        **kwargs
    ) -> float:
        """
        Вычисляет множитель на основе режима рынка.
        
        Args:
            market_regime: Режим рынка (опционально)
            **kwargs: Остальные параметры
        
        Returns:
            float: Множитель (0.0 - 1.0)
        """
        # Реализация для будущего использования
        if market_regime is None:
            return 1.0
        
        # Пример: в трендовом режиме увеличиваем размер на 10%
        # if market_regime.trend_type == "TREND":
        #     return 1.1
        
        return 1.0


class VolatilityFactor(PositionSizingFactor):
    """
    Фактор размера позиции на основе волатильности.
    
    Пример: при высокой волатильности уменьшаем размер позиции.
    """
    
    def calculate_factor(
        self,
        confidence: float,
        entropy: float,
        portfolio_state: PortfolioStateProtocol,
        symbol: str,
        volatility_level: Optional[str] = None,
        **kwargs
    ) -> float:
        """
        Вычисляет множитель на основе волатильности.
        
        Args:
            volatility_level: Уровень волатильности (опционально)
            **kwargs: Остальные параметры
        
        Returns:
            float: Множитель (0.0 - 1.0)
        """
        # Реализация для будущего использования
        if volatility_level is None:
            return 1.0
        
        # Пример: при высокой волатильности уменьшаем размер на 20%
        # if volatility_level == "HIGH":
        #     return 0.8
        
        return 1.0


# ========== АДАПТЕР ДЛЯ PORTFOLIO STATE ==========

class PortfolioStateAdapter:
    """
    Адаптер для преобразования PortfolioState в PortfolioStateProtocol.
    
    Позволяет использовать PortfolioState из portfolio_brain.py с PositionSizer.
    """
    
    def __init__(self, portfolio_state):
        """
        Инициализация адаптера.
        
        Args:
            portfolio_state: Объект PortfolioState из portfolio_brain.py
        """
        self.portfolio_state = portfolio_state
    
    def total_exposure(self) -> float:
        """Возвращает суммарную экспозицию портфеля"""
        return self.portfolio_state.total_exposure
    
    def available_risk_ratio(self) -> float:
        """
        Возвращает доступный риск-коэффициент (0.0 - 1.0).
        
        Вычисляется как: (risk_budget - used_risk) / risk_budget
        """
        if self.portfolio_state.risk_budget == 0:
            return 0.0
        
        available_risk = self.portfolio_state.risk_budget - self.portfolio_state.used_risk
        ratio = available_risk / self.portfolio_state.risk_budget
        
        return max(0.0, min(1.0, ratio))

