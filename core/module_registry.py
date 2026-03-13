"""
ModuleRegistry - Реестр модулей с классификацией критичности

ЦЕЛЬ: Централизованное управление модулями и их критичностью.
"""
import logging
from typing import Dict, Optional, Callable, List, Any
from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import Enum

logger = logging.getLogger(__name__)


class ModuleCriticality(str, Enum):
    """Критичность модуля"""
    CRITICAL = "CRITICAL"      # Недоступность → SAFE_HALT
    NON_CRITICAL = "NON_CRITICAL"  # Недоступность → DEGRADED (graceful degradation)


@dataclass
class ModuleHealth:
    """Здоровье модуля"""
    available: bool
    valid: bool
    last_heartbeat: Optional[datetime] = None
    error: Optional[str] = None


@dataclass
class ModuleInfo:
    """Информация о модуле"""
    name: str
    criticality: ModuleCriticality
    timeout_seconds: float
    description: str
    get_instance: Callable[[], Any]  # Функция для получения экземпляра модуля
    registered_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class ModuleRegistry:
    """
    Реестр модулей системы.
    
    Управляет классификацией модулей и их критичностью.
    """
    
    def __init__(self):
        self._modules: Dict[str, ModuleInfo] = {}
        self._default_criticality = ModuleCriticality.NON_CRITICAL
        self._default_timeout = 5.0
    
    def register_module(
        self,
        name: str,
        criticality: ModuleCriticality,
        get_instance: Callable[[], Any],
        timeout_seconds: Optional[float] = None,
        description: str = ""
    ):
        """
        Регистрирует модуль.
        
        Args:
            name: Имя модуля
            criticality: Критичность модуля
            get_instance: Функция для получения экземпляра модуля
            timeout_seconds: Таймаут для операций модуля (секунды)
            description: Описание модуля
        """
        if name in self._modules:
            logger.warning(f"Module {name} already registered, overwriting")
        
        self._modules[name] = ModuleInfo(
            name=name,
            criticality=criticality,
            timeout_seconds=timeout_seconds or self._default_timeout,
            description=description,
            get_instance=get_instance
        )
        
        logger.info(
            f"Module registered: {name} (criticality: {criticality.value}, "
            f"timeout: {timeout_seconds or self._default_timeout}s)"
        )
    
    def get_module(self, name: str) -> Optional[ModuleInfo]:
        """Получить информацию о модуле"""
        return self._modules.get(name)
    
    def list_modules(self) -> List[str]:
        """Список всех зарегистрированных модулей"""
        return list(self._modules.keys())
    
    def get_critical_modules(self) -> List[str]:
        """Список всех CRITICAL модулей"""
        return [
            name for name, info in self._modules.items()
            if info.criticality == ModuleCriticality.CRITICAL
        ]
    
    def get_non_critical_modules(self) -> List[str]:
        """Список всех NON_CRITICAL модулей"""
        return [
            name for name, info in self._modules.items()
            if info.criticality == ModuleCriticality.NON_CRITICAL
        ]
    
    def is_critical(self, name: str) -> bool:
        """Проверяет, является ли модуль критическим"""
        module_info = self.get_module(name)
        return module_info is not None and module_info.criticality == ModuleCriticality.CRITICAL
    
    def set_criticality(self, name: str, criticality: ModuleCriticality):
        """Изменяет критичность модуля"""
        module_info = self.get_module(name)
        if not module_info:
            raise ValueError(f"Module {name} not registered")
        
        old_criticality = module_info.criticality
        module_info.criticality = criticality
        
        logger.info(
            f"Module {name} criticality changed: {old_criticality.value} → {criticality.value}"
        )
    
    def unregister_module(self, name: str):
        """Удаляет модуль из реестра"""
        if name in self._modules:
            del self._modules[name]
            logger.info(f"Module {name} unregistered")


# Глобальный экземпляр
_module_registry: Optional[ModuleRegistry] = None


def get_module_registry() -> ModuleRegistry:
    """Получить глобальный экземпляр ModuleRegistry"""
    global _module_registry
    if _module_registry is None:
        _module_registry = ModuleRegistry()
        # Регистрируем стандартные модули
        _register_standard_modules(_module_registry)
    return _module_registry


def set_module_registry(registry: ModuleRegistry):
    """Установить глобальный экземпляр (для тестов)"""
    global _module_registry
    _module_registry = registry


def _register_standard_modules(registry: ModuleRegistry):
    """Регистрирует стандартные модули системы"""
    
    # CRITICAL модули
    from core.decision_core import get_decision_core
    registry.register_module(
        name="DecisionCore",
        criticality=ModuleCriticality.CRITICAL,
        get_instance=get_decision_core,
        timeout_seconds=5.0,
        description="Единая точка принятия торговых решений"
    )
    
    from system_state_machine import get_state_machine
    registry.register_module(
        name="SystemStateMachine",
        criticality=ModuleCriticality.CRITICAL,
        get_instance=get_state_machine,
        timeout_seconds=0.1,  # Синхронные операции
        description="Управление системными состояниями"
    )
    
    # NON_CRITICAL модули (участвуют в decision flow, но недоступность не блокирует торговлю)
    try:
        from brains.meta_decision_brain import MetaDecisionBrain
        # MetaDecisionBrain: NON_CRITICAL по умолчанию
        # Участвует в decision flow (первый фильтр), но недоступность не блокирует торговлю
        # Может быть изменён на CRITICAL через конфигурацию
        registry.register_module(
            name="MetaDecisionBrain",
            criticality=ModuleCriticality.NON_CRITICAL,
            get_instance=lambda: MetaDecisionBrain(),
            timeout_seconds=3.0,
            description="Мета-решения о торговле (WHEN NOT TO TRADE) - участвует в decision flow"
        )
    except ImportError:
        logger.warning("MetaDecisionBrain not available")
    
    try:
        from brains.risk_exposure_brain import get_risk_exposure_brain
        registry.register_module(
            name="RiskExposureBrain",
            criticality=ModuleCriticality.CRITICAL,  # Риск критичен
            get_instance=get_risk_exposure_brain,
            timeout_seconds=3.0,
            description="Расчёт риска и экспозиции"
        )
    except ImportError:
        logger.warning("RiskExposureBrain not available")
    
    try:
        from brains.market_regime_brain import get_market_regime_brain
        registry.register_module(
            name="MarketRegimeBrain",
            criticality=ModuleCriticality.NON_CRITICAL,
            get_instance=get_market_regime_brain,
            timeout_seconds=5.0,
            description="Анализ режима рынка"
        )
    except ImportError:
        logger.warning("MarketRegimeBrain not available")
    
    try:
        from brains.cognitive_filter import get_cognitive_filter
        registry.register_module(
            name="CognitiveFilter",
            criticality=ModuleCriticality.NON_CRITICAL,
            get_instance=get_cognitive_filter,
            timeout_seconds=3.0,
            description="Фильтр когнитивных ошибок"
        )
    except ImportError:
        logger.warning("CognitiveFilter not available")
    
    try:
        from brains.opportunity_awareness import get_opportunity_awareness
        registry.register_module(
            name="OpportunityAwareness",
            criticality=ModuleCriticality.NON_CRITICAL,
            get_instance=get_opportunity_awareness,
            timeout_seconds=5.0,
            description="Отслеживание возможностей рынка"
        )
    except ImportError:
        logger.warning("OpportunityAwareness not available")
    
    try:
        from execution.gatekeeper import get_gatekeeper
        registry.register_module(
            name="Gatekeeper",
            criticality=ModuleCriticality.CRITICAL,  # Gatekeeper критичен
            get_instance=get_gatekeeper,
            timeout_seconds=5.0,
            description="Проверка сигналов перед отправкой"
        )
    except ImportError:
        logger.warning("Gatekeeper not available")
    
    try:
        from core.portfolio_brain import get_portfolio_brain
        registry.register_module(
            name="PortfolioBrain",
            criticality=ModuleCriticality.NON_CRITICAL,
            get_instance=get_portfolio_brain,
            timeout_seconds=5.0,
            description="Портфельный анализ - участвует в decision flow"
        )
    except ImportError:
        logger.warning("PortfolioBrain not available")
    
    try:
        from core.position_sizer import PositionSizer
        registry.register_module(
            name="PositionSizer",
            criticality=ModuleCriticality.NON_CRITICAL,
            get_instance=lambda: PositionSizer(),
            timeout_seconds=3.0,
            description="Расчёт размера позиции - участвует в decision flow"
        )
    except ImportError:
        logger.warning("PositionSizer not available")

