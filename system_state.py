"""
Единая модель состояния системы.

Вместо разрозненных переменных и singleton объектов,
все важное состояние хранится здесь и передается явно.
"""
from datetime import datetime, UTC
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field


# Импортируем типы из decision_core для type hints
try:
    from core.decision_core import MarketRegime, RiskExposure, CognitiveState, Opportunity
except ImportError:
    # Fallback для случаев, когда decision_core еще не загружен
    MarketRegime = Any
    RiskExposure = Any
    CognitiveState = Any
    Opportunity = Any


@dataclass
class PerformanceMetrics:
    """Метрики производительности"""
    total_cycles: int = 0
    successful_cycles: int = 0
    errors: int = 0
    last_error: Optional[str] = None


@dataclass
class SystemHealth:
    """Здоровье системы"""
    is_running: bool = True
    safe_mode: bool = False  # Режим безопасности - блокирует торговлю
    trading_paused: bool = False  # Торговля приостановлена (CRITICAL alert)
    last_heartbeat: Optional[datetime] = None
    consecutive_errors: int = 0


class SystemState:
    """
    Единая модель состояния системы.
    
    Хранит все важное состояние в одном месте.
    Передается явно между модулями.
    """
    
    def __init__(self):
        # Состояния от brain'ов (хранятся здесь, а не в brain'ах)
        self.market_regime: Optional[MarketRegime] = None  # От MarketRegimeBrain
        self.risk_state: Optional[RiskExposure] = None  # От RiskExposureBrain
        self.cognitive_state: Optional[CognitiveState] = None  # От CognitiveFilter
        self.opportunities: Dict[str, Opportunity] = {}  # От OpportunityAwareness (по символам)
        
        # Корреляции рынка
        self.market_correlations: Dict = {}
        self.last_analysis_time: Optional[datetime] = None
        
        # Решение Decision Core
        self.can_trade: bool = False
        self.last_decision_time: Optional[datetime] = None
        
        # Открытые позиции (загружаются из БД при необходимости)
        self.open_positions: List[Dict] = []
        
        # Недавние сигналы (последние 50)
        self.recent_signals: List[Dict] = []
        
        # Кэш сигналов (перенесён из state_cache.py)
        # Хранит последнее состояние 15m для каждого символа
        self.signal_cache: Dict[str, str] = {}  # {symbol: last_state_15m}
        
        # Метрики производительности
        self.performance_metrics = PerformanceMetrics()
        
        # Здоровье системы
        self.system_health = SystemHealth()
    
    def update_market_regime(self, regime: MarketRegime):
        """Обновляет режим рынка (вызывается MarketRegimeBrain)"""
        self.market_regime = regime
        self.last_analysis_time = datetime.now(UTC)
    
    def update_risk_state(self, exposure: RiskExposure):
        """Обновляет состояние риска (вызывается RiskExposureBrain)"""
        self.risk_state = exposure
        self.last_analysis_time = datetime.now(UTC)
    
    def update_cognitive_state(self, cognitive: CognitiveState):
        """Обновляет когнитивное состояние (вызывается CognitiveFilter)"""
        self.cognitive_state = cognitive
        self.last_analysis_time = datetime.now(UTC)
    
    def update_opportunity(self, symbol: str, opportunity: Opportunity):
        """Обновляет возможность для символа (вызывается OpportunityAwareness)"""
        self.opportunities[symbol] = opportunity
        self.last_analysis_time = datetime.now(UTC)
    
    def update_market_correlations(self, correlations: Dict):
        """Обновляет корреляции рынка"""
        self.market_correlations = correlations
        self.last_analysis_time = datetime.now(UTC)
    
    def update_trading_decision(self, can_trade: bool):
        """Обновляет решение о торговле (вызывается DecisionCore)"""
        self.can_trade = can_trade
        self.last_decision_time = datetime.now(UTC)
    
    def add_signal(self, signal: Dict):
        """Добавляет новый сигнал"""
        self.recent_signals.append(signal)
        # Храним только последние 50 сигналов
        if len(self.recent_signals) > 50:
            self.recent_signals.pop(0)
    
    def update_signal_cache(self, symbol: str, state_15m: str):
        """
        Обновляет кэш сигналов для символа.
        Используется для предотвращения дублирования сигналов.
        
        Args:
            symbol: Торговая пара
            state_15m: Состояние на 15m таймфрейме
        """
        self.signal_cache[symbol] = state_15m
    
    def is_new_signal(self, symbol: str, state_15m: Optional[str]) -> bool:
        """
        Проверяет, является ли сигнал новым (изменилось ли состояние).
        
        Args:
            symbol: Торговая пара
            state_15m: Состояние на 15m таймфрейме (может быть None)
        
        Returns:
            bool: True если сигнал новый, False если уже был отправлен
        """
        if state_15m is None:
            return False
        
        last_state = self.signal_cache.get(symbol)
        if last_state == state_15m:
            return False
        
        # Состояние изменилось - это новый сигнал
        self.signal_cache[symbol] = state_15m
        return True
    
    def reset_signal_cache(self, symbol: Optional[str] = None):
        """
        Сбрасывает кэш сигналов для указанного символа или для всех символов.
        
        Args:
            symbol: Символ для сброса кэша. Если None, сбрасывает кэш для всех символов.
        """
        if symbol is None:
            self.signal_cache = {}
        else:
            if symbol in self.signal_cache:
                del self.signal_cache[symbol]
    
    def update_open_positions(self, positions: List[Dict]):
        """Обновляет список открытых позиций"""
        self.open_positions = positions
    
    def increment_cycle(self, success: bool = True):
        """Увеличивает счетчик циклов"""
        self.performance_metrics.total_cycles += 1
        if success:
            self.performance_metrics.successful_cycles += 1
        else:
            self.performance_metrics.errors += 1
    
    def record_error(self, error: str):
        """Записывает ошибку"""
        self.performance_metrics.last_error = error
        self.performance_metrics.errors += 1
        self.system_health.consecutive_errors += 1
    
    def reset_errors(self):
        """Сбрасывает счетчик ошибок"""
        self.system_health.consecutive_errors = 0
    
    def update_heartbeat(self):
        """Обновляет время последнего heartbeat"""
        self.system_health.last_heartbeat = datetime.now(UTC)
    
    def reset(self):
        """Сбрасывает состояние (для тестов)"""
        self.__init__()
    
    def create_snapshot(self) -> Dict:
        """
        Создаёт снимок критичных данных для сохранения.
        
        Сохраняет только то, что нужно восстановить после перезапуска:
        - open_positions
        - performance_metrics
        - system_health
        - recent_signals (последние 20)
        - signal_cache
        
        НЕ сохраняет быстро устаревающие данные:
        - market_regime, risk_state, cognitive_state, opportunities, can_trade, market_correlations
        
        Returns:
            dict: Снимок состояния
        """
        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "open_positions": self.open_positions.copy(),
            "performance_metrics": {
                "total_cycles": self.performance_metrics.total_cycles,
                "successful_cycles": self.performance_metrics.successful_cycles,
                "errors": self.performance_metrics.errors,
                "last_error": self.performance_metrics.last_error
            },
            "system_health": {
                "is_running": self.system_health.is_running,
                "safe_mode": self.system_health.safe_mode,
                "trading_paused": self.system_health.trading_paused,
                "last_heartbeat": self.system_health.last_heartbeat.isoformat() if self.system_health.last_heartbeat else None,
                "consecutive_errors": self.system_health.consecutive_errors
            },
            "recent_signals": self.recent_signals[-20:].copy(),  # Только последние 20
            "signal_cache": self.signal_cache.copy()
        }
    
    def restore_from_snapshot(self, snapshot: Dict):
        """
        Восстанавливает состояние из снимка.
        
        Args:
            snapshot: Снимок состояния (из create_snapshot)
        """
        if not snapshot:
            return
        
        try:
            # Восстанавливаем открытые позиции
            if "open_positions" in snapshot:
                self.open_positions = snapshot["open_positions"]
            
            # Восстанавливаем метрики
            if "performance_metrics" in snapshot:
                pm = snapshot["performance_metrics"]
                self.performance_metrics.total_cycles = pm.get("total_cycles", 0)
                self.performance_metrics.successful_cycles = pm.get("successful_cycles", 0)
                self.performance_metrics.errors = pm.get("errors", 0)
                self.performance_metrics.last_error = pm.get("last_error")
            
            # Восстанавливаем здоровье системы
            if "system_health" in snapshot:
                sh = snapshot["system_health"]
                self.system_health.is_running = sh.get("is_running", True)
                self.system_health.safe_mode = sh.get("safe_mode", False)
                self.system_health.trading_paused = sh.get("trading_paused", False)
                if sh.get("last_heartbeat"):
                    try:
                        self.system_health.last_heartbeat = datetime.fromisoformat(sh["last_heartbeat"])
                    except (ValueError, TypeError):
                        pass
                self.system_health.consecutive_errors = sh.get("consecutive_errors", 0)
            
            # Восстанавливаем последние сигналы
            if "recent_signals" in snapshot:
                self.recent_signals = snapshot["recent_signals"]
            
            # Восстанавливаем кэш сигналов
            if "signal_cache" in snapshot:
                self.signal_cache = snapshot["signal_cache"]
        except Exception as e:
            # Если восстановление не удалось, продолжаем с пустым состоянием
            import logging
            logging.getLogger(__name__).warning(f"Ошибка восстановления из snapshot: {e}")
    
    def to_dict(self) -> Dict:
        """Возвращает состояние в виде словаря (для логирования)"""
        return {
            "market_regime": {
                "has_regime": self.market_regime is not None,
                "trend_type": self.market_regime.trend_type if self.market_regime else None,
                "volatility": self.market_regime.volatility_level if self.market_regime else None
            },
            "risk_state": {
                "has_exposure": self.risk_state is not None,
                "total_risk_pct": self.risk_state.total_risk_pct if self.risk_state else None,
                "active_positions": self.risk_state.active_positions if self.risk_state else 0
            },
            "cognitive_state": {
                "has_state": self.cognitive_state is not None,
                "should_pause": self.cognitive_state.should_pause if self.cognitive_state else False
            },
            "opportunities": len(self.opportunities),
            "can_trade": self.can_trade,
            "last_analysis": self.last_analysis_time.isoformat() if self.last_analysis_time else None,
            "open_positions": len(self.open_positions),
            "recent_signals": len(self.recent_signals),
            "performance": {
                "total_cycles": self.performance_metrics.total_cycles,
                "successful_cycles": self.performance_metrics.successful_cycles,
                "errors": self.performance_metrics.errors
            },
            "health": {
                "is_running": self.system_health.is_running,
                "consecutive_errors": self.system_health.consecutive_errors,
                "last_heartbeat": self.system_health.last_heartbeat.isoformat() if self.system_health.last_heartbeat else None
            }
        }


# Глобальный экземпляр SystemState (создается в runner.py)
_system_state_instance: Optional['SystemState'] = None


def get_system_state() -> Optional['SystemState']:
    """
    Возвращает глобальный экземпляр SystemState.
    
    ИСПОЛЬЗОВАНИЕ: Только для чтения (Telegram команды).
    НЕ ИСПОЛЬЗУЙТЕ для изменения состояния!
    
    ВАЖНО: Всегда проверяйте на None:
        system_state = get_system_state()
        if system_state is None:
            # Безопасный fallback
            return
    
    Returns:
        SystemState или None если не инициализирован
    """
    return _system_state_instance


def set_system_state(state: 'SystemState'):
    """
    Устанавливает глобальный экземпляр SystemState.
    
    ИНВАРИАНТ: Вызывается ТОЛЬКО из runner.py при старте.
    Не вызывайте из других мест!
    
    Args:
        state: Экземпляр SystemState
    """
    global _system_state_instance
    _system_state_instance = state

