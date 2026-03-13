"""
Portfolio Brain - портфельный анализ сигналов.

Анализирует систему как целое, а не отдельный сигнал.
Отвечает на вопрос: "Улучшает ли ЭТОТ сигнал ПОРТФЕЛЬ?"

PortfolioBrain НЕ анализирует рынок.
Он анализирует систему как целое.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum
from core.signal_snapshot import SignalSnapshot, SignalDecision
from core.market_state import MarketState


class PositionDirection(str, Enum):
    """Направление позиции"""
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True)
class PositionSnapshot:
    """
    Immutable snapshot одной открытой позиции.
    
    Используется для портфельного анализа.
    """
    symbol: str
    direction: PositionDirection
    size: float  # Размер позиции в USDT
    entry_price: float
    unrealized_pnl: float
    market_state: Optional[MarketState]  # MarketState на момент входа
    confidence: float  # Confidence сигнала на момент входа
    entropy: float  # Entropy сигнала на момент входа
    
    def __post_init__(self):
        """Проверка инвариантов"""
        if self.size <= 0:
            raise ValueError(f"Position size {self.size} must be > 0")
        if self.entry_price <= 0:
            raise ValueError(f"Entry price {self.entry_price} must be > 0")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"Confidence {self.confidence} must be in range [0, 1]")
        if not (0.0 <= self.entropy <= 1.0):
            raise ValueError(f"Entropy {self.entropy} must be in range [0, 1]")


@dataclass(frozen=True)
class PortfolioState:
    """
    Immutable snapshot состояния портфеля.
    
    Содержит агрегированные метрики портфеля.
    """
    total_exposure: float  # Суммарная экспозиция в USDT
    long_exposure: float  # Экспозиция LONG в USDT
    short_exposure: float  # Экспозиция SHORT в USDT
    net_exposure: float  # Чистая экспозиция (long - short)
    
    risk_budget: float  # Доступный риск-бюджет в USDT
    used_risk: float  # Использованный риск в USDT
    
    regime_exposure: Dict[MarketState, float] = field(default_factory=dict)  # Экспозиция по MarketState
    symbol_exposure: Dict[str, float] = field(default_factory=dict)  # Экспозиция по символам
    
    def __post_init__(self):
        """Проверка инвариантов"""
        if self.total_exposure < 0:
            raise ValueError(f"Total exposure {self.total_exposure} must be >= 0")
        if self.long_exposure < 0:
            raise ValueError(f"Long exposure {self.long_exposure} must be >= 0")
        if self.short_exposure < 0:
            raise ValueError(f"Short exposure {self.short_exposure} must be >= 0")
        if self.risk_budget < 0:
            raise ValueError(f"Risk budget {self.risk_budget} must be >= 0")
        if self.used_risk < 0:
            raise ValueError(f"Used risk {self.used_risk} must be >= 0")


class PortfolioDecision(str, Enum):
    """Решение портфельного анализа"""
    ALLOW = "ALLOW"  # Разрешить сигнал
    REDUCE = "REDUCE"  # Разрешить с уменьшенным размером
    BLOCK = "BLOCK"  # Заблокировать сигнал
    SCALE_DOWN = "SCALE_DOWN"  # Уменьшить размер из-за перегрузки


@dataclass
class PortfolioAnalysis:
    """Результат анализа портфеля"""
    decision: PortfolioDecision
    reason: str
    recommended_size_multiplier: float = 1.0  # Множитель для размера позиции (0.0 - 1.0)
    
    # Агрегированные метрики
    portfolio_entropy: float = 0.0
    dominant_market_state: Optional[MarketState] = None
    exposure_by_state: Dict[MarketState, float] = field(default_factory=dict)
    exposure_by_direction: Dict[str, float] = field(default_factory=dict)
    average_confidence: float = 0.0
    risk_utilization_ratio: float = 0.0  # used_risk / risk_budget


class PortfolioBrain:
    """
    Портфельный анализ сигналов.
    
    Анализирует систему как целое, а не отдельный сигнал.
    Отвечает на вопрос: "Улучшает ли ЭТОТ сигнал ПОРТФЕЛЬ?"
    
    PortfolioBrain НЕ анализирует рынок.
    Он анализирует систему как целое.
    """
    
    def __init__(self):
        """Инициализация Portfolio Brain (без состояния)"""
        pass
    
    def evaluate(
        self,
        snapshot: SignalSnapshot,
        open_positions: List[PositionSnapshot],
        portfolio_state: PortfolioState,
    ) -> PortfolioAnalysis:
        """
        Оценивает сигнал с точки зрения портфеля.
        
        Args:
            snapshot: SignalSnapshot для оценки
            open_positions: Список открытых позиций
            portfolio_state: Состояние портфеля
        
        Returns:
            PortfolioAnalysis: Результат анализа
        """
        # Вычисляем агрегированные метрики
        portfolio_entropy = self._calculate_portfolio_entropy(open_positions)
        dominant_market_state = self._find_dominant_market_state(open_positions)
        exposure_by_state = self._calculate_exposure_by_state(open_positions)
        exposure_by_direction = self._calculate_exposure_by_direction(open_positions)
        average_confidence = self._calculate_average_confidence(open_positions)
        risk_utilization_ratio = (
            portfolio_state.used_risk / portfolio_state.risk_budget
            if portfolio_state.risk_budget > 0
            else 0.0
        )
        
        # ========== БЛОКИРУЮЩИЕ УСЛОВИЯ (HARD) ==========
        block_reason = self._check_blocking_conditions(
            snapshot, portfolio_state, portfolio_entropy, dominant_market_state, exposure_by_state
        )
        if block_reason:
            return PortfolioAnalysis(
                decision=PortfolioDecision.BLOCK,
                reason=block_reason,
                recommended_size_multiplier=0.0,
                portfolio_entropy=portfolio_entropy,
                dominant_market_state=dominant_market_state,
                exposure_by_state=exposure_by_state,
                exposure_by_direction=exposure_by_direction,
                average_confidence=average_confidence,
                risk_utilization_ratio=risk_utilization_ratio
            )
        
        # ========== УМЕНЬШЕНИЕ (SOFT BLOCK) ==========
        scale_down_reason, scale_multiplier = self._check_scale_down_conditions(
            snapshot, portfolio_state, open_positions, average_confidence, portfolio_entropy
        )
        if scale_down_reason:
            return PortfolioAnalysis(
                decision=PortfolioDecision.SCALE_DOWN,
                reason=scale_down_reason,
                recommended_size_multiplier=scale_multiplier,
                portfolio_entropy=portfolio_entropy,
                dominant_market_state=dominant_market_state,
                exposure_by_state=exposure_by_state,
                exposure_by_direction=exposure_by_direction,
                average_confidence=average_confidence,
                risk_utilization_ratio=risk_utilization_ratio
            )
        
        # ========== РАЗРЕШЕНИЕ ==========
        allow_reason = self._check_allow_conditions(
            snapshot, portfolio_state, open_positions, average_confidence, portfolio_entropy, exposure_by_state
        )
        if allow_reason:
            return PortfolioAnalysis(
                decision=PortfolioDecision.ALLOW,
                reason=allow_reason,
                recommended_size_multiplier=1.0,
                portfolio_entropy=portfolio_entropy,
                dominant_market_state=dominant_market_state,
                exposure_by_state=exposure_by_state,
                exposure_by_direction=exposure_by_direction,
                average_confidence=average_confidence,
                risk_utilization_ratio=risk_utilization_ratio
            )
        
        # ========== REDUCE (стратегически полезен, но портфель перегружен) ==========
        reduce_reason = self._check_reduce_conditions(snapshot, portfolio_state, average_confidence)
        if reduce_reason:
            return PortfolioAnalysis(
                decision=PortfolioDecision.REDUCE,
                reason=reduce_reason,
                recommended_size_multiplier=0.3,  # Минимальный размер
                portfolio_entropy=portfolio_entropy,
                dominant_market_state=dominant_market_state,
                exposure_by_state=exposure_by_state,
                exposure_by_direction=exposure_by_direction,
                average_confidence=average_confidence,
                risk_utilization_ratio=risk_utilization_ratio
            )
        
        # По умолчанию - разрешить
        return PortfolioAnalysis(
            decision=PortfolioDecision.ALLOW,
            reason="No portfolio constraints",
            recommended_size_multiplier=1.0,
            portfolio_entropy=portfolio_entropy,
            dominant_market_state=dominant_market_state,
            exposure_by_state=exposure_by_state,
            exposure_by_direction=exposure_by_direction,
            average_confidence=average_confidence,
            risk_utilization_ratio=risk_utilization_ratio
        )
    
    # ========== БЛОКИРУЮЩИЕ УСЛОВИЯ ==========
    
    def _check_blocking_conditions(
        self,
        snapshot: SignalSnapshot,
        portfolio_state: PortfolioState,
        portfolio_entropy: float,
        dominant_market_state: Optional[MarketState],
        exposure_by_state: Dict[MarketState, float]
    ) -> Optional[str]:
        """
        Проверяет блокирующие условия (HARD).
        
        Если ЛЮБОЕ выполнено → BLOCK.
        
        Returns:
            str: Причина блокировки или None
        """
        # 1. total_exposure > risk_budget
        if portfolio_state.total_exposure > portfolio_state.risk_budget:
            return f"Total exposure ({portfolio_state.total_exposure:.2f}) exceeds risk budget ({portfolio_state.risk_budget:.2f})"
        
        # 2. entropy портфеля > 0.75
        if portfolio_entropy > 0.75:
            return f"Portfolio entropy ({portfolio_entropy:.2f}) too high (>0.75)"
        
        # 3. 60% портфеля в одном MarketState
        if dominant_market_state and portfolio_state.total_exposure > 0:
            dominant_exposure_pct = (
                exposure_by_state.get(dominant_market_state, 0.0) / portfolio_state.total_exposure * 100
            )
            if dominant_exposure_pct > 60.0:
                # Проверяем, усиливает ли новый сигнал доминирующее состояние
                signal_state = snapshot.states.get(snapshot.timeframe_anchor)
                if signal_state == dominant_market_state:
                    return f"Portfolio overexposed to {dominant_market_state.value} ({dominant_exposure_pct:.1f}%), signal would reinforce"
        
        # 4. новый сигнал усиливает уже доминирующее направление
        if dominant_market_state:
            signal_state = snapshot.states.get(snapshot.timeframe_anchor)
            if signal_state == dominant_market_state:
                # Проверяем, не перегружен ли портфель этим состоянием
                if portfolio_state.total_exposure > 0:
                    state_exposure_pct = (
                        exposure_by_state.get(dominant_market_state, 0.0) / portfolio_state.total_exposure * 100
                    )
                    if state_exposure_pct > 50.0:  # Уже больше 50%
                        return f"Signal reinforces dominant state {dominant_market_state.value} ({state_exposure_pct:.1f}% exposure)"
        
        # 5. confidence нового сигнала < 0.4
        if snapshot.confidence < 0.4:
            return f"Signal confidence ({snapshot.confidence:.2f}) too low (<0.4)"
        
        return None
    
    # ========== УМЕНЬШЕНИЕ (SOFT BLOCK) ==========
    
    def _check_scale_down_conditions(
        self,
        snapshot: SignalSnapshot,
        portfolio_state: PortfolioState,
        open_positions: List[PositionSnapshot],
        average_confidence: float,
        portfolio_entropy: float
    ) -> tuple[Optional[str], float]:
        """
        Проверяет условия для уменьшения размера (SCALE_DOWN).
        
        Returns:
            tuple: (причина, множитель размера) или (None, 1.0)
        """
        multiplier = 1.0
        
        # 1. Высокая корреляция сигнала с портфелем
        correlation_penalty = self._calculate_portfolio_correlation(snapshot, open_positions)
        if correlation_penalty > 0.7:
            multiplier *= 0.5
            return (f"High correlation with portfolio ({correlation_penalty:.2f})", multiplier)
        
        # 2. Усиливает уже перегруженный режим
        if portfolio_state.total_exposure > 0:
            signal_state = snapshot.states.get(snapshot.timeframe_anchor)
            if signal_state:
                # Проверяем, не перегружен ли этот режим
                state_exposure = sum(
                    pos.size for pos in open_positions
                    if pos.market_state == signal_state
                )
                state_exposure_pct = (state_exposure / portfolio_state.total_exposure) * 100
                if state_exposure_pct > 40.0:
                    multiplier *= 0.6
                    return (f"Reinforces overloaded state {signal_state.value} ({state_exposure_pct:.1f}%)", multiplier)
        
        # 3. confidence < средний confidence портфеля
        if average_confidence > 0 and snapshot.confidence < average_confidence * 0.8:
            multiplier *= 0.7
            return (
                f"Signal confidence ({snapshot.confidence:.2f}) below portfolio average ({average_confidence:.2f})",
                multiplier
            )
        
        return (None, 1.0)
    
    # ========== РАЗРЕШЕНИЕ ==========
    
    def _check_allow_conditions(
        self,
        snapshot: SignalSnapshot,
        portfolio_state: PortfolioState,
        open_positions: List[PositionSnapshot],
        average_confidence: float,
        portfolio_entropy: float,
        exposure_by_state: Dict[MarketState, float]
    ) -> Optional[str]:
        """
        Проверяет условия для разрешения сигнала (ALLOW).
        
        Returns:
            str: Причина разрешения или None
        """
        # 1. Сигнал диверсифицирует режимы
        signal_state = snapshot.states.get(snapshot.timeframe_anchor)
        if signal_state and portfolio_state.total_exposure > 0:
            state_exposure = exposure_by_state.get(signal_state, 0.0)
            state_exposure_pct = (state_exposure / portfolio_state.total_exposure) * 100
            if state_exposure_pct < 20.0:  # Меньше 20% в этом состоянии
                return f"Diversifies portfolio (only {state_exposure_pct:.1f}% in {signal_state.value})"
        
        # 2. Снижает net_exposure
        # Определяем направление сигнала (упрощённо - по decision и states)
        if snapshot.decision == SignalDecision.ENTER:
            # Если портфель перегружен в одну сторону, сигнал в другую сторону - хорошо
            if abs(portfolio_state.net_exposure) > portfolio_state.total_exposure * 0.3:
                # Сигнал может снизить net exposure (упрощённая проверка)
                return "Signal may reduce net exposure"
        
        # 3. confidence > median(confidence портфеля)
        if average_confidence > 0 and snapshot.confidence > average_confidence * 1.2:
            return f"Signal confidence ({snapshot.confidence:.2f}) above portfolio average ({average_confidence:.2f})"
        
        # 4. entropy сигнала < entropy портфеля
        if portfolio_entropy > 0 and snapshot.entropy < portfolio_entropy * 0.8:
            return f"Signal entropy ({snapshot.entropy:.2f}) lower than portfolio ({portfolio_entropy:.2f})"
        
        return None
    
    # ========== REDUCE ==========
    
    def _check_reduce_conditions(
        self,
        snapshot: SignalSnapshot,
        portfolio_state: PortfolioState,
        average_confidence: float
    ) -> Optional[str]:
        """
        Проверяет условия для REDUCE (стратегически полезен, но портфель перегружен).
        
        Returns:
            str: Причина или None
        """
        # Портфель перегружен, но сигнал стратегически полезен
        if portfolio_state.total_exposure > portfolio_state.risk_budget * 0.8:
            # Сигнал имеет высокий confidence или низкую entropy
            if snapshot.confidence > 0.7 or snapshot.entropy < 0.3:
                return "Portfolio overloaded but signal strategically valuable"
        
        return None
    
    # ========== АГРЕГИРОВАННЫЕ МЕТРИКИ ==========
    
    def _calculate_portfolio_entropy(self, open_positions: List[PositionSnapshot]) -> float:
        """Вычисляет энтропию портфеля"""
        if not open_positions:
            return 0.0
        
        # Средняя энтропия позиций (взвешенная по размеру)
        total_size = sum(pos.size for pos in open_positions)
        if total_size == 0:
            return 0.0
        
        weighted_entropy = sum(pos.entropy * pos.size for pos in open_positions) / total_size
        return weighted_entropy
    
    def _find_dominant_market_state(
        self, open_positions: List[PositionSnapshot]
    ) -> Optional[MarketState]:
        """Находит доминирующее MarketState в портфеле"""
        if not open_positions:
            return None
        
        # Подсчитываем экспозицию по состояниям
        state_exposure: Dict[MarketState, float] = {}
        for pos in open_positions:
            if pos.market_state:
                state_exposure[pos.market_state] = state_exposure.get(pos.market_state, 0.0) + pos.size
        
        if not state_exposure:
            return None
        
        # Находим состояние с максимальной экспозицией
        dominant_state = max(state_exposure.items(), key=lambda x: x[1])[0]
        return dominant_state
    
    def _calculate_exposure_by_state(
        self, open_positions: List[PositionSnapshot]
    ) -> Dict[MarketState, float]:
        """Вычисляет экспозицию по MarketState"""
        exposure: Dict[MarketState, float] = {}
        for pos in open_positions:
            if pos.market_state:
                exposure[pos.market_state] = exposure.get(pos.market_state, 0.0) + pos.size
        return exposure
    
    def _calculate_exposure_by_direction(
        self, open_positions: List[PositionSnapshot]
    ) -> Dict[str, float]:
        """Вычисляет экспозицию по направлению"""
        exposure = {"LONG": 0.0, "SHORT": 0.0}
        for pos in open_positions:
            exposure[pos.direction.value] = exposure.get(pos.direction.value, 0.0) + pos.size
        return exposure
    
    def _calculate_average_confidence(self, open_positions: List[PositionSnapshot]) -> float:
        """Вычисляет среднюю confidence портфеля (взвешенную по размеру)"""
        if not open_positions:
            return 0.0
        
        total_size = sum(pos.size for pos in open_positions)
        if total_size == 0:
            return 0.0
        
        weighted_confidence = sum(pos.confidence * pos.size for pos in open_positions) / total_size
        return weighted_confidence
    
    def _calculate_portfolio_correlation(
        self, snapshot: SignalSnapshot, open_positions: List[PositionSnapshot]
    ) -> float:
        """
        Вычисляет корреляцию сигнала с портфелем.
        
        Упрощённая метрика: насколько сигнал похож на существующие позиции.
        """
        if not open_positions:
            return 0.0
        
        # Проверяем совпадение по символу
        same_symbol = sum(1 for pos in open_positions if pos.symbol == snapshot.symbol)
        if same_symbol > 0:
            return 0.9  # Высокая корреляция - тот же символ
        
        # Проверяем совпадение по MarketState
        signal_state = snapshot.states.get(snapshot.timeframe_anchor)
        if signal_state:
            same_state = sum(
                1 for pos in open_positions
                if pos.market_state == signal_state
            )
            if same_state > len(open_positions) * 0.5:
                return 0.7  # Средняя корреляция - много позиций в том же состоянии
        
        return 0.3  # Низкая корреляция


def get_portfolio_brain() -> PortfolioBrain:
    """Singleton для Portfolio Brain"""
    global _portfolio_brain
    if '_portfolio_brain' not in globals():
        _portfolio_brain = PortfolioBrain()
    return _portfolio_brain


# ========== HELPER ФУНКЦИИ ДЛЯ ПРЕОБРАЗОВАНИЯ ДАННЫХ ==========

def convert_trades_to_positions(open_trades: List[Dict], current_prices: Optional[Dict[str, float]] = None) -> List[PositionSnapshot]:
    """
    Преобразует открытые сделки из БД в PositionSnapshot.
    
    Args:
        open_trades: Список открытых сделок из БД
        current_prices: Словарь текущих цен {symbol: price} для расчёта unrealized_pnl
    
    Returns:
        List[PositionSnapshot]: Список PositionSnapshot
    """
    positions = []
    
    for trade in open_trades:
        symbol = trade.get("symbol")
        side = trade.get("side", "LONG")
        entry_price = trade.get("entry", 0.0)
        position_size = trade.get("position_size", 0.0)
        
        # Вычисляем unrealized_pnl
        unrealized_pnl = 0.0
        if current_prices and symbol in current_prices:
            current_price = current_prices[symbol]
            if side == "LONG":
                unrealized_pnl = (current_price - entry_price) * (position_size / entry_price)
            else:  # SHORT
                unrealized_pnl = (entry_price - current_price) * (position_size / entry_price)
        
        # Преобразуем side в PositionDirection
        direction = PositionDirection.LONG if side == "LONG" else PositionDirection.SHORT
        
        # MarketState, confidence, entropy извлекаем из snapshot (если есть)
        # Для legacy сделок используем значения по умолчанию
        market_state = None  # Не сохранялось в старых сделках
        confidence = 0.5  # Среднее значение по умолчанию
        entropy = 0.5  # Среднее значение по умолчанию
        
        # TODO: В будущем можно сохранять snapshot_id в trades и извлекать оттуда
        
        position = PositionSnapshot(
            symbol=symbol,
            direction=direction,
            size=position_size,
            entry_price=entry_price,
            unrealized_pnl=unrealized_pnl,
            market_state=market_state,
            confidence=confidence,
            entropy=entropy
        )
        positions.append(position)
    
    return positions


def calculate_portfolio_state(
    open_positions: List[PositionSnapshot],
    risk_budget: float,
    initial_balance: float = 10000.0
) -> PortfolioState:
    """
    Вычисляет PortfolioState на основе открытых позиций.
    
    Args:
        open_positions: Список открытых позиций
        risk_budget: Доступный риск-бюджет в USDT
        initial_balance: Начальный баланс для расчёта экспозиции в %
    
    Returns:
        PortfolioState: Состояние портфеля
    """
    # Вычисляем экспозицию
    total_exposure = sum(pos.size for pos in open_positions)
    long_exposure = sum(pos.size for pos in open_positions if pos.direction == PositionDirection.LONG)
    short_exposure = sum(pos.size for pos in open_positions if pos.direction == PositionDirection.SHORT)
    net_exposure = long_exposure - short_exposure
    
    # Вычисляем использованный риск (упрощённо - сумма размеров позиций)
    used_risk = total_exposure
    
    # Вычисляем экспозицию по MarketState
    regime_exposure: Dict[MarketState, float] = {}
    for pos in open_positions:
        if pos.market_state:
            regime_exposure[pos.market_state] = regime_exposure.get(pos.market_state, 0.0) + pos.size
    
    # Вычисляем экспозицию по символам
    symbol_exposure: Dict[str, float] = {}
    for pos in open_positions:
        symbol_exposure[pos.symbol] = symbol_exposure.get(pos.symbol, 0.0) + pos.size
    
    return PortfolioState(
        total_exposure=total_exposure,
        long_exposure=long_exposure,
        short_exposure=short_exposure,
        net_exposure=net_exposure,
        risk_budget=risk_budget,
        used_risk=used_risk,
        regime_exposure=regime_exposure,
        symbol_exposure=symbol_exposure
    )

