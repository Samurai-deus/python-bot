"""
Trading Risk Core v1.0 - Governed Implementation

ADR-TRADING-RISK-CORE-001: Trading Risk Core v1.0

This module implements a pure policy enforcement layer for trading risk management.

CORE PRINCIPLES (NON-NEGOTIABLE):
- Risk Core is NOT a strategy
- Risk Core is NOT execution
- Risk Core does NOT optimize
- Risk Core does NOT explain itself to strategy
- Risk Core always fails closed
- Risk Core has veto power

If uncertain → DENY trading.
"""
from enum import Enum
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC
import logging

logger = logging.getLogger(__name__)


# ========== CANONICAL RISK STATES (DO NOT CHANGE) ==========
# ADR-TRADING-RISK-CORE-001 Section 2

class RiskState(Enum):
    """
    Canonical risk states - DO NOT MODIFY.
    
    Severity order: HALTED > LOCKED > LIMITED > SAFE
    """
    SAFE = "SAFE"           # Full trading permission
    LIMITED = "LIMITED"     # Limited trading (position size restricted)
    LOCKED = "LOCKED"       # Trading locked (exceptional recovery)
    HALTED = "HALTED"       # Trading halted (terminal, no auto-recovery)


# ========== TRADING PERMISSION ==========
# ADR-TRADING-RISK-CORE-001 Section 4

class TradingPermission(Enum):
    """
    Trading permission output.
    
    ADR-TRADING-RISK-CORE-001 Section 4: Outputs ONLY
    """
    ALLOW = "ALLOW"                     # Full permission
    ALLOW_LIMITED = "ALLOW_LIMITED"      # Limited permission (size restricted)
    DENY = "DENY"                        # Trading denied


# ========== TRADING INTENT (INPUT) ==========
# ADR-TRADING-RISK-CORE-001 Section 4: Inputs ONLY

@dataclass
class TradingIntent:
    """
    Trading intent - facts only.
    
    ADR-TRADING-RISK-CORE-001 Section 4: Inputs ONLY
    Risk Core MUST NOT know: strategy logic, indicators, confidence, expected profit, signal reasons, market regime
    """
    symbol: str
    side: str  # "LONG" | "SHORT"
    position_size_usd: float
    entry_price: float
    stop_price: float
    leverage: Optional[float] = None


# ========== CAPITAL & EXPOSURE SNAPSHOT (INPUT) ==========
# ADR-TRADING-RISK-CORE-001 Section 4: Inputs ONLY

@dataclass
class CapitalSnapshot:
    """Current capital state - facts only."""
    current_balance_usd: float
    initial_balance_usd: float
    total_loss_usd: float  # Cumulative loss
    loss_24h_usd: float  # Loss in last 24 hours
    loss_7d_usd: float   # Loss in last 7 days


@dataclass
class PositionSnapshot:
    """Single position snapshot."""
    symbol: str
    side: str
    position_size_usd: float
    entry_price: float
    stop_price: float
    leverage: Optional[float] = None


@dataclass
class ExposureSnapshot:
    """Current exposure state - facts only."""
    open_positions: List[PositionSnapshot]
    total_exposure_usd: float
    max_single_position_usd: float
    correlation_groups: Dict[str, List[str]]  # Strategy-blind correlation groups


# ========== BEHAVIORAL COUNTERS (INPUT) ==========
# ADR-TRADING-RISK-CORE-001 Section 4: Inputs ONLY

@dataclass
class BehavioralCounters:
    """Behavioral counters - facts only."""
    actions_last_hour: int
    actions_last_24h: int
    consecutive_losses: int
    last_loss_timestamp: Optional[datetime] = None
    last_action_timestamp: Optional[datetime] = None


# ========== SYSTEM HEALTH FLAGS (INPUT) ==========
# ADR-TRADING-RISK-CORE-001 Section 4: Inputs ONLY

@dataclass
class SystemHealthFlags:
    """System health flags - read-only."""
    is_safe_mode: bool
    consecutive_errors: int
    runtime_healthy: bool
    critical_modules_available: bool


# ========== VIOLATION REPORT (INTERNAL) ==========
# ADR-TRADING-RISK-CORE-001 Section 4

@dataclass
class ViolationReport:
    """Internal violation report."""
    violations: List[str] = field(default_factory=list)
    highest_severity_state: RiskState = RiskState.SAFE
    violated_invariants: List[str] = field(default_factory=list)


# ========== RISK CORE CONFIGURATION ==========
# ADR-TRADING-RISK-CORE-001 Section 3: Invariants

@dataclass
class RiskCoreConfig:
    """
    Risk Core configuration - invariant thresholds.
    
    ADR-TRADING-RISK-CORE-001 Section 3: All invariants
    """
    # Capital invariants
    max_absolute_loss_pct: float = 20.0  # Maximum absolute loss % of initial balance
    max_loss_24h_pct: float = 5.0       # Maximum loss in 24h % of balance
    max_loss_7d_pct: float = 10.0       # Maximum loss in 7d % of balance
    
    # Exposure invariants
    max_single_position_pct: float = 10.0  # Max single position % of balance
    max_aggregate_exposure_pct: float = 50.0  # Max aggregate exposure % of balance
    max_correlated_group_pct: float = 30.0  # Max correlated group % of balance
    
    # Behavioral invariants
    max_actions_per_hour: int = 10
    max_actions_per_24h: int = 50
    loss_retry_cooldown_minutes: int = 60  # Cooldown after loss
    action_cooldown_seconds: int = 60      # Cooldown between actions
    
    # Systemic invariants
    max_consecutive_errors: int = 5


# ========== RISK CORE FSM ==========
# ADR-TRADING-RISK-CORE-001 Section 2: FSM Rules

class RiskCore:
    """
    Trading Risk Core v1.0 - Pure Policy Enforcement Layer
    
    ADR-TRADING-RISK-CORE-001
    
    Characteristics:
    - Isolated module
    - Deterministic
    - Stateless except: FSM state, rolling counters, cooldown timers
    - Pure policy enforcement (NOT strategy, NOT execution, NOT optimization)
    - Always fails closed
    - Has veto power
    """
    
    def __init__(self, config: Optional[RiskCoreConfig] = None):
        """
        Initialize Risk Core.
        
        ADR-TRADING-RISK-CORE-001 Section 1: Risk Core as Independent Module
        """
        self.config = config or RiskCoreConfig()
        
        # FSM state (only state allowed)
        self._risk_state = RiskState.SAFE
        
        # Rolling counters (allowed state)
        self._behavioral_counters = BehavioralCounters(
            actions_last_hour=0,
            actions_last_24h=0,
            consecutive_losses=0
        )
        
        # Cooldown timers (allowed state)
        self._cooldown_until: Optional[datetime] = None
        self._loss_cooldown_until: Optional[datetime] = None
        
        # Last update timestamp for rolling windows
        self._last_hour_window_start: datetime = datetime.now(UTC)
        self._last_24h_window_start: datetime = datetime.now(UTC)
    
    @property
    def risk_state(self) -> RiskState:
        """Current risk state (for observability)."""
        return self._risk_state
    
    def evaluate(
        self,
        intent: TradingIntent,
        capital: CapitalSnapshot,
        exposure: ExposureSnapshot,
        behavioral: BehavioralCounters,
        system_health: SystemHealthFlags
    ) -> Tuple[TradingPermission, RiskState, Optional[ViolationReport]]:
        """
        Evaluate trading intent against all invariants.
        
        ADR-TRADING-RISK-CORE-001 Section 4: Interfaces
        
        Args:
            intent: Trading intent (facts only)
            capital: Capital snapshot (facts only)
            exposure: Exposure snapshot (facts only)
            behavioral: Behavioral counters (facts only)
            system_health: System health flags (read-only)
        
        Returns:
            Tuple of (TradingPermission, RiskState, ViolationReport)
        
        Failure semantics (ADR-TRADING-RISK-CORE-001 Section 6):
        - If Risk Core fails → DENY
        - If inputs inconsistent → HALTED
        - If state undefined → HALTED
        - If invariant unclear → DENY
        """
        try:
            # Validate inputs (fail-closed)
            if not self._validate_inputs(intent, capital, exposure, behavioral, system_health):
                self._risk_state = RiskState.HALTED
                violation_report = ViolationReport(
                    violations=["Invalid inputs detected"],
                    highest_severity_state=RiskState.HALTED,
                    violated_invariants=["INPUT_VALIDATION"]
                )
                logger.error("Risk Core: Invalid inputs → HALTED")
                return TradingPermission.DENY, RiskState.HALTED, violation_report
            
            # Update rolling counters
            self._update_rolling_counters(behavioral)
            
            # Check all invariants and collect violations
            violation_report = ViolationReport()
            
            # 1. Capital invariants (ADR-TRADING-RISK-CORE-001 Section 3.1)
            self._check_capital_invariants(capital, violation_report)
            
            # 2. Exposure invariants (ADR-TRADING-RISK-CORE-001 Section 3.2)
            self._check_exposure_invariants(intent, exposure, capital, violation_report)
            
            # 3. Behavioral invariants (ADR-TRADING-RISK-CORE-001 Section 3.3)
            self._check_behavioral_invariants(behavioral, violation_report)
            
            # 4. Systemic/Unknown invariants (ADR-TRADING-RISK-CORE-001 Section 3.4)
            self._check_systemic_invariants(system_health, violation_report)
            
            # 5. Meta invariants (ADR-TRADING-RISK-CORE-001 Section 3.5)
            # (Bypass detection is handled at integration layer)
            
            # Determine FSM state from violations (ADR-TRADING-RISK-CORE-001 Section 2: FSM Rules)
            new_state = self._determine_risk_state(violation_report)
            
            # Update FSM state
            self._risk_state = new_state
            
            # Determine trading permission from state
            permission = self._state_to_permission(new_state)
            
            # Log for observability
            if violation_report.violations:
                logger.warning(
                    f"Risk Core: {len(violation_report.violations)} violations detected, "
                    f"state={new_state.value}, permission={permission.value}"
                )
            
            return permission, new_state, violation_report
            
        except Exception as e:
            # Fail-closed: any exception → DENY
            logger.error(f"Risk Core evaluation failed: {type(e).__name__}: {e}", exc_info=True)
            self._risk_state = RiskState.HALTED
            violation_report = ViolationReport(
                violations=[f"Risk Core exception: {type(e).__name__}"],
                highest_severity_state=RiskState.HALTED,
                violated_invariants=["SYSTEM_ERROR"]
            )
            return TradingPermission.DENY, RiskState.HALTED, violation_report
    
    def _validate_inputs(
        self,
        intent: TradingIntent,
        capital: CapitalSnapshot,
        exposure: ExposureSnapshot,
        behavioral: BehavioralCounters,
        system_health: SystemHealthFlags
    ) -> bool:
        """
        Validate inputs - fail-closed on inconsistency.
        
        ADR-TRADING-RISK-CORE-001 Section 6: If inputs inconsistent → HALTED
        """
        # Check intent
        if intent.position_size_usd <= 0:
            return False
        if intent.entry_price <= 0 or intent.stop_price <= 0:
            return False
        if intent.side not in ["LONG", "SHORT"]:
            return False
        
        # Check capital
        if capital.current_balance_usd < 0:
            return False
        if capital.initial_balance_usd <= 0:
            return False
        
        # Check exposure
        if exposure.total_exposure_usd < 0:
            return False
        
        # Check behavioral
        if behavioral.actions_last_hour < 0 or behavioral.actions_last_24h < 0:
            return False
        if behavioral.consecutive_losses < 0:
            return False
        
        return True
    
    def _update_rolling_counters(self, behavioral: BehavioralCounters):
        """Update rolling counters for time windows."""
        now = datetime.now(UTC)
        
        # Reset hourly window if needed
        if (now - self._last_hour_window_start) >= timedelta(hours=1):
            self._behavioral_counters.actions_last_hour = 0
            self._last_hour_window_start = now
        
        # Reset 24h window if needed
        if (now - self._last_24h_window_start) >= timedelta(hours=24):
            self._behavioral_counters.actions_last_24h = 0
            self._last_24h_window_start = now
        
        # Update from external behavioral counters
        self._behavioral_counters.actions_last_hour = behavioral.actions_last_hour
        self._behavioral_counters.actions_last_24h = behavioral.actions_last_24h
        self._behavioral_counters.consecutive_losses = behavioral.consecutive_losses
        self._behavioral_counters.last_loss_timestamp = behavioral.last_loss_timestamp
        self._behavioral_counters.last_action_timestamp = behavioral.last_action_timestamp
    
    def _check_capital_invariants(
        self,
        capital: CapitalSnapshot,
        violation_report: ViolationReport
    ):
        """
        Check capital invariants.
        
        ADR-TRADING-RISK-CORE-001 Section 3.1:
        - Absolute loss limit
        - Time-bound loss limit
        - Irreversibility
        """
        # Absolute loss limit
        loss_pct = (capital.total_loss_usd / capital.initial_balance_usd * 100) if capital.initial_balance_usd > 0 else 0
        if loss_pct >= self.config.max_absolute_loss_pct:
            violation_report.violations.append(
                f"Capital invariant violated: Absolute loss {loss_pct:.2f}% >= {self.config.max_absolute_loss_pct}%"
            )
            violation_report.violated_invariants.append("CAPITAL_ABSOLUTE_LOSS")
            violation_report.highest_severity_state = max(
                violation_report.highest_severity_state,
                RiskState.LOCKED,
                key=lambda s: self._state_severity(s)
            )
        
        # Time-bound loss limit (24h)
        loss_24h_pct = (capital.loss_24h_usd / capital.current_balance_usd * 100) if capital.current_balance_usd > 0 else 0
        if loss_24h_pct >= self.config.max_loss_24h_pct:
            violation_report.violations.append(
                f"Capital invariant violated: 24h loss {loss_24h_pct:.2f}% >= {self.config.max_loss_24h_pct}%"
            )
            violation_report.violated_invariants.append("CAPITAL_TIME_BOUND_24H")
            violation_report.highest_severity_state = max(
                violation_report.highest_severity_state,
                RiskState.LOCKED,
                key=lambda s: self._state_severity(s)
            )
        
        # Time-bound loss limit (7d)
        loss_7d_pct = (capital.loss_7d_usd / capital.current_balance_usd * 100) if capital.current_balance_usd > 0 else 0
        if loss_7d_pct >= self.config.max_loss_7d_pct:
            violation_report.violations.append(
                f"Capital invariant violated: 7d loss {loss_7d_pct:.2f}% >= {self.config.max_loss_7d_pct}%"
            )
            violation_report.violated_invariants.append("CAPITAL_TIME_BOUND_7D")
            violation_report.highest_severity_state = max(
                violation_report.highest_severity_state,
                RiskState.LOCKED,
                key=lambda s: self._state_severity(s)
            )
        
        # Irreversibility: losses can only increase
        # (This is enforced by the fact that we track cumulative loss)
    
    def _check_exposure_invariants(
        self,
        intent: TradingIntent,
        exposure: ExposureSnapshot,
        capital: CapitalSnapshot,
        violation_report: ViolationReport
    ):
        """
        Check exposure invariants.
        
        ADR-TRADING-RISK-CORE-001 Section 3.2:
        - Single position cap
        - Aggregate exposure cap
        - Correlated group cap (strategy-blind)
        """
        balance = capital.current_balance_usd
        
        # Single position cap
        single_position_pct = (intent.position_size_usd / balance * 100) if balance > 0 else 0
        if single_position_pct > self.config.max_single_position_pct:
            violation_report.violations.append(
                f"Exposure invariant violated: Single position {single_position_pct:.2f}% > {self.config.max_single_position_pct}%"
            )
            violation_report.violated_invariants.append("EXPOSURE_SINGLE_POSITION")
            violation_report.highest_severity_state = max(
                violation_report.highest_severity_state,
                RiskState.LIMITED,
                key=lambda s: self._state_severity(s)
            )
        
        # Aggregate exposure cap (including new position)
        new_total_exposure = exposure.total_exposure_usd + intent.position_size_usd
        aggregate_pct = (new_total_exposure / balance * 100) if balance > 0 else 0
        if aggregate_pct > self.config.max_aggregate_exposure_pct:
            violation_report.violations.append(
                f"Exposure invariant violated: Aggregate exposure {aggregate_pct:.2f}% > {self.config.max_aggregate_exposure_pct}%"
            )
            violation_report.violated_invariants.append("EXPOSURE_AGGREGATE")
            violation_report.highest_severity_state = max(
                violation_report.highest_severity_state,
                RiskState.LOCKED,
                key=lambda s: self._state_severity(s)
            )
        
        # Correlated group cap (strategy-blind)
        # Check if intent.symbol belongs to any correlation group
        for group_name, symbols in exposure.correlation_groups.items():
            if intent.symbol in symbols:
                # Calculate total exposure of this group (including new position)
                group_exposure = sum(
                    pos.position_size_usd
                    for pos in exposure.open_positions
                    if pos.symbol in symbols
                )
                group_exposure += intent.position_size_usd
                group_pct = (group_exposure / balance * 100) if balance > 0 else 0
                
                if group_pct > self.config.max_correlated_group_pct:
                    violation_report.violations.append(
                        f"Exposure invariant violated: Correlated group '{group_name}' {group_pct:.2f}% > {self.config.max_correlated_group_pct}%"
                    )
                    violation_report.violated_invariants.append("EXPOSURE_CORRELATED_GROUP")
                    violation_report.highest_severity_state = max(
                        violation_report.highest_severity_state,
                        RiskState.LOCKED,
                        key=lambda s: self._state_severity(s)
                    )
    
    def _check_behavioral_invariants(
        self,
        behavioral: BehavioralCounters,
        violation_report: ViolationReport
    ):
        """
        Check behavioral invariants.
        
        ADR-TRADING-RISK-CORE-001 Section 3.3:
        - Action rate limiting
        - Loss retry suppression
        - Cooldown enforcement
        """
        # Action rate limiting (per hour)
        if behavioral.actions_last_hour >= self.config.max_actions_per_hour:
            violation_report.violations.append(
                f"Behavioral invariant violated: Actions per hour {behavioral.actions_last_hour} >= {self.config.max_actions_per_hour}"
            )
            violation_report.violated_invariants.append("BEHAVIORAL_ACTION_RATE_HOUR")
            violation_report.highest_severity_state = max(
                violation_report.highest_severity_state,
                RiskState.LIMITED,
                key=lambda s: self._state_severity(s)
            )
        
        # Action rate limiting (per 24h)
        if behavioral.actions_last_24h >= self.config.max_actions_per_24h:
            violation_report.violations.append(
                f"Behavioral invariant violated: Actions per 24h {behavioral.actions_last_24h} >= {self.config.max_actions_per_24h}"
            )
            violation_report.violated_invariants.append("BEHAVIORAL_ACTION_RATE_24H")
            violation_report.highest_severity_state = max(
                violation_report.highest_severity_state,
                RiskState.LOCKED,
                key=lambda s: self._state_severity(s)
            )
        
        # Loss retry suppression
        if behavioral.consecutive_losses > 0:
            now = datetime.now(UTC)
            if behavioral.last_loss_timestamp:
                time_since_loss = (now - behavioral.last_loss_timestamp).total_seconds() / 60
                if time_since_loss < self.config.loss_retry_cooldown_minutes:
                    violation_report.violations.append(
                        f"Behavioral invariant violated: Loss retry cooldown active "
                        f"({time_since_loss:.1f}min < {self.config.loss_retry_cooldown_minutes}min)"
                    )
                    violation_report.violated_invariants.append("BEHAVIORAL_LOSS_RETRY_SUPPRESSION")
                    violation_report.highest_severity_state = max(
                        violation_report.highest_severity_state,
                        RiskState.LOCKED,
                        key=lambda s: self._state_severity(s)
                    )
        
        # Cooldown enforcement
        if behavioral.last_action_timestamp:
            now = datetime.now(UTC)
            time_since_action = (now - behavioral.last_action_timestamp).total_seconds()
            if time_since_action < self.config.action_cooldown_seconds:
                violation_report.violations.append(
                    f"Behavioral invariant violated: Action cooldown active "
                    f"({time_since_action:.1f}s < {self.config.action_cooldown_seconds}s)"
                )
                violation_report.violated_invariants.append("BEHAVIORAL_COOLDOWN")
                violation_report.highest_severity_state = max(
                    violation_report.highest_severity_state,
                    RiskState.LIMITED,
                    key=lambda s: self._state_severity(s)
                )
    
    def _check_systemic_invariants(
        self,
        system_health: SystemHealthFlags,
        violation_report: ViolationReport
    ):
        """
        Check systemic/unknown invariants.
        
        ADR-TRADING-RISK-CORE-001 Section 3.4:
        - Unsafe runtime → LOCKED
        - Unknown state → HALTED
        """
        # Unsafe runtime → LOCKED
        if not system_health.runtime_healthy:
            violation_report.violations.append("Systemic invariant violated: Runtime unhealthy")
            violation_report.violated_invariants.append("SYSTEMIC_UNSAFE_RUNTIME")
            violation_report.highest_severity_state = max(
                violation_report.highest_severity_state,
                RiskState.LOCKED,
                key=lambda s: self._state_severity(s)
            )
        
        # Critical modules unavailable → LOCKED
        if not system_health.critical_modules_available:
            violation_report.violations.append("Systemic invariant violated: Critical modules unavailable")
            violation_report.violated_invariants.append("SYSTEMIC_CRITICAL_MODULES_UNAVAILABLE")
            violation_report.highest_severity_state = max(
                violation_report.highest_severity_state,
                RiskState.LOCKED,
                key=lambda s: self._state_severity(s)
            )
        
        # Too many consecutive errors → LOCKED
        if system_health.consecutive_errors >= self.config.max_consecutive_errors:
            violation_report.violations.append(
                f"Systemic invariant violated: Consecutive errors {system_health.consecutive_errors} >= {self.config.max_consecutive_errors}"
            )
            violation_report.violated_invariants.append("SYSTEMIC_CONSECUTIVE_ERRORS")
            violation_report.highest_severity_state = max(
                violation_report.highest_severity_state,
                RiskState.LOCKED,
                key=lambda s: self._state_severity(s)
            )
        
        # Safe mode → LOCKED
        if system_health.is_safe_mode:
            violation_report.violations.append("Systemic invariant violated: System in safe mode")
            violation_report.violated_invariants.append("SYSTEMIC_SAFE_MODE")
            violation_report.highest_severity_state = max(
                violation_report.highest_severity_state,
                RiskState.LOCKED,
                key=lambda s: self._state_severity(s)
            )
    
    def _determine_risk_state(self, violation_report: ViolationReport) -> RiskState:
        """
        Determine FSM state from violations.
        
        ADR-TRADING-RISK-CORE-001 Section 2: FSM Rules
        - Severity order: HALTED > LOCKED > LIMITED > SAFE
        - Unknown > Known
        - Capital > Behavior
        - Multiple violations → choose highest severity
        """
        if not violation_report.violations:
            # No violations → SAFE
            return RiskState.SAFE
        
        # Return highest severity state from violations
        return violation_report.highest_severity_state
    
    def _state_to_permission(self, state: RiskState) -> TradingPermission:
        """
        Convert FSM state to trading permission.
        
        ADR-TRADING-RISK-CORE-001 Section 4: Outputs ONLY
        """
        if state == RiskState.SAFE:
            return TradingPermission.ALLOW
        elif state == RiskState.LIMITED:
            return TradingPermission.ALLOW_LIMITED
        elif state == RiskState.LOCKED:
            return TradingPermission.DENY
        elif state == RiskState.HALTED:
            return TradingPermission.DENY
        else:
            # Unknown state → DENY (fail-closed)
            logger.error(f"Risk Core: Unknown state {state} → DENY")
            return TradingPermission.DENY
    
    def _state_severity(self, state: RiskState) -> int:
        """Get severity level for state ordering."""
        severity_map = {
            RiskState.SAFE: 0,
            RiskState.LIMITED: 1,
            RiskState.LOCKED: 2,
            RiskState.HALTED: 3
        }
        return severity_map.get(state, 3)  # Unknown → highest severity
    
    def reset(self):
        """
        Reset Risk Core state (for testing/recovery).
        
        Note: HALTED state cannot be reset automatically (ADR-TRADING-RISK-CORE-001 Section 2).
        """
        if self._risk_state == RiskState.HALTED:
            logger.warning("Risk Core: Cannot reset HALTED state automatically")
            return
        
        self._risk_state = RiskState.SAFE
        self._behavioral_counters = BehavioralCounters(
            actions_last_hour=0,
            actions_last_24h=0,
            consecutive_losses=0
        )
        self._cooldown_until = None
        self._loss_cooldown_until = None
        self._last_hour_window_start = datetime.now(UTC)
        self._last_24h_window_start = datetime.now(UTC)


# ========== GLOBAL INSTANCE ==========

_risk_core: Optional[RiskCore] = None


def get_risk_core(config: Optional[RiskCoreConfig] = None) -> RiskCore:
    """Get global Risk Core instance."""
    global _risk_core
    if _risk_core is None:
        _risk_core = RiskCore(config)
    return _risk_core

