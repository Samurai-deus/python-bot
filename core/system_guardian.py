"""
SystemGuardian - Глобальный слой принуждения инвариантов и политик

ЦЕЛЬ: Гарантировать, что система НЕ МОЖЕТ работать в аналитически небезопасном состоянии.

ПРИНЦИПЫ:
- Fail-safe по умолчанию
- Принудительное выполнение всех инвариантов
- Мониторинг здоровья всех модулей
- Финальная проверка перед торговлей
"""
import logging
from typing import Dict, List, Optional, Any, Set
from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import Enum

from system_state_machine import SystemState, get_state_machine
from core.module_registry import ModuleRegistry, ModuleCriticality, ModuleHealth, get_module_registry

logger = logging.getLogger(__name__)


class AsyncToSyncAdapter:
    """
    Тонкий адаптер для вызова async функций из синхронного контекста.
    
    Инкапсулирует логику адаптации к event loop, делая SystemGuardian
    execution-agnostic (независимым от runtime контекста).
    
    Поведение:
    - Если event loop запущен → использует run_coroutine_threadsafe
    - Если event loop не запущен → возвращает fail-safe результат (не создаёт новый loop)
    - Fail-safe: любой сбой → возвращает блокирующий результат
    """
    
    @staticmethod
    def call_async(coro, timeout: float = 5.0, fail_safe_result=None):
        """
        Вызывает async корутину из синхронного контекста.
        
        CRITICAL: Never creates nested event loops.
        If no event loop is running, returns fail-safe result instead of creating a new loop.
        This prevents "RuntimeError: This event loop is already running".
        
        Args:
            coro: Async корутина для выполнения
            timeout: Таймаут в секундах
            fail_safe_result: Результат при сбое (для fail-safe)
        
        Returns:
            Результат выполнения корутины или fail_safe_result при сбое
        """
        import asyncio
        
        try:
            # Определяем контекст: запущен ли event loop
            try:
                loop = asyncio.get_running_loop()
                # Контекст: event loop запущен → используем thread-safe вызов
                future = asyncio.run_coroutine_threadsafe(coro, loop)
                return future.result(timeout=timeout)
            except RuntimeError:
                # CRITICAL: get_running_loop() failed - could mean:
                # 1. No loop running (safe case, but we can't create one from sync context)
                # 2. Loop running in different thread (unsafe to create new loop)
                # 
                # NEVER use asyncio.run() here - it will fail if we're in async context
                # Instead, return fail-safe result to prevent nested loop creation
                logger.error(
                    "AsyncToSyncAdapter: No running event loop detected. "
                    "Cannot safely execute async operation from sync context. "
                    "Returning fail-safe result to prevent nested event loop creation."
                )
                return fail_safe_result
        except (asyncio.TimeoutError, TimeoutError):
            # Timeout → возвращаем fail-safe результат
            logger.error(f"AsyncToSyncAdapter timeout (operation: {coro.__name__ if hasattr(coro, '__name__') else 'unknown'})")
            return fail_safe_result
        except Exception as e:
            # Любой другой сбой → возвращаем fail-safe результат
            logger.error(
                f"AsyncToSyncAdapter failed: {type(e).__name__}: {e}",
                exc_info=True
            )
            return fail_safe_result


class InvariantViolationSeverity(str, Enum):
    """Серьёзность нарушения инварианта"""
    CRITICAL = "CRITICAL"  # Немедленный SAFE_MODE
    WARNING = "WARNING"    # Логирование, но продолжение работы
    INFO = "INFO"          # Информационное сообщение


@dataclass
class InvariantViolation:
    """Нарушение инварианта"""
    invariant_id: str  # INV-1, INV-2, etc.
    severity: InvariantViolationSeverity
    message: str
    module: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TradingPermission:
    """Разрешение на торговлю"""
    allowed: bool
    reason: str
    blocked_by: Optional[str] = None  # Модуль или инвариант, который заблокировал
    violations: List[InvariantViolation] = field(default_factory=list)


class ModuleHealthMonitor:
    """Мониторинг здоровья модулей"""
    
    def __init__(self, module_registry: ModuleRegistry):
        self.module_registry = module_registry
        self._health_cache: Dict[str, ModuleHealth] = {}
        self._last_check: Dict[str, datetime] = {}
    
    async def check_module_health(self, module_name: str) -> ModuleHealth:
        """
        Проверяет здоровье модуля.
        
        Args:
            module_name: Имя модуля
        
        Returns:
            ModuleHealth: Состояние здоровья модуля
        """
        module_info = self.module_registry.get_module(module_name)
        if not module_info:
            return ModuleHealth(
                available=False,
                valid=False,
                last_heartbeat=None,
                error="Module not registered"
            )
        
        # Проверяем доступность
        available = await self._check_availability(module_info)
        
        # Проверяем валидность данных (если модуль доступен)
        valid = True
        if available:
            valid = await self._check_data_validity(module_info)
        
        # Получаем последний heartbeat
        last_heartbeat = await self._get_last_heartbeat(module_info)
        
        health = ModuleHealth(
            available=available,
            valid=valid,
            last_heartbeat=last_heartbeat,
            error=None if available and valid else "Module unavailable or invalid"
        )
        
        # Кэшируем результат
        self._health_cache[module_name] = health
        self._last_check[module_name] = datetime.now(UTC)
        
        return health
    
    async def _check_availability(self, module_info) -> bool:
        """Проверяет доступность модуля"""
        try:
            # Пытаемся получить экземпляр модуля
            module_instance = module_info.get_instance()
            if module_instance is None:
                return False
            
            # Проверяем, что модуль отвечает (если есть метод health_check)
            if hasattr(module_instance, 'health_check'):
                try:
                    result = await asyncio.wait_for(
                        module_instance.health_check(),
                        timeout=module_info.timeout_seconds
                    )
                    return result is True
                except asyncio.TimeoutError:
                    logger.warning(f"Module {module_info.name} health check timeout")
                    return False
                except Exception as e:
                    logger.warning(f"Module {module_info.name} health check failed: {e}")
                    return False
            
            # Если метода health_check нет, считаем доступным если экземпляр существует
            return True
        except Exception as e:
            logger.error(f"Error checking availability of {module_info.name}: {e}")
            return False
    
    async def _check_data_validity(self, module_info) -> bool:
        """Проверяет валидность данных модуля"""
        # Базовая проверка - можно расширить
        try:
            module_instance = module_info.get_instance()
            if module_instance is None:
                return False
            
            # Если есть метод validate_data, вызываем его
            if hasattr(module_instance, 'validate_data'):
                try:
                    result = await asyncio.wait_for(
                        module_instance.validate_data(),
                        timeout=module_info.timeout_seconds
                    )
                    return result is True
                except Exception:
                    return False
            
            # По умолчанию считаем валидным если модуль доступен
            return True
        except Exception:
            return False
    
    async def _get_last_heartbeat(self, module_info) -> Optional[datetime]:
        """Получает время последнего heartbeat модуля"""
        # Базовая реализация - можно расширить
        # Модули могут реализовать метод get_last_heartbeat()
        try:
            module_instance = module_info.get_instance()
            if module_instance and hasattr(module_instance, 'get_last_heartbeat'):
                return module_instance.get_last_heartbeat()
        except Exception:
            pass
        return None
    
    async def check_all_modules(self) -> Dict[str, ModuleHealth]:
        """Проверяет здоровье всех зарегистрированных модулей"""
        all_modules = self.module_registry.list_modules()
        health_status = {}
        
        for module_name in all_modules:
            health_status[module_name] = await self.check_module_health(module_name)
        
        return health_status


class InvariantEnforcer:
    """Принуждение всех инвариантов"""
    
    def __init__(self, module_registry: ModuleRegistry, state_machine):
        self.module_registry = module_registry
        self.state_machine = state_machine
    
    async def check_all_invariants(self) -> List[InvariantViolation]:
        """
        Проверяет все инварианты.
        
        Returns:
            List[InvariantViolation]: Список нарушений
        """
        violations = []
        
        # INV-1: CRITICAL MODULE AVAILABILITY
        violations.extend(await self._check_invariant_1())
        
        # INV-2: DECISION CORE AUTHORITY
        violations.extend(await self._check_invariant_2())
        
        # INV-3: META DECISION BRAIN CRITICALITY
        violations.extend(await self._check_invariant_3())
        
        # INV-4: STATE MACHINE CONSISTENCY
        violations.extend(await self._check_invariant_4())
        
        # INV-5: DATA VALIDITY
        violations.extend(await self._check_invariant_5())
        
        # INV-6: TIMEOUT ENFORCEMENT
        violations.extend(await self._check_invariant_6())
        
        return violations
    
    async def _check_invariant_1(self) -> List[InvariantViolation]:
        """INV-1: CRITICAL MODULE AVAILABILITY"""
        violations = []
        
        critical_modules = self.module_registry.get_critical_modules()
        for module_name in critical_modules:
            module_info = self.module_registry.get_module(module_name)
            if not module_info:
                violations.append(InvariantViolation(
                    invariant_id="INV-1",
                    severity=InvariantViolationSeverity.CRITICAL,
                    message=f"CRITICAL module {module_name} not registered",
                    module=module_name
                ))
                continue
            
            # Проверяем доступность
            module_instance = module_info.get_instance()
            if module_instance is None:
                violations.append(InvariantViolation(
                    invariant_id="INV-1",
                    severity=InvariantViolationSeverity.CRITICAL,
                    message=f"CRITICAL module {module_name} unavailable (instance is None)",
                    module=module_name
                ))
        
        return violations
    
    async def _check_invariant_2(self) -> List[InvariantViolation]:
        """INV-2: DECISION CORE AUTHORITY"""
        violations = []
        
        # Проверяем, что DecisionCore зарегистрирован и доступен
        decision_core_info = self.module_registry.get_module("DecisionCore")
        if not decision_core_info:
            violations.append(InvariantViolation(
                invariant_id="INV-2",
                severity=InvariantViolationSeverity.CRITICAL,
                message="DecisionCore not registered",
                module="DecisionCore"
            ))
        elif decision_core_info.get_instance() is None:
            violations.append(InvariantViolation(
                invariant_id="INV-2",
                severity=InvariantViolationSeverity.CRITICAL,
                message="DecisionCore unavailable",
                module="DecisionCore"
            ))
        
        return violations
    
    async def _check_invariant_3(self) -> List[InvariantViolation]:
        """INV-3: META DECISION BRAIN CRITICALITY"""
        violations = []
        
        meta_brain_info = self.module_registry.get_module("MetaDecisionBrain")
        if meta_brain_info and meta_brain_info.criticality == ModuleCriticality.CRITICAL:
            # Если MetaDecisionBrain помечен как CRITICAL, он должен быть доступен
            if meta_brain_info.get_instance() is None:
                violations.append(InvariantViolation(
                    invariant_id="INV-3",
                    severity=InvariantViolationSeverity.CRITICAL,
                    message="MetaDecisionBrain is CRITICAL but unavailable",
                    module="MetaDecisionBrain"
                ))
        
        return violations
    
    async def _check_invariant_4(self) -> List[InvariantViolation]:
        """INV-4: STATE MACHINE CONSISTENCY"""
        violations = []
        
        current_state = self.state_machine.state
        
        # Проверяем, что состояние валидно
        if current_state not in [SystemState.RUNNING, SystemState.DEGRADED, 
                                 SystemState.SAFE_MODE, SystemState.RECOVERING, SystemState.FATAL]:
            violations.append(InvariantViolation(
                invariant_id="INV-4",
                severity=InvariantViolationSeverity.CRITICAL,
                message=f"Invalid system state: {current_state}",
                metadata={"state": current_state.value}
            ))
        
        # Проверяем консистентность: SAFE_MODE или FATAL → trading_paused == True
        if current_state in [SystemState.SAFE_MODE, SystemState.FATAL]:
            if not self.state_machine.trading_paused:
                violations.append(InvariantViolation(
                    invariant_id="INV-4",
                    severity=InvariantViolationSeverity.CRITICAL,
                    message=f"State {current_state} requires trading_paused=True, but it's False",
                    metadata={"state": current_state.value}
                ))
        
        return violations
    
    async def _check_invariant_5(self) -> List[InvariantViolation]:
        """INV-5: DATA VALIDITY"""
        violations = []
        
        # Базовая проверка - можно расширить для конкретных модулей
        # Здесь проверяем, что критичные данные не None и не NaN
        
        critical_modules = self.module_registry.get_critical_modules()
        for module_name in critical_modules:
            module_info = self.module_registry.get_module(module_name)
            if not module_info:
                continue
            
            # Если модуль имеет метод validate_data, вызываем его
            module_instance = module_info.get_instance()
            if module_instance and hasattr(module_instance, 'validate_data'):
                try:
                    is_valid = await asyncio.wait_for(
                        module_instance.validate_data(),
                        timeout=module_info.timeout_seconds
                    )
                    if not is_valid:
                        violations.append(InvariantViolation(
                            invariant_id="INV-5",
                            severity=InvariantViolationSeverity.CRITICAL,
                            message=f"CRITICAL module {module_name} has invalid data",
                            module=module_name
                        ))
                except asyncio.TimeoutError:
                    violations.append(InvariantViolation(
                        invariant_id="INV-5",
                        severity=InvariantViolationSeverity.CRITICAL,
                        message=f"CRITICAL module {module_name} data validation timeout",
                        module=module_name
                    ))
                except Exception as e:
                    violations.append(InvariantViolation(
                        invariant_id="INV-5",
                        severity=InvariantViolationSeverity.CRITICAL,
                        message=f"CRITICAL module {module_name} data validation error: {e}",
                        module=module_name
                    ))
        
        return violations
    
    async def _check_invariant_6(self) -> List[InvariantViolation]:
        """INV-6: TIMEOUT ENFORCEMENT"""
        violations = []
        
        # Проверяем, что все модули отвечают в пределах таймаута
        # Это проверяется через ModuleHealthMonitor
        # Здесь только логируем предупреждения
        
        return violations


class PolicyEnforcer:
    """Принуждение политик fail-safe"""
    
    def __init__(self, module_registry: ModuleRegistry, state_machine):
        self.module_registry = module_registry
        self.state_machine = state_machine
    
    async def enforce_fail_safe_policy(
        self,
        module_name: str,
        failure_type: str,
        failure_details: Dict[str, Any]
    ) -> bool:
        """
        Применяет fail-safe политику при отказе модуля.
        
        Args:
            module_name: Имя модуля
            failure_type: Тип отказа ("unavailable", "invalid", "timeout")
            failure_details: Детали отказа
        
        Returns:
            bool: True если политика применена (торговля заблокирована)
        """
        module_info = self.module_registry.get_module(module_name)
        if not module_info:
            logger.error(f"Module {module_name} not found in registry")
            return False
        
        # RULE-1: CRITICAL MODULE FAILURE → SAFE_MODE
        if module_info.criticality == ModuleCriticality.CRITICAL:
            logger.critical(
                f"CRITICAL module {module_name} failure ({failure_type}): {failure_details}. "
                f"Transitioning to SAFE_MODE."
            )
            
            await self.state_machine.transition_to(
                SystemState.SAFE_MODE,
                f"CRITICAL module {module_name} failure: {failure_type}",
                owner="PolicyEnforcer",
                metadata={
                    "module": module_name,
                    "failure_type": failure_type,
                    **failure_details
                }
            )
            return True
        
        # RULE-2: NON_CRITICAL MODULE FAILURE → DEGRADED (если не в SAFE_MODE/FATAL)
        elif module_info.criticality == ModuleCriticality.NON_CRITICAL:
            current_state = self.state_machine.state
            if current_state == SystemState.RUNNING:
                logger.warning(
                    f"NON_CRITICAL module {module_name} failure ({failure_type}): {failure_details}. "
                    f"Transitioning to DEGRADED."
                )
                
                await self.state_machine.transition_to(
                    SystemState.DEGRADED,
                    f"NON_CRITICAL module {module_name} failure: {failure_type}",
                    owner="PolicyEnforcer",
                    metadata={
                        "module": module_name,
                        "failure_type": failure_type,
                        **failure_details
                    }
                )
                return False  # Торговля не заблокирована, но система деградирована
        
        return False


class TradingGate:
    """Финальная проверка перед торговлей"""
    
    def __init__(
        self,
        module_registry: ModuleRegistry,
        state_machine,
        invariant_enforcer: InvariantEnforcer,
        health_monitor: ModuleHealthMonitor
    ):
        self.module_registry = module_registry
        self.state_machine = state_machine
        self.invariant_enforcer = invariant_enforcer
        self.health_monitor = health_monitor
    
    async def can_trade(self) -> TradingPermission:
        """
        Финальная проверка перед торговлей.
        
        Returns:
            TradingPermission: Разрешение на торговлю
        """
        violations = []
        
        # 1. Проверка системного состояния
        current_state = self.state_machine.state
        if current_state != SystemState.RUNNING:
            return TradingPermission(
                allowed=False,
                reason=f"System state is {current_state.value}, trading only allowed in RUNNING state",
                blocked_by="SystemStateMachine",
                violations=[]
            )
        
        # 2. Проверка всех инвариантов
        invariant_violations = await self.invariant_enforcer.check_all_invariants()
        critical_violations = [
            v for v in invariant_violations
            if v.severity == InvariantViolationSeverity.CRITICAL
        ]
        
        if critical_violations:
            return TradingPermission(
                allowed=False,
                reason=f"Critical invariant violations detected: {len(critical_violations)}",
                blocked_by="InvariantEnforcer",
                violations=critical_violations
            )
        
        # 3. Проверка здоровья всех CRITICAL модулей
        critical_modules = self.module_registry.get_critical_modules()
        for module_name in critical_modules:
            health = await self.health_monitor.check_module_health(module_name)
            if not health.available or not health.valid:
                violations.append(InvariantViolation(
                    invariant_id="INV-1",
                    severity=InvariantViolationSeverity.CRITICAL,
                    message=f"CRITICAL module {module_name} unavailable or invalid",
                    module=module_name,
                    metadata={
                        "available": health.available,
                        "valid": health.valid,
                        "error": health.error
                    }
                ))
        
        if violations:
            return TradingPermission(
                allowed=False,
                reason=f"CRITICAL modules unavailable or invalid: {len(violations)}",
                blocked_by="ModuleHealthMonitor",
                violations=violations
            )
        
        # 4. Все проверки пройдены
        return TradingPermission(
            allowed=True,
            reason="All checks passed",
            blocked_by=None,
            violations=[]
        )


class SystemGuardian:
    """
    Глобальный слой принуждения инвариантов и политик.
    
    Обеспечивает fail-safe поведение системы.
    """
    
    def __init__(
        self,
        module_registry: Optional[ModuleRegistry] = None,
        state_machine = None
    ):
        self.module_registry = module_registry or get_module_registry()
        self.state_machine = state_machine or get_state_machine()
        
        # Инициализируем компоненты
        self.health_monitor = ModuleHealthMonitor(self.module_registry)
        self.invariant_enforcer = InvariantEnforcer(self.module_registry, self.state_machine)
        self.policy_enforcer = PolicyEnforcer(self.module_registry, self.state_machine)
        self.trading_gate = TradingGate(
            self.module_registry,
            self.state_machine,
            self.invariant_enforcer,
            self.health_monitor
        )
    
    async def check_all_invariants(self) -> List[InvariantViolation]:
        """Проверяет все инварианты"""
        return await self.invariant_enforcer.check_all_invariants()
    
    async def check_module_health(self) -> Dict[str, ModuleHealth]:
        """Проверяет здоровье всех модулей"""
        return await self.health_monitor.check_all_modules()
    
    async def can_trade(self) -> TradingPermission:
        """Проверяет, можно ли торговать (async)"""
        return await self.trading_gate.can_trade()
    
    def can_trade_sync(self) -> TradingPermission:
        """
        Синхронная проверка, можно ли торговать.
        
        АРХИТЕКТУРНЫЙ КОНТРАКТ:
        - Единственный способ проверки разрешения на торговлю из синхронного контекста
        - Все async операции инкапсулированы через AsyncToSyncAdapter
        - Вызывающий код (Gatekeeper) не должен знать об async деталях
        - SystemGuardian execution-agnostic (независим от runtime контекста)
        
        Returns:
            TradingPermission: Разрешение на торговлю
        
        Fail-safe: Любой сбой (exception, timeout) → блокировка торговли
        """
        # Fail-safe результат при сбое
        fail_safe_permission = TradingPermission(
            allowed=False,
            reason="SystemGuardian error - fail-safe block",
            blocked_by="SystemGuardian",
            violations=[]
        )
        
        # Используем адаптер для вызова async метода
        permission = AsyncToSyncAdapter.call_async(
            self.trading_gate.can_trade(),
            timeout=5.0,
            fail_safe_result=fail_safe_permission
        )
        
        return permission
    
    async def handle_violations(self, violations: List[InvariantViolation]):
        """
        Обрабатывает нарушения инвариантов.
        
        Args:
            violations: Список нарушений
        """
        critical_violations = [
            v for v in violations
            if v.severity == InvariantViolationSeverity.CRITICAL
        ]
        
        if critical_violations:
            # Критические нарушения → SAFE_MODE
            logger.critical(
                f"CRITICAL invariant violations detected: {len(critical_violations)}. "
                f"Transitioning to SAFE_MODE."
            )
            
            for violation in critical_violations:
                logger.critical(
                    f"INVARIANT VIOLATION: {violation.invariant_id} - {violation.message} "
                    f"(module: {violation.module})"
                )
            
            await self.state_machine.transition_to(
                SystemState.SAFE_MODE,
                f"CRITICAL invariant violations: {len(critical_violations)}",
                owner="SystemGuardian",
                metadata={
                    "violations_count": len(critical_violations),
                    "violations": [
                        {
                            "invariant_id": v.invariant_id,
                            "message": v.message,
                            "module": v.module
                        }
                        for v in critical_violations
                    ]
                }
            )
        else:
            # Предупреждения только логируются
            for violation in violations:
                logger.warning(
                    f"INVARIANT WARNING: {violation.invariant_id} - {violation.message}"
                )


# Глобальный экземпляр
_system_guardian: Optional[SystemGuardian] = None


def get_system_guardian() -> SystemGuardian:
    """Получить глобальный экземпляр SystemGuardian"""
    global _system_guardian
    if _system_guardian is None:
        _system_guardian = SystemGuardian()
    return _system_guardian


def set_system_guardian(guardian: SystemGuardian):
    """Установить глобальный экземпляр (для тестов)"""
    global _system_guardian
    _system_guardian = guardian

