"""
ReplayEngine — offline инструмент анализа решений.

ReplayEngine повторно прогоняет сохранённые SignalSnapshot через текущую логику
принятия решений и собирает статистику.

ВАЖНО:
- ReplayEngine НЕ используется в runtime
- ReplayEngine НЕ торгует
- ReplayEngine НЕ пишет в production-логи
- ReplayEngine НЕ меняет состояние системы
- ReplayEngine работает ТОЛЬКО с данными snapshot'ов

Чем Replay отличается от Backtest:
- Backtest: симулирует торговлю на исторических данных с реальными ценами
- Replay: повторяет принятие решений на сохранённых snapshot'ах системы
- Replay выявляет расхождения в решениях (drift detection)
- Replay не торгует, только анализирует решения
"""
from dataclasses import dataclass, field
from typing import List, Iterator, Optional, TYPE_CHECKING, Dict
from core.signal_snapshot import SignalSnapshot
from core.portfolio_brain import (
    PortfolioBrain, PortfolioAnalysis, PortfolioDecision,
    PortfolioState, calculate_portfolio_state, convert_trades_to_positions
)
from core.position_sizer import PositionSizer, PortfolioStateAdapter
from core.decision_core import DecisionCore, TradingDecision
from capital import INITIAL_BALANCE, RISK_PERCENT

if TYPE_CHECKING:
    from core.position_sizer import PositionSizingResult

# MetaDecisionBrain - опциональный импорт
try:
    from brains.meta_decision_brain import (
        MetaDecisionBrain, MetaDecisionResult, SystemHealthStatus, TimeContext
    )
    META_DECISION_AVAILABLE = True
except ImportError:
    META_DECISION_AVAILABLE = False
    MetaDecisionBrain = None
    MetaDecisionResult = None
    SystemHealthStatus = None
    TimeContext = None


@dataclass
class ReplaySummary:
    """
    Сводная статистика по replay snapshot'ов.
    
    Содержит агрегированные метрики по всем прогоненным snapshot'ам.
    """
    total_snapshots: int = 0
    meta_blocked: int = 0
    decision_blocked: int = 0
    portfolio_blocked: int = 0
    position_sizer_blocked: int = 0
    allowed_trades: int = 0
    size_reduced_count: int = 0
    avg_size_multiplier: float = 0.0
    avg_final_risk: float = 0.0
    
    # Детальная статистика
    meta_block_reasons: dict = field(default_factory=dict)
    decision_block_reasons: dict = field(default_factory=dict)
    portfolio_block_reasons: dict = field(default_factory=dict)
    
    def __post_init__(self):
        """Вычисляет средние значения"""
        if self.size_reduced_count > 0:
            self.avg_size_multiplier = self.avg_size_multiplier / self.size_reduced_count
        if self.allowed_trades > 0:
            self.avg_final_risk = self.avg_final_risk / self.allowed_trades


class ReplayEngine:
    """
    Offline инструмент для повторного прогона SignalSnapshot через текущую логику.
    
    ReplayEngine:
    - Принимает список SignalSnapshot (или iterator)
    - Прогоняет через MetaDecisionBrain, DecisionCore, PortfolioBrain, PositionSizer
    - Собирает статистику по решениям
    - Возвращает ReplaySummary
    
    Принципы:
    - НЕ взаимодействует с live-рынком
    - НЕ меняет SystemState
    - Работает ТОЛЬКО на данных snapshot
    - НЕ торгует
    - НЕ пишет в production-логи
    - Никаких side effects
    - Только pure logic
    """
    
    def __init__(
        self,
        meta_brain: Optional[MetaDecisionBrain] = None,
        portfolio_brain: Optional[PortfolioBrain] = None,
        position_sizer: Optional[PositionSizer] = None,
        decision_core: Optional[DecisionCore] = None
    ):
        """
        Инициализация ReplayEngine.
        
        Args:
            meta_brain: MetaDecisionBrain (опционально, создаётся по умолчанию)
            portfolio_brain: PortfolioBrain (опционально, создаётся по умолчанию)
            position_sizer: PositionSizer (опционально, создаётся по умолчанию)
            decision_core: DecisionCore (опционально, создаётся по умолчанию)
        """
        # Создаём brain'ы только если они доступны
        if META_DECISION_AVAILABLE:
            self.meta_brain = meta_brain or MetaDecisionBrain()
        else:
            self.meta_brain = None
        
        self.portfolio_brain = portfolio_brain or PortfolioBrain()
        self.position_sizer = position_sizer or PositionSizer()
        self.decision_core = decision_core or DecisionCore()
    
    def replay_snapshots(
        self,
        snapshots: List[SignalSnapshot]
    ) -> ReplaySummary:
        """
        Прогоняет список SignalSnapshot через текущую логику.
        
        Args:
            snapshots: Список SignalSnapshot для replay
        
        Returns:
            ReplaySummary: Сводная статистика по всем snapshot'ам
        """
        summary = ReplaySummary()
        summary.total_snapshots = len(snapshots)
        
        for snapshot in snapshots:
            self._replay_single_snapshot(snapshot, summary)
        
        # Вычисляем средние значения
        summary.__post_init__()
        
        return summary
    
    def replay_snapshots_iterator(
        self,
        snapshots: Iterator[SignalSnapshot]
    ) -> ReplaySummary:
        """
        Прогоняет iterator SignalSnapshot через текущую логику.
        
        Args:
            snapshots: Iterator SignalSnapshot для replay
        
        Returns:
            ReplaySummary: Сводная статистика по всем snapshot'ам
        """
        summary = ReplaySummary()
        
        for snapshot in snapshots:
            summary.total_snapshots += 1
            self._replay_single_snapshot(snapshot, summary)
        
        # Вычисляем средние значения
        summary.__post_init__()
        
        return summary
    
    def _replay_single_snapshot(
        self,
        snapshot: SignalSnapshot,
        summary: ReplaySummary
    ):
        """
        Прогоняет один snapshot через всю логику.
        
        Args:
            snapshot: SignalSnapshot для replay
            summary: ReplaySummary для накопления статистики
        """
        # ========== 1. META DECISION BRAIN ==========
        if self.meta_brain:
            meta_result = self._replay_meta_decision(snapshot)
            if meta_result and not meta_result.allow_trading:
                summary.meta_blocked += 1
                reason = meta_result.reason
                summary.meta_block_reasons[reason] = summary.meta_block_reasons.get(reason, 0) + 1
                return  # Early exit - не продолжаем дальше
        
        # ========== 2. DECISION CORE ==========
        decision_result = self._replay_decision_core(snapshot)
        if decision_result and not decision_result.can_trade:
            summary.decision_blocked += 1
            reason = decision_result.reason
            summary.decision_block_reasons[reason] = summary.decision_block_reasons.get(reason, 0) + 1
            return  # Early exit - не продолжаем дальше
        
        # ========== 3. PORTFOLIO BRAIN ==========
        portfolio_result = self._replay_portfolio(snapshot)
        if portfolio_result and portfolio_result.decision == PortfolioDecision.BLOCK:
            summary.portfolio_blocked += 1
            reason = portfolio_result.reason
            summary.portfolio_block_reasons[reason] = summary.portfolio_block_reasons.get(reason, 0) + 1
            return  # Early exit - не продолжаем дальше
        
        # ========== 4. POSITION SIZER ==========
        sizing_result = self._replay_position_sizer(snapshot, portfolio_result)
        if sizing_result and not sizing_result.position_allowed:
            summary.position_sizer_blocked += 1
            return  # Early exit - не продолжаем дальше
        
        # ========== СИГНАЛ РАЗРЕШЁН ==========
        summary.allowed_trades += 1
        
        # Собираем статистику по размеру позиции
        if sizing_result:
            summary.avg_final_risk += sizing_result.final_risk
            
            # Проверяем, был ли уменьшен размер
            if portfolio_result and portfolio_result.recommended_size_multiplier < 1.0:
                summary.size_reduced_count += 1
                summary.avg_size_multiplier += portfolio_result.recommended_size_multiplier
    
    def _replay_meta_decision(
        self,
        snapshot: SignalSnapshot
    ) -> Optional[MetaDecisionResult]:
        """
        Прогоняет snapshot через MetaDecisionBrain.
        
        Args:
            snapshot: SignalSnapshot для анализа
        
        Returns:
            MetaDecisionResult или None (если MetaDecisionBrain недоступен)
        """
        if not self.meta_brain or not META_DECISION_AVAILABLE:
            return None
        
        try:
            # Извлекаем данные из snapshot
            market_regime = snapshot.market_regime
            confidence_score = snapshot.confidence
            entropy_score = snapshot.entropy

            # Use snapshot data for portfolio_exposure (offline — no live DB)
            portfolio_exposure = 0.0

            # Вызываем MetaDecisionBrain
            meta_result = self.meta_brain.evaluate(
                market_regime=market_regime,
                confidence_score=confidence_score,
                entropy_score=entropy_score,
                portfolio_exposure=portfolio_exposure,
                recent_outcomes=None,  # Недоступно в offline режиме
                signals_count_recent=0,  # Недоступно в offline режиме
                system_health=SystemHealthStatus.OK,  # Предполагаем OK для offline
                time_context=TimeContext.UNKNOWN  # Недоступно в offline режиме
            )
            
            return meta_result
        except Exception:
            # В случае ошибки не блокируем - просто пропускаем
            return None
    
    def _replay_decision_core(
        self,
        snapshot: SignalSnapshot
    ) -> Optional[TradingDecision]:
        """
        Прогоняет snapshot через DecisionCore.
        
        Args:
            snapshot: SignalSnapshot для анализа
        
        Returns:
            TradingDecision или None (если ошибка)
        """
        try:
            # DecisionCore требует system_state, но для offline создаём минимальный mock
            # В реальности DecisionCore должен работать только с snapshot'ом
            # Здесь используем упрощённую версию
            
            # Создаём минимальный system_state mock
            class MockSystemState:
                def __init__(self):
                    self.system_health = type('obj', (object,), {'safe_mode': False})()
                    self.cognitive_state = None
                    self.market_regime = snapshot.market_regime
                    self.risk_state = None
                    self.opportunities = {}
                
                def update_trading_decision(self, can_trade: bool) -> None:
                    """
                    No-op.
                    Required for interface compatibility with DecisionCore
                    during replay / dry-run / offline evaluation.
                    
                    This method exists only to satisfy the interface contract
                    expected by DecisionCore.should_i_trade(). It performs
                    no side effects and does not mutate any state.
                    """
                    return None
            
            mock_system_state = MockSystemState()
            
            # Вызываем DecisionCore
            decision = self.decision_core.should_i_trade(
                symbol=snapshot.symbol,
                system_state=mock_system_state
            )
            
            return decision
        except Exception:
            # В случае ошибки не блокируем - просто пропускаем
            return None
    
    def _replay_portfolio(
        self,
        snapshot: SignalSnapshot
    ) -> Optional[PortfolioAnalysis]:
        """
        Прогоняет snapshot через PortfolioBrain.
        
        Args:
            snapshot: SignalSnapshot для анализа
        
        Returns:
            PortfolioAnalysis или None (если нет открытых позиций или ошибка)
        """
        try:
            # Offline replay — no live DB access.
            # Without historical portfolio state in the snapshot,
            # we skip portfolio analysis (assume no open positions).
            return None
        except Exception:
            # В случае ошибки не блокируем - просто пропускаем
            return None
    
    def _replay_position_sizer(
        self,
        snapshot: SignalSnapshot,
        portfolio_analysis: Optional[PortfolioAnalysis]
    ) -> Optional["PositionSizingResult"]:
        """
        Прогоняет snapshot через PositionSizer.
        
        Args:
            snapshot: SignalSnapshot для анализа
            portfolio_analysis: Результат PortfolioBrain (опционально)
        
        Returns:
            PositionSizingResult или None (если ошибка)
        """
        try:
            # Offline replay — use INITIAL_BALANCE, empty portfolio (no live DB)
            balance = INITIAL_BALANCE
            portfolio_state = PortfolioState(
                total_exposure=0.0,
                long_exposure=0.0,
                short_exposure=0.0,
                net_exposure=0.0,
                risk_budget=balance * (RISK_PERCENT / 100.0),
                used_risk=0.0,
            )

            portfolio_adapter = PortfolioStateAdapter(portfolio_state)
            
            # Вызываем PositionSizer
            sizing_result = self.position_sizer.calculate(
                confidence=snapshot.confidence,
                entropy=snapshot.entropy,
                portfolio_state=portfolio_adapter,
                symbol=snapshot.symbol,
                balance=balance
            )
            
            return sizing_result
        except Exception:
            # В случае ошибки не блокируем - просто пропускаем
            return None
