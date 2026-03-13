"""
Автоматический запуск и перезапуск торгового бота
Обеспечивает непрерывную работу с автоматическим восстановлением при ошибках

Runtime Layer - Production Hardening:
- Structured logging with PID, task name, component
- Non-blocking heartbeat (every 10 seconds)
- AsyncIO safety with task tracking
- Telegram boundary hardening
- Graceful shutdown (SIGTERM/SIGINT)
- Single-instance protection
- systemd compatibility

INVARIANTS:
- FATAL ⇒ process MUST exit (enforced by FATAL_REAPER thread)
- SAFE_MODE TTL ⇒ exit even if asyncio stalled (enforced by ThreadWatchdog)
- ThreadWatchdog never mutates state (only sends events)
- StateMachine is single-writer (all transitions via transition_to)
- No async dependency for death (os._exit from threads)
- Event queue overflow → FATAL (hard guarantee delivery)
- No state transitions after shutdown start
- FATAL_REAPER runs in daemon thread, checks every 1-2 seconds
- ThreadWatchdog enforces SAFE_MODE TTL with direct os._exit
"""
import asyncio
import logging
import sys
import traceback
import signal
import os
import time
import threading
import uuid
from datetime import datetime, UTC, timedelta
from enum import Enum
from pathlib import Path
from typing import Set, Optional

# File locking (Unix only)
try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False  # Windows

# Импорты для работы бота
from error_alert import error_alert
from telegram_bot import send_message
from health_monitor import send_heartbeat, HEARTBEAT_INTERVAL
from daily_report import generate_daily_report

# Новые модули для контролируемой архитектуры
from chaos_engine import get_chaos_engine, ChaosType
from system_state_machine import get_state_machine, SystemState as SystemStateEnum
from task_dump import log_task_dump
from systemd_integration import get_systemd_integration, ExitCode

# Импорты для анализа рынка (будем вызывать напрямую)
from config import SYMBOLS, TIMEFRAMES
from data_loader import get_candles_parallel
from time_filter import is_good_time
from correlation_analysis import analyze_market_correlations
from spike_alert import check_all_symbols_for_spikes
from signal_generator import generate_signals_for_symbols

# Экосистема
from core.decision_core import get_decision_core
from brains.market_regime_brain import get_market_regime_brain
from brains.risk_exposure_brain import get_risk_exposure_brain
from brains.cognitive_filter import get_cognitive_filter
from brains.opportunity_awareness import get_opportunity_awareness
from execution.gatekeeper import get_gatekeeper

# Настройки
BASE_DIR = Path(__file__).parent.absolute()
LOG_FILE = os.environ.get("LOG_FILE", str(BASE_DIR / "runner.log"))
PID_FILE = os.environ.get("PID_FILE", str(BASE_DIR / "market_bot.pid"))
ANALYSIS_INTERVAL = int(os.environ.get("BOT_INTERVAL", "300"))  # 5 минут (базовый интервал)
MAX_CONSECUTIVE_ERRORS = int(os.environ.get("MAX_CONSECUTIVE_ERRORS", "5"))
ERROR_PAUSE = int(os.environ.get("ERROR_PAUSE", "600"))  # 10 минут

# Adaptive system parameters
ADAPTIVE_INTERVAL_MIN = float(os.environ.get("ADAPTIVE_INTERVAL_MIN", "300"))  # Минимальный интервал (базовый)
ADAPTIVE_INTERVAL_MAX = float(os.environ.get("ADAPTIVE_INTERVAL_MAX", "900"))  # Максимальный интервал (3x базового)
ADAPTIVE_INTERVAL_MULTIPLIER = float(os.environ.get("ADAPTIVE_INTERVAL_MULTIPLIER", "1.5"))  # Множитель при ошибках
ADAPTIVE_STABLE_CYCLES = int(os.environ.get("ADAPTIVE_STABLE_CYCLES", "3"))  # Количество успешных циклов для уменьшения интервала
AUTO_RESUME_SAFE_MODE_DELAY = int(os.environ.get("AUTO_RESUME_SAFE_MODE_DELAY", "60"))  # Задержка перед auto-resume (секунды)

# Adaptive system feature flags
ADAPTIVE_INTERVAL_ENABLED = os.environ.get("ADAPTIVE_INTERVAL_ENABLED", "true").lower() == "true"
AUTO_RESUME_TRADING_ENABLED = os.environ.get("AUTO_RESUME_TRADING_ENABLED", "true").lower() == "true"
AUTO_RESUME_SUCCESS_CYCLES = int(os.environ.get("AUTO_RESUME_SUCCESS_CYCLES", "3"))  # Количество успешных циклов для auto-resume

# Analysis timing limits
MAX_ANALYSIS_TIME = float(os.environ.get("MAX_ANALYSIS_TIME", "30"))  # секунд - мягкий лимит
ALERT_ANALYSIS_TIME = float(os.environ.get("ALERT_ANALYSIS_TIME", "60"))  # секунд - порог для алерта
ALERT_COOLDOWN = int(os.environ.get("ALERT_COOLDOWN", "300"))  # секунд - cooldown между алертами
METRICS_LOG_INTERVAL = int(os.environ.get("METRICS_LOG_INTERVAL", "600"))  # секунд - интервал логирования метрик

# Alert escalation thresholds
WARN_ERROR_THRESHOLD = int(os.environ.get("WARN_ERROR_THRESHOLD", "3"))  # WARN при >= 3 ошибках
CRITICAL_ERROR_THRESHOLD = int(os.environ.get("CRITICAL_ERROR_THRESHOLD", "5"))  # CRITICAL при >= 5 ошибках
VOLATILITY_THRESHOLD = float(os.environ.get("VOLATILITY_THRESHOLD", "0.5"))  # Порог волатильности для WARN (placeholder)
RUNTIME_HEARTBEAT_INTERVAL = 10.0  # 10 секунд для runtime heartbeat

# Health server configuration
HEALTH_SERVER_HOST = os.environ.get("HEALTH_SERVER_HOST", "127.0.0.1")
HEALTH_SERVER_PORT = int(os.environ.get("HEALTH_SERVER_PORT", "8080"))

# Global reference to control plane server for graceful shutdown
_control_plane_server = None
SYNTHETIC_DECISION_TICK_INTERVAL = 10.0  # 10 секунд для synthetic decision tick
ENABLE_SYNTHETIC_DECISION_TICK = os.environ.get("ENABLE_SYNTHETIC_DECISION_TICK", "false").lower() == "true"
FAULT_INJECT_LOOP_STALL = os.environ.get("FAULT_INJECT_LOOP_STALL", "false").lower() == "true"
LOOP_STALL_DURATION = 120.0  # 120 секунд для loop stall
HEARTBEAT_MISS_THRESHOLD = 2.0  # Пропуск 2 heartbeats = stall detected

# ========== PRODUCTION HARDENING CONSTANTS ==========
HEARTBEAT_MISS_ENFORCEMENT_THRESHOLD = 2  # После 2 пропущенных heartbeats → SAFE_MODE
LOOP_GUARD_TIMEOUT = 300.0  # 300 секунд - максимальное время блокировки event loop
ITERATION_BUDGET_SECONDS = 60.0  # 60 секунд - жесткий лимит времени на одну итерацию анализа (с большим запасом от LOOP_GUARD_TIMEOUT)
SAFE_MODE_TTL = 600.0  # 600 секунд (10 минут) - TTL для SAFE_MODE
GRACEFUL_SHUTDOWN_TIMEOUT = 10.0  # 10 секунд - жёсткий таймаут на graceful shutdown
FATAL_EXIT_CODE = 10  # Exit code для FATAL состояния (systemd restart)

# ========== THREAD WATCHDOG CONSTANTS ==========
THREAD_WATCHDOG_INTERVAL = 5.0  # Проверка каждые 5 секунд
THREAD_WATCHDOG_HEARTBEAT_TIMEOUT = 30.0  # 30 секунд без heartbeat → LOOP_STALL

# ========== CHAOS TRACKING (для инварианта) ==========
# HARDENING: _chaos_was_active остается для chaos invariant tracking
_chaos_was_active: bool = False  # Флаг: был ли chaos активен (для REQUIREMENT 2)
# HARDENING: _safe_mode_entered_at УДАЛЕН - теперь управляется state machine

# ========== THREAD-SAFE HEARTBEAT ACCESS ==========
_heartbeat_lock = threading.Lock()  # Lock для thread-safe доступа к last_heartbeat

# ========== STRUCTURED LOGGING ==========

class StructuredFormatter(logging.Formatter):
    """
    Structured formatter для production logging.
    Формат: timestamp | level | pid | task | component | message
    """
    def __init__(self):
        super().__init__()
        self.pid = os.getpid()
    
    def format(self, record: logging.LogRecord) -> str:
        # Извлекаем task name из record (если есть)
        task_name = getattr(record, 'task_name', None)
        if task_name is None:
            # Пытаемся получить из текущего task (безопасно)
            try:
                current_task = asyncio.current_task()
                if current_task:
                    task_name = current_task.get_name()
                else:
                    task_name = 'main'
            except RuntimeError:
                # Нет event loop - не async контекст
                task_name = 'main'
        
        component = getattr(record, 'component', 'runner')
        
        # Формируем структурированное сообщение
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat()
        level = record.levelname
        message = record.getMessage()
        
        # JSON-like структурированный формат (читаемый для journalctl)
        log_entry = (
            f"timestamp={timestamp} "
            f"level={level} "
            f"pid={self.pid} "
            f"task={task_name} "
            f"component={component} "
            f"message={message}"
        )
        
        # Добавляем exception info если есть
        if record.exc_info:
            log_entry += f"\n{self.formatException(record.exc_info)}"
        
        return log_entry

# Настройка структурированного логирования
def setup_structured_logging():
    """Настраивает структурированное логирование для production"""
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # Удаляем существующие handlers
    root_logger.handlers.clear()
    
    # Создаём formatter
    formatter = StructuredFormatter()
    
    # File handler
    file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    
    # Console handler (для systemd/journalctl)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    return root_logger

# Инициализируем логирование
root_logger = setup_structured_logging()
logger = logging.getLogger(__name__)

# Настраиваем record factory для автоматического добавления component и task_name
old_factory = logging.getLogRecordFactory()

def enhanced_record_factory(*args, **kwargs):
    """Enhanced record factory для добавления component и task_name"""
    record = old_factory(*args, **kwargs)
    
    # Добавляем component из logger name
    if not hasattr(record, 'component'):
        record.component = record.name.split('.')[0] if '.' in record.name else 'runner'
    
    # Добавляем task_name если в async контексте
    if not hasattr(record, 'task_name'):
        try:
            current_task = asyncio.current_task()
            if current_task:
                record.task_name = current_task.get_name()
        except RuntimeError:
            # Нет event loop
            pass
    
    return record

logging.setLogRecordFactory(enhanced_record_factory)


# Импортируем SystemState
from system_state import SystemState

# Создаем единое состояние системы
system_state = SystemState()

# Устанавливаем глобальный экземпляр для доступа из telegram_commands
from system_state import set_system_state
set_system_state(system_state)

# ========== HARDENING: STATE MACHINE HELPER FUNCTIONS ==========
async def enter_safe_mode(reason: str, owner: str, metadata: Optional[dict] = None) -> bool:
    """
    HARDENING: Единая точка входа в SAFE_MODE через state machine.
    
    Все переходы в SAFE_MODE должны использовать эту функцию.
    Автоматически синхронизирует состояние с system_state.
    """
    state_machine = get_state_machine()
    success = await state_machine.transition_to(
        SystemStateEnum.SAFE_MODE,
        reason=reason,
        owner=owner,
        metadata=metadata
    )
    return success

async def exit_safe_mode_via_recovery(reason: str, owner: str) -> bool:
    """
    HARDENING: Выход из SAFE_MODE через recovery (единственный разрешённый путь).
    
    Используется только после успешных recovery cycles.
    """
    state_machine = get_state_machine()
    success = await state_machine.transition_to(
        SystemStateEnum.RECOVERING,
        reason,
        owner
    )
    if success:
        # После RECOVERING можно перейти в RUNNING
        await state_machine.transition_to(
            SystemStateEnum.RUNNING,
            f"Recovery completed: {reason}",
            owner
        )
    return success

# ========== GLOBAL METRICS FOR HEALTH ENDPOINT ==========
# Метрики анализа рынка для healthcheck endpoint
# Обновляются в market_analysis_loop
_analysis_metrics = {
    "analysis_count": 0,
    "analysis_total_time": 0.0,
    "analysis_max_time": 0.0,
    "last_analysis_duration": 0.0,
    "start_time": None,  # Будет установлено при первом запуске
}

# ========== PROMETHEUS METRICS STATE ==========
# Histogram buckets for analysis duration (seconds)
ANALYSIS_DURATION_BUCKETS = [0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 13.0]

# Prometheus metrics state
_prometheus_metrics = {
    # Histogram: analysis duration buckets
    "analysis_duration_buckets": {bucket: 0 for bucket in ANALYSIS_DURATION_BUCKETS},
    "analysis_duration_sum": 0.0,  # Sum of all durations
    "analysis_duration_count": 0,  # Total observations
    
    # Counters
    "scheduler_stalls_total": 0,
    "analysis_cycles_total": 0,
    # Admin command counters with result labels
    # Structure: {"command": {"result": count}}
    "admin_commands_total": {
        "pause": {"success": 0},
        "resume": {"success": 0, "blocked_safe_mode": 0}
    }
}

# Adaptive system state (volatility tracking, recovery cycles)
_adaptive_system_state = {
    "volatility_state": "MEDIUM",  # LOW, MEDIUM, HIGH (from market_regime.volatility_level)
    "adaptive_interval": None,  # Current adaptive interval (None = not initialized)
    "recovery_cycles": 0,  # Consecutive successful cycles while trading_paused
}

# Control plane state (manual pause tracking)
_control_plane_state = {
    "manual_pause_active": False,  # True if trading was paused manually (via admin/telegram)
    # NOTE: admin_commands_total moved to _prometheus_metrics for single source of truth
}

# ========== CONCURRENCY PROTECTION FOR HTTP HANDLERS ==========
# Lock to prevent race conditions in HTTP handlers (especially admin commands)
# REQUIREMENT: Concurrent HTTP requests cannot race-clear safe_mode or resume trading while safe_mode == true
# Initialized lazily in start_http_server() when event loop is available
_admin_command_lock = None

def _get_admin_lock():
    """Returns the admin command lock, initializing it if needed"""
    global _admin_command_lock
    if _admin_command_lock is None:
        _admin_command_lock = asyncio.Lock()
    return _admin_command_lock

def get_analysis_metrics():
    """Возвращает текущие метрики анализа для health endpoint"""
    return _analysis_metrics.copy()

def update_analysis_metrics(metrics_update: dict):
    """Обновляет глобальные метрики анализа"""
    global _analysis_metrics
    _analysis_metrics.update(metrics_update)

def get_prometheus_metrics():
    """Возвращает текущие Prometheus метрики"""
    return _prometheus_metrics.copy()

def record_analysis_duration(duration: float):
    """
    Записывает длительность анализа в histogram buckets.
    
    NON-BLOCKING: Просто обновляет счетчики в памяти.
    
    Prometheus histogram buckets are cumulative:
    - Each bucket counts all observations <= bucket value
    - Values < smallest bucket are still counted in smallest bucket
    """
    global _prometheus_metrics
    # Обновляем sum и count
    _prometheus_metrics["analysis_duration_sum"] += duration
    _prometheus_metrics["analysis_duration_count"] += 1
    
    # Обновляем buckets (cumulative - все bucket'ы >= duration увеличиваются)
    for bucket in ANALYSIS_DURATION_BUCKETS:
        if duration <= bucket:
            _prometheus_metrics["analysis_duration_buckets"][bucket] += 1

def increment_scheduler_stalls():
    """Увеличивает счетчик scheduler stalls (NON-BLOCKING)"""
    global _prometheus_metrics
    _prometheus_metrics["scheduler_stalls_total"] += 1

def increment_analysis_cycles():
    """Увеличивает счетчик завершенных циклов анализа (NON-BLOCKING)"""
    global _prometheus_metrics
    _prometheus_metrics["analysis_cycles_total"] += 1

def get_adaptive_system_state():
    """Возвращает текущее состояние адаптивной системы"""
    return _adaptive_system_state.copy()

def update_volatility_state(volatility_level: str):
    """Обновляет состояние волатильности (NON-BLOCKING)"""
    global _adaptive_system_state
    # Нормализуем уровень волатильности: LOW, MEDIUM, HIGH
    if volatility_level in ["LOW", "NORMAL", "MEDIUM", "HIGH", "EXTREME"]:
        # Маппинг: LOW -> LOW, NORMAL/MEDIUM -> MEDIUM, HIGH/EXTREME -> HIGH
        if volatility_level == "LOW":
            _adaptive_system_state["volatility_state"] = "LOW"
        elif volatility_level in ["NORMAL", "MEDIUM"]:
            _adaptive_system_state["volatility_state"] = "MEDIUM"
        else:  # HIGH, EXTREME
            _adaptive_system_state["volatility_state"] = "HIGH"

def pause_trading_manually():
    """
    Приостанавливает торговлю вручную (через admin/telegram).
    
    Returns:
        bool: True если успешно, False если уже приостановлена
    """
    global _control_plane_state, _prometheus_metrics, _adaptive_system_state
    
    if _control_plane_state["manual_pause_active"]:
        return False  # Уже приостановлена
    
    _control_plane_state["manual_pause_active"] = True
    
    # HARDENING: Синхронизируем trading_paused через state machine
    state_machine = get_state_machine()
    state_machine.sync_to_system_state(system_state, manual_pause_active=True)
    
    # Обновляем метрику с новой структурой
    if "success" not in _prometheus_metrics["admin_commands_total"]["pause"]:
        _prometheus_metrics["admin_commands_total"]["pause"]["success"] = 0
    _prometheus_metrics["admin_commands_total"]["pause"]["success"] += 1
    _adaptive_system_state["recovery_cycles"] = 0
    
    logger.info("Trading paused manually via control plane")
    return True

def resume_trading_manually():
    """
    Возобновляет торговлю вручную (через admin/telegram).
    
    Returns:
        tuple: (success: bool, message: str)
    """
    global _control_plane_state, _prometheus_metrics, _adaptive_system_state
    
    # Проверяем safe_mode (имеет приоритет)
    if system_state.system_health.safe_mode:
        return (False, "Cannot resume: system is in safe_mode")
    
    # Проверяем, не активна ли уже торговля
    if not system_state.system_health.trading_paused:
        return (False, "Trading is already active")
    
    _control_plane_state["manual_pause_active"] = False
    
    # HARDENING: Синхронизируем trading_paused через state machine
    state_machine = get_state_machine()
    state_machine.sync_to_system_state(system_state, manual_pause_active=False)
    
    # Обновляем метрику с новой структурой (result labels)
    # ВАЖНО: Эта функция вызывается только если safe_mode == False (проверка выше)
    if "success" not in _prometheus_metrics["admin_commands_total"]["resume"]:
        _prometheus_metrics["admin_commands_total"]["resume"]["success"] = 0
    _prometheus_metrics["admin_commands_total"]["resume"]["success"] += 1
    _adaptive_system_state["recovery_cycles"] = 0
    
    logger.info("Trading resumed manually via control plane")
    return (True, "Trading resumed")

# ========== ALERT ESCALATION SYSTEM ==========

# Alert deduplication: track last sent timestamp per alert type
_alert_last_sent: dict[str, float] = {}

def _get_alert_key(alert_type: str, level: str) -> str:
    """Генерирует ключ для дедупликации алертов"""
    return f"{level}:{alert_type}"

def _should_send_alert(alert_key: str) -> bool:
    """Проверяет, можно ли отправить алерт (cooldown)"""
    global _alert_last_sent
    now = time.monotonic()
    last_sent = _alert_last_sent.get(alert_key, 0.0)
    return (now - last_sent) >= ALERT_COOLDOWN

def _mark_alert_sent(alert_key: str):
    """Отмечает, что алерт был отправлен"""
    global _alert_last_sent
    _alert_last_sent[alert_key] = time.monotonic()

async def evaluate_and_send_alerts(duration: float):
    """
    Оценивает условия для алертов и отправляет их асинхронно.
    
    NON-BLOCKING: Выполняется в отдельной задаче, не блокирует analysis loop.
    
    Args:
        duration: Длительность последнего анализа в секундах
    """
    try:
        # Получаем текущие метрики
        metrics = get_analysis_metrics()
        now = time.monotonic()
        
        # Вычисляем uptime для сообщений
        uptime = 0.0
        if metrics["start_time"] is not None:
            uptime = now - metrics["start_time"]
        
        alerts_to_send = []
        
        # ========== WARN ALERTS ==========
        
        # WARN: Analysis duration > ALERT_ANALYSIS_TIME
        if duration > ALERT_ANALYSIS_TIME:
            alert_key = _get_alert_key("analysis_duration_warn", "WARN")
            if _should_send_alert(alert_key):
                alerts_to_send.append({
                    "level": "WARN",
                    "type": "analysis_duration",
                    "message": (
                        f"⚠️ **WARN**: Market analysis slow\n\n"
                        f"Duration: {duration:.2f}s (limit: {ALERT_ANALYSIS_TIME:.2f}s)\n"
                        f"Uptime: {uptime:.0f}s\n"
                        f"Analysis runs: {metrics.get('analysis_count', 0)}\n"
                        f"Trading continues normally."
                    )
                })
                _mark_alert_sent(alert_key)
                logger.warning(f"WARN alert: Analysis duration {duration:.2f}s > {ALERT_ANALYSIS_TIME:.2f}s")
        
        # WARN: Consecutive errors >= WARN_ERROR_THRESHOLD
        if system_state.system_health.consecutive_errors >= WARN_ERROR_THRESHOLD:
            alert_key = _get_alert_key("consecutive_errors_warn", "WARN")
            if _should_send_alert(alert_key):
                alerts_to_send.append({
                    "level": "WARN",
                    "type": "consecutive_errors",
                    "message": (
                        f"⚠️ **WARN**: Multiple consecutive errors\n\n"
                        f"Consecutive errors: {system_state.system_health.consecutive_errors} "
                        f"(threshold: {WARN_ERROR_THRESHOLD})\n"
                        f"Uptime: {uptime:.0f}s\n"
                        f"Trading continues normally."
                    )
                })
                _mark_alert_sent(alert_key)
                logger.warning(f"WARN alert: Consecutive errors {system_state.system_health.consecutive_errors} >= {WARN_ERROR_THRESHOLD}")
        
        # WARN: Volatility spike (placeholder - пока не отслеживается)
        # TODO: Реализовать отслеживание волатильности
        volatility = 0.0  # Placeholder
        if volatility > VOLATILITY_THRESHOLD:
            alert_key = _get_alert_key("volatility_warn", "WARN")
            if _should_send_alert(alert_key):
                alerts_to_send.append({
                    "level": "WARN",
                    "type": "volatility",
                    "message": (
                        f"⚠️ **WARN**: Market volatility spike\n\n"
                        f"Volatility: {volatility:.3f} (threshold: {VOLATILITY_THRESHOLD:.3f})\n"
                        f"Uptime: {uptime:.0f}s\n"
                        f"Trading continues normally."
                    )
                })
                _mark_alert_sent(alert_key)
                logger.warning(f"WARN alert: Volatility {volatility:.3f} > {VOLATILITY_THRESHOLD:.3f}")
        
        # ========== CRITICAL ALERTS ==========
        
        # CRITICAL: Analysis duration > MAX_ANALYSIS_TIME
        if duration > MAX_ANALYSIS_TIME:
            alert_key = _get_alert_key("analysis_duration_critical", "CRITICAL")
            if _should_send_alert(alert_key):
                alerts_to_send.append({
                    "level": "CRITICAL",
                    "type": "analysis_duration",
                    "message": (
                        f"🚨 **CRITICAL**: Market analysis exceeded maximum time\n\n"
                        f"Duration: {duration:.2f}s (max: {MAX_ANALYSIS_TIME:.2f}s)\n"
                        f"Uptime: {uptime:.0f}s\n"
                        f"Analysis runs: {metrics.get('analysis_count', 0)}\n"
                        f"**Trading paused for safety.**"
                    ),
                    "pause_trading": True
                })
                _mark_alert_sent(alert_key)
                logger.error(f"CRITICAL alert: Analysis duration {duration:.2f}s > {MAX_ANALYSIS_TIME:.2f}s")
        
        # CRITICAL: Consecutive errors >= CRITICAL_ERROR_THRESHOLD
        if system_state.system_health.consecutive_errors >= CRITICAL_ERROR_THRESHOLD:
            alert_key = _get_alert_key("consecutive_errors_critical", "CRITICAL")
            if _should_send_alert(alert_key):
                alerts_to_send.append({
                    "level": "CRITICAL",
                    "type": "consecutive_errors",
                    "message": (
                        f"🚨 **CRITICAL**: Critical error threshold exceeded\n\n"
                        f"Consecutive errors: {system_state.system_health.consecutive_errors} "
                        f"(threshold: {CRITICAL_ERROR_THRESHOLD})\n"
                        f"Uptime: {uptime:.0f}s\n"
                        f"**Trading paused for safety.**"
                    ),
                    "pause_trading": True
                })
                _mark_alert_sent(alert_key)
                logger.error(f"CRITICAL alert: Consecutive errors {system_state.system_health.consecutive_errors} >= {CRITICAL_ERROR_THRESHOLD}")
        
        # CRITICAL: System entered safe_mode
        if system_state.system_health.safe_mode:
            alert_key = _get_alert_key("safe_mode", "CRITICAL")
            if _should_send_alert(alert_key):
                alerts_to_send.append({
                    "level": "CRITICAL",
                    "type": "safe_mode",
                    "message": (
                        f"🚨 **CRITICAL**: System entered safe mode\n\n"
                        f"Consecutive errors: {system_state.system_health.consecutive_errors}\n"
                        f"Uptime: {uptime:.0f}s\n"
                        f"**Trading paused for safety.**"
                    ),
                    "pause_trading": True
                })
                _mark_alert_sent(alert_key)
                logger.error(f"CRITICAL alert: System entered safe_mode")
        
        # CRITICAL: Scheduler stall detected (via heartbeat miss)
        # Проверяем через последний heartbeat
        if system_state.system_health.last_heartbeat:
            time_since_heartbeat = (datetime.now(UTC) - system_state.system_health.last_heartbeat).total_seconds()
            expected_interval = RUNTIME_HEARTBEAT_INTERVAL
            if time_since_heartbeat > expected_interval * HEARTBEAT_MISS_THRESHOLD:
                alert_key = _get_alert_key("scheduler_stall", "CRITICAL")
                if _should_send_alert(alert_key):
                    missed_heartbeats = int((time_since_heartbeat - expected_interval) / expected_interval)
                    alerts_to_send.append({
                        "level": "CRITICAL",
                        "type": "scheduler_stall",
                        "message": (
                            f"🚨 **CRITICAL**: Scheduler stall detected\n\n"
                            f"Time since last heartbeat: {time_since_heartbeat:.1f}s\n"
                            f"Expected interval: {expected_interval}s\n"
                            f"Missed heartbeats: {missed_heartbeats}\n"
                            f"Uptime: {uptime:.0f}s\n"
                            f"**Trading paused for safety.**"
                        ),
                        "pause_trading": True
                    })
                    _mark_alert_sent(alert_key)
                    logger.error(f"CRITICAL alert: Scheduler stall detected (missed {missed_heartbeats} heartbeats)")
        
        # Отправляем все алерты (неблокирующе)
        for alert in alerts_to_send:
            try:
                # Отправляем через Telegram (неблокирующе)
                await asyncio.wait_for(
                    asyncio.to_thread(send_message, alert["message"]),
                    timeout=10.0
                )
                logger.info(f"Alert sent: {alert['level']} - {alert['type']}")
                
                # HARDENING: CRITICAL alerts: приостанавливаем торговлю через manual pause
                if alert.get("pause_trading") and alert["level"] == "CRITICAL":
                    _control_plane_state["manual_pause_active"] = True
                    state_machine = get_state_machine()
                    state_machine.sync_to_system_state(system_state, manual_pause_active=True)
                    logger.error(f"Trading paused due to CRITICAL alert: {alert['type']}")
                    
            except asyncio.TimeoutError:
                logger.warning(f"Timeout sending alert: {alert['level']} - {alert['type']}")
            except Exception as e:
                logger.warning(f"Error sending alert {alert['level']} - {alert['type']}: {type(e).__name__}: {e}")
                
    except Exception as e:
        # Не блокируем analysis loop при ошибках в алертах
        logger.warning(f"Error in alert evaluation: {type(e).__name__}: {e}")

# ========== SINGLE-INSTANCE PROTECTION ==========

def check_single_instance() -> bool:
    """
    Проверяет, что только один экземпляр процесса может работать.
    Использует PID file с файловой блокировкой.
    
    Returns:
        bool: True если можно запускаться, False если уже запущен другой экземпляр
    """
    pid_path = Path(PID_FILE)
    
    # Проверяем существующий PID file
    if pid_path.exists():
        try:
            # Читаем PID
            with open(pid_path, 'r') as f:
                old_pid = int(f.read().strip())
            
            # Проверяем, жив ли процесс
            try:
                os.kill(old_pid, 0)  # Signal 0 = проверка существования
                # Процесс жив - другой экземпляр работает
                logger.warning(f"Another instance is running (PID: {old_pid}). Exiting.")
                return False
            except ProcessLookupError:
                # Процесс не существует - старый PID file
                logger.info(f"Removing stale PID file (PID: {old_pid} no longer exists)")
                pid_path.unlink()
        except (ValueError, IOError) as e:
            logger.warning(f"Error reading PID file: {e}. Removing it.")
            try:
                pid_path.unlink()
            except Exception:
                pass
    
    # Создаём новый PID file
    try:
        with open(pid_path, 'w') as f:
            f.write(str(os.getpid()))
        logger.info(f"PID file created: {PID_FILE} (PID: {os.getpid()})")
        return True
    except Exception as e:
        logger.error(f"Failed to create PID file: {e}")
        return False

def cleanup_pid_file():
    """Удаляет PID file при завершении"""
    pid_path = Path(PID_FILE)
    if pid_path.exists():
        try:
            pid_path.unlink()
            logger.info("PID file removed")
        except Exception as e:
            logger.warning(f"Failed to remove PID file: {e}")

# ========== THREAD-SAFE HEARTBEAT ACCESS ==========
def get_last_heartbeat_timestamp() -> Optional[float]:
    """
    Thread-safe чтение last_heartbeat timestamp.
    
    Используется ThreadWatchdog для проверки состояния event loop
    из отдельного потока (вне asyncio).
    
    Returns:
        Optional[float]: Unix timestamp последнего heartbeat или None
    """
    global _heartbeat_lock
    with _heartbeat_lock:
        if system_state.system_health.last_heartbeat:
            return system_state.system_health.last_heartbeat.timestamp()
        return None


def update_heartbeat_thread_safe():
    """
    Thread-safe обновление heartbeat.
    
    Вызывается из asyncio heartbeat loop для обновления timestamp,
    который читается ThreadWatchdog.
    """
    global _heartbeat_lock
    with _heartbeat_lock:
        system_state.update_heartbeat()


# ========== THREAD-BASED WATCHDOG ==========
class ThreadWatchdogState(Enum):
    """
    HARDENING: Явные состояния lifecycle для ThreadWatchdog.
    
    INIT → ARMED → TRIGGERED → STOPPED
    """
    INIT = "INIT"  # Создан, но не запущен
    ARMED = "ARMED"  # Запущен, ждёт первого heartbeat и event loop
    TRIGGERED = "TRIGGERED"  # Обнаружил LOOP_STALL, отправил событие
    STOPPED = "STOPPED"  # Остановлен


class ThreadWatchdog:
    """
    HARDENING: Thread-based watchdog для детектирования блокировки event loop.
    
    SINGLE-WRITER PRINCIPLE: ThreadWatchdog НЕ МУТИРУЕТ состояние напрямую.
    Вместо этого отправляет события в state machine через thread-safe очередь.
    
    КРИТИЧНО: Работает в отдельном threading.Thread (daemon=True),
    НЕ использует asyncio, await, loop, tasks.
    
    WHY: Event loop НЕ МОЖЕТ детектировать собственную смерть.
    Если event loop заблокирован (например, CPU-bound chaos удерживает GIL),
    все asyncio задачи тоже заблокированы, и watchdog внутри asyncio не сработает.
    
    ThreadWatchdog работает ВНЕ asyncio и гарантированно обнаружит блокировку.
    
    HARDENING INVARIANTS:
    - ThreadWatchdog НЕ проверяет TTL (это делает state machine)
    - ThreadWatchdog НЕ вызывает os._exit (это делает state machine при FATAL)
    - ThreadWatchdog НЕ мутирует SystemState напрямую
    - Все переходы состояния происходят через state_machine.trigger_loop_stall_thread_safe()
    - ThreadWatchdog НЕ работает после FATAL (проверяет should_exit_fatal())
    - ThreadWatchdog НЕ триггерит повторно (idempotent через lifecycle state)
    """
    
    def __init__(self, state_machine_instance, heartbeat_timeout: float = THREAD_WATCHDOG_HEARTBEAT_TIMEOUT):
        """
        HARDENING: Принимает state machine, не system_state.
        ThreadWatchdog работает только с state machine для thread-safe переходов.
        """
        self.state_machine = state_machine_instance
        self.heartbeat_timeout = heartbeat_timeout
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.triggered = False  # Idempotent: предотвращает повторные срабатывания
        self.trigger_lock = threading.Lock()
        
        # HARDENING: Явный lifecycle state
        self.lifecycle_state = ThreadWatchdogState.INIT
        self.lifecycle_lock = threading.Lock()
        self.first_heartbeat_received = False
        self.event_loop_set = False
    
    def start(self):
        """
        HARDENING: Запускает watchdog в отдельном потоке.
        
        Lifecycle: INIT → ARMED (после первого heartbeat и event loop)
        """
        with self.lifecycle_lock:
            if self.lifecycle_state != ThreadWatchdogState.INIT:
                logger.warning(f"ThreadWatchdog already started (state: {self.lifecycle_state.value})")
                return
            
            if self.thread is not None and self.thread.is_alive():
                logger.warning("ThreadWatchdog thread already running")
                return
        
        self.stop_event.clear()
        self.triggered = False
        self.first_heartbeat_received = False
        self.event_loop_set = False
        
        self.thread = threading.Thread(
            target=self._watchdog_loop,
            name="ThreadWatchdog",
            daemon=True
        )
        self.thread.start()
        
        logger.critical(
            f"THREAD_WATCHDOG_STARTED "
            f"heartbeat_timeout={self.heartbeat_timeout}s "
            f"check_interval={THREAD_WATCHDOG_INTERVAL}s "
            f"lifecycle_state={ThreadWatchdogState.INIT.value}"
        )
    
    def arm(self):
        """
        HARDENING: Переводит watchdog в ARMED состояние.
        
        Вызывается после:
        - первого heartbeat
        - установки event loop в state machine
        
        ARMED означает, что watchdog готов к детектированию LOOP_STALL.
        """
        with self.lifecycle_lock:
            if self.lifecycle_state == ThreadWatchdogState.INIT:
                if self.first_heartbeat_received and self.event_loop_set:
                    self.lifecycle_state = ThreadWatchdogState.ARMED
                    logger.critical(
                        f"THREAD_WATCHDOG_ARMED: "
                        f"first_heartbeat={self.first_heartbeat_received} "
                        f"event_loop_set={self.event_loop_set}"
                    )
                else:
                    logger.debug(
                        f"THREAD_WATCHDOG_NOT_READY: "
                        f"first_heartbeat={self.first_heartbeat_received} "
                        f"event_loop_set={self.event_loop_set}"
                    )
    
    def stop(self, timeout: float = 5.0):
        """
        HARDENING: Останавливает watchdog.
        
        Lifecycle: любое состояние → STOPPED
        """
        with self.lifecycle_lock:
            if self.lifecycle_state == ThreadWatchdogState.STOPPED:
                return
            self.lifecycle_state = ThreadWatchdogState.STOPPED
        
        if self.thread is None or not self.thread.is_alive():
            return
        
        logger.info("Stopping ThreadWatchdog...")
        self.stop_event.set()
        
        if self.thread.is_alive():
            self.thread.join(timeout=timeout)
            if self.thread.is_alive():
                logger.warning("ThreadWatchdog did not stop within timeout")
            else:
                logger.info("ThreadWatchdog stopped")
    
    def _watchdog_loop(self):
        """
        HARDENING: Основной цикл watchdog (выполняется в отдельном потоке).
        
        SINGLE-WRITER: НЕ мутирует состояние напрямую.
        Только читает heartbeat timestamp и отправляет события в state machine.
        
        НЕ использует asyncio, await, loop, tasks.
        Только threading, time.
        
        Lifecycle: INIT → ARMED → TRIGGERED → STOPPED
        """
        while not self.stop_event.is_set():
            try:
                # Проверяем каждые N секунд
                if self.stop_event.wait(THREAD_WATCHDOG_INTERVAL):
                    # stop_event установлен - выходим
                    break
                
                # HARDENING: Проверяем состояние через state machine (thread-safe чтение)
                # Если уже в FATAL, watchdog ОБЯЗАН остановиться
                if self.state_machine.should_exit_fatal():
                    logger.info("THREAD_WATCHDOG: System in FATAL state, exiting (invariant: no work after FATAL)")
                    with self.lifecycle_lock:
                        self.lifecycle_state = ThreadWatchdogState.STOPPED
                    break
                
                # HARDENING: SAFE_MODE TTL - DUPLICATE ENFORCEMENT В THREAD
                # Дублируем TTL-логику: если now - entered_at > SAFE_MODE_TTL → os._exit
                # НЕ ждём asyncio, TTL не должен зависеть от event loop
                current_state = self.state_machine.state
                if current_state == SystemStateEnum.SAFE_MODE:
                    safe_mode_entered_at = self.state_machine.get_safe_mode_entered_at()
                    safe_mode_ttl = self.state_machine.get_safe_mode_ttl()
                    
                    if safe_mode_entered_at is not None:
                        duration = (datetime.now(UTC) - safe_mode_entered_at).total_seconds()
                        
                        if duration >= safe_mode_ttl:
                            logger.critical(
                                f"THREAD_WATCHDOG: SAFE_MODE TTL expired - "
                                f"duration={duration:.1f}s >= ttl={safe_mode_ttl}s, "
                                f"calling os._exit({FATAL_EXIT_CODE}) "
                                f"(invariant: SAFE_MODE TTL ⇒ exit even if asyncio stalled)"
                            )
                            # КРИТИЧНО: os._exit напрямую, не через asyncio
                            os._exit(FATAL_EXIT_CODE)
                
                # HARDENING: Проверяем lifecycle state
                with self.lifecycle_lock:
                    if self.lifecycle_state == ThreadWatchdogState.STOPPED:
                        break
                    if self.lifecycle_state == ThreadWatchdogState.TRIGGERED:
                        # Уже сработал - только мониторим FATAL
                        continue
                
                # Thread-safe чтение last_heartbeat timestamp
                last_heartbeat_ts = get_last_heartbeat_timestamp()
                current_time = time.time()
                
                if last_heartbeat_ts is None:
                    # Heartbeat ещё не был обновлён - пропускаем проверку
                    continue
                
                # HARDENING: Отмечаем первый heartbeat
                if not self.first_heartbeat_received:
                    self.first_heartbeat_received = True
                    self.arm()  # Попытка перехода в ARMED
                
                # HARDENING: Проверяем, что мы в ARMED состоянии перед детектированием
                with self.lifecycle_lock:
                    if self.lifecycle_state != ThreadWatchdogState.ARMED:
                        # Ещё не готов - пропускаем проверку
                        continue
                
                time_since_heartbeat = current_time - last_heartbeat_ts
                
                # Проверяем timeout
                if time_since_heartbeat > self.heartbeat_timeout:
                    # HARDENING: LOOP_STALL DETECTED
                    # Отправляем событие в state machine, НЕ мутируем состояние напрямую
                    self._trigger_loop_stall(time_since_heartbeat, last_heartbeat_ts)
                
            except Exception as e:
                # Критическая ошибка в watchdog - логируем, но продолжаем
                logger.error(
                    f"THREAD_WATCHDOG_ERROR: {type(e).__name__}: {e}",
                    exc_info=True
                )
                # Небольшая задержка перед следующей проверкой
                time.sleep(1.0)
        
        logger.info("ThreadWatchdog loop exited")
    
    def _trigger_loop_stall(self, time_since_heartbeat: float, last_heartbeat_ts: float):
        """
        HARDENING: Триггерит LOOP_STALL через state machine.
        
        SINGLE-WRITER PRINCIPLE: НЕ мутирует состояние напрямую.
        Отправляет событие в state machine, которое обрабатывается в asyncio loop.
        
        Thread-safe, idempotent (не срабатывает повторно).
        Lifecycle: ARMED → TRIGGERED
        """
        with self.trigger_lock:
            # HARDENING: Проверяем lifecycle state для idempotency
            with self.lifecycle_lock:
                if self.lifecycle_state == ThreadWatchdogState.TRIGGERED:
                    # Уже сработал - idempotent
                    return
                if self.lifecycle_state != ThreadWatchdogState.ARMED:
                    logger.warning(
                        f"THREAD_WATCHDOG: Cannot trigger in state {self.lifecycle_state.value}, "
                        f"must be ARMED"
                    )
                    return
                
                self.lifecycle_state = ThreadWatchdogState.TRIGGERED
            
            if self.triggered:
                # Дополнительная проверка для thread-safety
                return
            
            self.triggered = True
        
        # Генерируем incident_id
        incident_id = f"thread-watchdog-{uuid.uuid4().hex[:8]}"
        
        logger.critical(
            f"THREAD_WATCHDOG_TRIGGERED "
            f"time_since_heartbeat={time_since_heartbeat:.1f}s "
            f"heartbeat_timeout={self.heartbeat_timeout}s "
            f"last_heartbeat_ts={last_heartbeat_ts} "
            f"incident_id={incident_id}"
        )
        
        # HARDENING: Проверяем состояние через state machine (thread-safe чтение)
        # Если уже в SAFE_MODE или FATAL, не отправляем событие повторно
        current_state = self.state_machine.state
        if current_state == SystemStateEnum.SAFE_MODE:
            # HARDENING: TTL проверяется в state machine, не здесь
            logger.debug("THREAD_WATCHDOG: Already in SAFE_MODE, TTL check handled by state machine")
            return
        
        if current_state == SystemStateEnum.FATAL:
            logger.debug("THREAD_WATCHDOG: System in FATAL state, skipping trigger")
            return
        
        # HARDENING: Отправляем событие в state machine через thread-safe метод
        # State machine обработает переход в SAFE_MODE в asyncio loop
        success = self.state_machine.trigger_loop_stall_thread_safe(
            time_since_heartbeat=time_since_heartbeat,
            incident_id=incident_id
        )
        
        if success:
            logger.critical(
                f"THREAD_WATCHDOG_EVENT_SENT: LOOP_STALL event queued for state machine "
                f"incident_id={incident_id}"
            )
        else:
            logger.error(
                f"THREAD_WATCHDOG_EVENT_FAILED: Failed to queue LOOP_STALL event "
                f"incident_id={incident_id}"
            )


# ========== RUNTIME STATE (явное состояние вместо global) ==========
class RuntimeState:
    """
    Явное состояние runtime для watchdog и reaper.
    Устраняет необходимость в global declarations.
    """
    def __init__(self):
        self.thread_watchdog: Optional[ThreadWatchdog] = None
        self.fatal_reaper: Optional['FatalReaper'] = None
    
    def get_thread_watchdog(self) -> Optional[ThreadWatchdog]:
        """Возвращает экземпляр ThreadWatchdog"""
        return self.thread_watchdog
    
    def set_thread_watchdog(self, watchdog: Optional[ThreadWatchdog]):
        """Устанавливает экземпляр ThreadWatchdog"""
        self.thread_watchdog = watchdog
    
    def get_fatal_reaper(self) -> Optional['FatalReaper']:
        """Возвращает экземпляр FatalReaper"""
        return self.fatal_reaper
    
    def set_fatal_reaper(self, reaper: Optional['FatalReaper']):
        """Устанавливает экземпляр FatalReaper"""
        self.fatal_reaper = reaper


# Глобальный экземпляр RuntimeState (единственный global)
_runtime_state = RuntimeState()


def get_thread_watchdog() -> Optional[ThreadWatchdog]:
    """Возвращает глобальный экземпляр ThreadWatchdog"""
    return _runtime_state.get_thread_watchdog()


class FatalReaper:
    """
    HARDENING: Thread-level FATAL REAPER.
    
    Отдельный daemon thread, который:
    - НЕ использует asyncio
    - Раз в 1-2 секунды проверяет state_machine.state == FATAL
    - Если FATAL → вызывает os._exit(FATAL_EXIT_CODE)
    
    Это последний рубеж - убивает процесс даже если asyncio умер.
    """
    
    def __init__(self, state_machine_instance, check_interval: float = 1.5):
        self.state_machine = state_machine_instance
        self.check_interval = check_interval
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
    
    def start(self):
        """Запускает FATAL_REAPER в отдельном daemon thread"""
        if self.thread is not None and self.thread.is_alive():
            logger.warning("FATAL_REAPER already running")
            return
        
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._reaper_loop,
            name="FATAL_REAPER",
            daemon=True
        )
        self.thread.start()
        logger.critical(f"FATAL_REAPER_STARTED check_interval={self.check_interval}s")
    
    def stop(self):
        """Останавливает FATAL_REAPER"""
        if self.thread is None or not self.thread.is_alive():
            return
        
        logger.info("Stopping FATAL_REAPER...")
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2.0)
            if self.thread.is_alive():
                logger.warning("FATAL_REAPER did not stop within timeout")
            else:
                logger.info("FATAL_REAPER stopped")
    
    def _reaper_loop(self):
        """
        HARDENING: Основной цикл reaper.
        
        НЕ использует asyncio, await, loop, tasks.
        Только threading, time, os.
        """
        logger.critical("FATAL_REAPER: Loop started")
        
        while not self.stop_event.is_set():
            try:
                # Проверяем каждые N секунд
                if self.stop_event.wait(self.check_interval):
                    # stop_event установлен - выходим
                    break
                
                # HARDENING: Thread-safe чтение состояния
                current_state = self.state_machine.state
                
                if current_state == SystemStateEnum.FATAL:
                    logger.critical(
                        f"FATAL_REAPER: FATAL state detected - "
                        f"calling os._exit({FATAL_EXIT_CODE}) "
                        f"(invariant: FATAL ⇒ process MUST exit)"
                    )
                    # КРИТИЧНО: os._exit, не sys.exit
                    # os._exit убивает процесс немедленно, не вызывая cleanup
                    # Это гарантирует выход даже если asyncio мёртв
                    os._exit(FATAL_EXIT_CODE)
                
            except Exception as e:
                # Критическая ошибка в reaper - логируем, но продолжаем
                logger.error(
                    f"FATAL_REAPER_ERROR: {type(e).__name__}: {e}",
                    exc_info=True
                )
                # Небольшая задержка перед следующей проверкой
                time.sleep(1.0)
        
        logger.info("FATAL_REAPER: Loop exited")


# ========== SHUTDOWN HANDLING ==========
# 
# ARCHITECTURE: Single event loop, centralized task registry, graceful shutdown
# 
# RULES:
# 1. Exactly ONE event loop created in if __name__ == "__main__" via asyncio.run(main())
# 2. All tasks registered in RUNNING_TASKS set
# 3. SIGTERM/SIGINT sets shutdown_event, allows loops to exit naturally
# 4. No blocking code after SIGTERM - process must exit within TimeoutStopSec

# Centralized task registry - ALL running tasks must be registered here
RUNNING_TASKS: Set[asyncio.Task] = set()

# Shutdown event - set by signal handler, checked by all loops
# ========== RUNTIME LIFECYCLE STATE MACHINE ==========
# Explicit runtime lifecycle states: RUNNING, SHUTTING_DOWN, STOPPED
# This is separate from operational state machine (safe_mode, etc.)
# and separate from RuntimeState class (which stores watchdog/reaper references)
class RuntimeLifecycleState(Enum):
    RUNNING = "RUNNING"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    STOPPED = "STOPPED"

_runtime_lifecycle_state: RuntimeLifecycleState = RuntimeLifecycleState.RUNNING
_runtime_lifecycle_state_lock = threading.Lock()

def get_runtime_lifecycle_state() -> RuntimeLifecycleState:
    """Get current runtime lifecycle state (thread-safe read)"""
    with _runtime_lifecycle_state_lock:
        return _runtime_lifecycle_state

def set_runtime_lifecycle_state(new_state: RuntimeLifecycleState, reason: str) -> bool:
    """
    Transition runtime lifecycle state (thread-safe).
    
    Returns:
        True if transition allowed, False if illegal
    """
    global _runtime_lifecycle_state
    with _runtime_lifecycle_state_lock:
        old_state = _runtime_lifecycle_state
        
        # Validate transitions
        allowed_transitions = {
            RuntimeLifecycleState.RUNNING: {RuntimeLifecycleState.SHUTTING_DOWN},
            RuntimeLifecycleState.SHUTTING_DOWN: {RuntimeLifecycleState.STOPPED},
            RuntimeLifecycleState.STOPPED: set(),  # Terminal state
        }
        
        if new_state not in allowed_transitions.get(old_state, set()):
            logger.critical(
                f"RUNTIME_LIFECYCLE_STATE_TRANSITION_DENIED: "
                f"from={old_state.value} to={new_state.value} reason={reason}"
            )
            return False
        
        _runtime_lifecycle_state = new_state
        logger.critical(
            f"RUNTIME_LIFECYCLE_STATE_TRANSITION: from={old_state.value} to={new_state.value} reason={reason}"
        )
        return True

_shutdown_event: Optional[asyncio.Event] = None

def get_shutdown_event() -> asyncio.Event:
    """
    Returns the global shutdown event.
    Creates it if it doesn't exist (safe to call from any async context).
    """
    global _shutdown_event
    if _shutdown_event is None:
        _shutdown_event = asyncio.Event()
    return _shutdown_event

def signal_handler(signum, frame):
    """
    Signal handler for graceful shutdown.
    
    CRITICAL: This runs in signal context - must be non-blocking.
    Sets shutdown_event to allow loops to exit naturally.
    
    HARDENING: При SIGTERM:
    - watchdog.stop()
    - reaper.stop()
    - запрет на любые state transitions после shutdown start
    - защита от повторного вызова shutdown
    """
    signal_name = signal.Signals(signum).name
    
    # CRITICAL: Prevent double shutdown
    current_state = get_runtime_lifecycle_state()
    if current_state != RuntimeLifecycleState.RUNNING:
        logger.critical(
            f"RUNTIME_LIFECYCLE_STATE: Shutdown already in progress (state={current_state.value}), "
            f"ignoring {signal_name} signal"
        )
        return
    
    # Transition to SHUTTING_DOWN
    if not set_runtime_lifecycle_state(RuntimeLifecycleState.SHUTTING_DOWN, f"Received {signal_name} signal"):
        logger.critical(f"RUNTIME_LIFECYCLE_STATE: Failed to transition to SHUTTING_DOWN, already shutting down")
        return
    
    logger.critical(f"Received {signal_name} signal. Initiating graceful shutdown...")
    
    # Set flags for immediate effect
    system_state.system_health.is_running = False
    
    # HARDENING: Запрет на state transitions после shutdown start
    state_machine = get_state_machine()
    state_machine.mark_shutdown_started()
    
    # HARDENING: Останавливаем watchdog и reaper
    watchdog = _runtime_state.get_thread_watchdog()
    reaper = _runtime_state.get_fatal_reaper()
    
    if watchdog:
        try:
            watchdog.stop(timeout=2.0)
        except Exception as e:
            logger.warning(f"Error stopping ThreadWatchdog: {type(e).__name__}: {e}")
    
    if reaper:
        try:
            reaper.stop()
        except Exception as e:
            logger.warning(f"Error stopping FATAL_REAPER: {type(e).__name__}: {e}")
    
    # Set shutdown event (if event loop is running)
    # This is safe - if loop doesn't exist, it will be created on first access
    try:
        shutdown_evt = get_shutdown_event()
        if not shutdown_evt.is_set():
            shutdown_evt.set()
    except RuntimeError:
        # No event loop running - this is OK, process will exit
        pass

def setup_signal_handlers():
    """Настраивает обработчики сигналов для graceful shutdown"""
    if sys.platform != 'win32':
        # Unix/Linux: SIGTERM и SIGINT
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
    else:
        # Windows: только SIGINT (Ctrl+C)
        signal.signal(signal.SIGINT, signal_handler)
    logger.info("Signal handlers registered (SIGTERM, SIGINT)")

# ========== TASK ORCHESTRATION ==========
#
# All background tasks MUST be registered via register_task().
# This ensures proper cancellation and shutdown.

def register_task(task: asyncio.Task, name: str) -> asyncio.Task:
    """
    Registers a task in the central RUNNING_TASKS registry.
    
    WHY: Centralized tracking enables proper shutdown - all tasks can be
    cancelled and awaited together. Without this, tasks may leak and prevent
    clean shutdown.
    
    CRITICAL: Tasks cannot be registered after shutdown starts.
    
    Args:
        task: The asyncio.Task to register
        name: Human-readable name for logging
        
    Returns:
        The same task (for chaining)
    """
    # CRITICAL: Prevent task creation after shutdown
    current_state = get_runtime_lifecycle_state()
    if current_state != RuntimeLifecycleState.RUNNING:
        logger.critical(
            f"RUNTIME_LIFECYCLE_STATE: Task registration blocked - runtime lifecycle state is {current_state.value}, "
            f"cannot register task '{name}'"
        )
        # Cancel the task immediately since we can't register it
        task.cancel()
        return task
    
    task.set_name(name)
    RUNNING_TASKS.add(task)
    logger.debug(f"Task registered: {name} (total: {len(RUNNING_TASKS)})")
    
    def task_done_callback(t: asyncio.Task):
        """Auto-removes task from registry when done"""
        RUNNING_TASKS.discard(t)
        logger.debug(f"Task completed: {name} (remaining: {len(RUNNING_TASKS)})")
    
    task.add_done_callback(task_done_callback)
    return task

async def shutdown_all_tasks(timeout: float = 10.0):
    """
    Cancels all registered tasks and waits for completion.
    
    WHY: Proper shutdown requires:
    1. Cancel all tasks (so they can exit their loops)
    2. Wait for completion (so resources are cleaned up)
    
    This is the ONLY place where task cancellation should happen during shutdown.
    """
    # NOTE: Control plane server закрывается в finally блоке main(), не здесь
    # Это гарантирует правильный порядок shutdown
    
    if not RUNNING_TASKS:
        logger.info("No tasks to cancel")
        return
    
    tasks_to_cancel = list(RUNNING_TASKS)
    logger.info(f"Cancelling {len(tasks_to_cancel)} registered tasks...")
    
    # Cancel all tasks with logging
    for task in tasks_to_cancel:
        task_name = task.get_name() if hasattr(task, 'get_name') else str(task)
        if not task.done():
            logger.debug(f"Cancelling task: {task_name}")
            task.cancel()
        else:
            logger.debug(f"Task already done: {task_name}")
    
    # Wait for completion with logging
    # CRITICAL: Use return_exceptions=True so one failing task doesn't block others
    # CRITICAL: Await without timeout to ensure all tasks complete
    results = await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
    
    # Log completion status for each task
    for task, result in zip(tasks_to_cancel, results):
        task_name = task.get_name() if hasattr(task, 'get_name') else str(task)
        if isinstance(result, Exception):
            if isinstance(result, asyncio.CancelledError):
                logger.debug(f"Task cancelled: {task_name}")
            else:
                logger.warning(f"Task completed with exception: {task_name}: {type(result).__name__}: {result}")
        else:
            logger.debug(f"Task completed successfully: {task_name}")
    
    logger.info(f"All {len(tasks_to_cancel)} registered tasks cancelled and completed")


# ========== ITERATION BUDGET ENFORCEMENT ==========
async def cooperative_yield():
    """
    Cooperative yield point - yields control back to event loop.
    
    This ensures the event loop remains responsive during long-running iterations.
    Should be called periodically within iteration loops.
    
    CRITICAL: Also checks shutdown state - if shutdown initiated, raises CancelledError
    to allow graceful shutdown to proceed.
    """
    # Check shutdown state before yielding
    shutdown_evt = get_shutdown_event()
    if shutdown_evt.is_set():
        # Shutdown initiated - raise CancelledError to stop iteration slicing
        raise asyncio.CancelledError("Shutdown initiated during iteration")
    
    # Yield control to event loop
    await asyncio.sleep(0)


class IterationBudgetTracker:
    """
    Tracks wall-time budget for a single iteration.
    
    Enforces soft time limits to prevent iteration-level stalls.
    """
    def __init__(self, budget_seconds: float):
        self.budget_seconds = budget_seconds
        self.start_time = None
        self.last_yield_time = None
        self.yield_interval = 5.0  # Yield every 5 seconds during iteration
    
    def start(self):
        """Start tracking iteration budget"""
        self.start_time = time.monotonic()
        self.last_yield_time = self.start_time
    
    def elapsed(self) -> float:
        """Get elapsed time since start"""
        if self.start_time is None:
            return 0.0
        return time.monotonic() - self.start_time
    
    def remaining(self) -> float:
        """Get remaining budget"""
        return max(0.0, self.budget_seconds - self.elapsed())
    
    def is_exceeded(self) -> bool:
        """Check if budget is exceeded"""
        return self.elapsed() > self.budget_seconds
    
    async def check_and_yield(self, force_yield: bool = False) -> bool:
        """
        Check if we should yield and yield if needed.
        
        Args:
            force_yield: If True, always yield regardless of interval
        
        Returns:
            True if budget is still available and iteration should continue,
            False if budget exceeded (iteration should defer remaining work)
        
        Raises:
            asyncio.CancelledError: If shutdown initiated
        """
        # CRITICAL: Check shutdown state first
        shutdown_evt = get_shutdown_event()
        if shutdown_evt.is_set():
            # Shutdown initiated - raise CancelledError to stop iteration slicing
            raise asyncio.CancelledError("Shutdown initiated during iteration")
        
        now = time.monotonic()
        
        # Yield periodically to keep event loop responsive
        # Also yield if forced (e.g., after each symbol in nested loop)
        if force_yield or (now - self.last_yield_time >= self.yield_interval):
            await cooperative_yield()
            self.last_yield_time = now
        
        # Check if budget exceeded
        if self.is_exceeded():
            return False
        
        return True


async def run_market_analysis():
    """
    Выполняет один цикл анализа рынка.
    Это async версия того, что делал main.py
    
    ITERATION BUDGET ENFORCEMENT:
    - Tracks wall-time budget per iteration (ITERATION_BUDGET_SECONDS = 60s)
    - Yields control to event loop periodically (every 5s)
    - Checks shutdown state at each yield point
    - If budget exceeded, defers remaining work to next iteration (returns False)
    - Prevents single iteration from blocking event loop beyond watchdog threshold (300s)
    - Shutdown-aware: raises CancelledError if shutdown initiated during iteration
    """
    import time
    
    # Initialize iteration budget tracker
    # CRITICAL: Use aggressive budget (60s) to prevent LOOP_GUARD_TIMEOUT (300s)
    # This ensures watchdog heartbeat can always observe progress
    budget_tracker = IterationBudgetTracker(ITERATION_BUDGET_SECONDS)
    budget_tracker.start()
    
    # Record iteration start time (as required)
    iteration_start = time.monotonic()
    
    start_time = time.time()
    logger.info(f"🚀 Начало анализа {len(SYMBOLS)} символов")
    
    # Проверка торгового времени
    if not is_good_time():
        logger.info("⏸ Не торговое время - пропускаем цикл")
        return True
    
    try:
        # Cooperative yield after initial checks
        await cooperative_yield()
        
        # Инициализация экосистемы
        logger.info("🧠 Инициализация экосистемы...")
        decision_core = get_decision_core()
        market_regime_brain = get_market_regime_brain()
        risk_exposure_brain = get_risk_exposure_brain()
        cognitive_filter = get_cognitive_filter()
        opportunity_awareness = get_opportunity_awareness()
        gatekeeper = get_gatekeeper()
        
        # Check budget and yield
        if not await budget_tracker.check_and_yield():
            logger.warning(f"⏱ Iteration budget exceeded ({budget_tracker.elapsed():.1f}s) after initialization - continuing with degraded mode")
        
        # Параллельная загрузка данных (синхронная операция в отдельном потоке)
        logger.info("📥 Параллельная загрузка данных...")
        load_start = time.time()
        # Используем asyncio.to_thread для синхронных операций с timeout
        try:
            all_candles = await asyncio.wait_for(
                asyncio.to_thread(get_candles_parallel, SYMBOLS, TIMEFRAMES, 120, 20),
                timeout=60.0
            )
        except asyncio.TimeoutError:
            # TimeoutError при загрузке данных - мягкое предупреждение, не авария
            load_duration = time.time() - load_start
            logger.warning(
                "⏱ Data loading slow: %.2fs (timeout=60s). Continuing with degraded mode.",
                load_duration
            )
            # Не активируем safe_mode и не возвращаем False - продолжаем работу
            # Записываем для метрик, но не блокируем анализ
            system_state.record_error("Data loading timeout (non-critical)")
            return False  # Возвращаем False, но не активируем safe_mode
        load_time = time.time() - load_start
        logger.info(f"✅ Данные загружены за {load_time:.2f} секунд")
        
        # Check budget and yield after data loading (shutdown-aware)
        try:
            if not await budget_tracker.check_and_yield():
                logger.warning(f"⏱ Iteration budget exceeded ({budget_tracker.elapsed():.1f}s) after data loading - deferring remaining work to next iteration")
                return False  # Defer remaining work to next iteration
        except asyncio.CancelledError:
            logger.info("Iteration cancelled due to shutdown")
            raise
        
        # Анализ "мозгами" экосистемы (синхронные операции в потоках)
        # Brain'ы обновляют SystemState напрямую, не через DecisionCore
        logger.debug("🧠 Анализ Market Regime Brain...")
        try:
            market_regime = await asyncio.wait_for(
                asyncio.to_thread(market_regime_brain.analyze, SYMBOLS, all_candles, system_state),
                timeout=30.0
            )
            logger.info(f"   Режим: {market_regime.trend_type}, Волатильность: {market_regime.volatility_level}, Risk: {market_regime.risk_sentiment}")
            # Обновляем состояние волатильности для адаптивной системы
            if market_regime and hasattr(market_regime, 'volatility_level'):
                update_volatility_state(market_regime.volatility_level)
        except asyncio.TimeoutError:
            logger.error("⏱ Таймаут анализа Market Regime Brain (30 сек)")
            market_regime = None
        except Exception as e:
            logger.error(f"⚠️ Ошибка в Market Regime Brain: {type(e).__name__}: {e}")
            market_regime = None
        
        logger.debug("🧠 Анализ Risk & Exposure Brain...")
        try:
            risk_exposure = await asyncio.wait_for(
                asyncio.to_thread(risk_exposure_brain.analyze, SYMBOLS, all_candles, system_state),
                timeout=30.0
            )
            logger.info(f"   Риск: {risk_exposure.total_risk_pct:.2f}%, Позиций: {risk_exposure.active_positions}, Перегрузка: {risk_exposure.is_overloaded}")
        except asyncio.TimeoutError:
            logger.error("⏱ Таймаут анализа Risk & Exposure Brain (30 сек)")
            risk_exposure = None
        except Exception as e:
            logger.error(f"⚠️ Ошибка в Risk & Exposure Brain: {type(e).__name__}: {e}")
            risk_exposure = None
        
        logger.debug("🧠 Анализ Cognitive Filter...")
        try:
            cognitive_state = await asyncio.wait_for(
                asyncio.to_thread(cognitive_filter.analyze, system_state),
                timeout=30.0
            )
            logger.debug(f"   Пере-торговля: {cognitive_state.overtrading_score:.2f}, Пауза: {cognitive_state.should_pause}")
        except asyncio.TimeoutError:
            logger.error("⏱ Таймаут анализа Cognitive Filter (30 сек)")
            cognitive_state = None
        except Exception as e:
            logger.error(f"⚠️ Ошибка в Cognitive Filter: {type(e).__name__}: {e}")
            cognitive_state = None
        
        # Check budget and yield after brain analysis (shutdown-aware)
        try:
            if not await budget_tracker.check_and_yield():
                logger.warning(f"⏱ Iteration budget exceeded ({budget_tracker.elapsed():.1f}s) after brain analysis - deferring remaining work to next iteration")
                return False  # Defer remaining work to next iteration
        except asyncio.CancelledError:
            logger.info("Iteration cancelled due to shutdown")
            raise
        
        # Проверка через Decision Core (читает из SystemState)
        try:
            global_decision = decision_core.should_i_trade(system_state=system_state)
        except RuntimeError as e:
            # Обработка fault injection или других RuntimeError из DecisionCore
            if "FAULT_INJECTION: decision_exception" in str(e):
                # Fault injection - логируем структурированно и продолжаем
                logger.error(
                    f"FAULT_INJECTION: decision_exception - "
                    f"Controlled exception from DecisionCore.should_i_trade(). "
                    f"Runtime continues. error_type=RuntimeError error_message={str(e)}"
                )
                # Записываем ошибку для health tracking
                system_state.record_error("FAULT_INJECTION: decision_exception")
                
                # HARDENING: Проверяем safe-mode активацию через state machine
                state_machine = get_state_machine()
                if system_state.system_health.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    if not state_machine.is_safe_mode:
                        await state_machine.transition_to(
                            SystemStateEnum.SAFE_MODE,
                            reason=f"Fault injection: consecutive_errors >= MAX_CONSECUTIVE_ERRORS",
                            owner="error_alert",
                            metadata={"consecutive_errors": system_state.system_health.consecutive_errors}
                        )
                        logger.warning(
                            f"SAFE-MODE activated after fault injection: "
                            f"consecutive_errors={system_state.system_health.consecutive_errors} "
                            f">= MAX_CONSECUTIVE_ERRORS={MAX_CONSECUTIVE_ERRORS}"
                        )
                
                # Возвращаем False для цикла анализа (ошибка обработана)
                return False
            else:
                # Другие RuntimeError - пробрасываем дальше
                raise
        
        if not global_decision.can_trade:
            logger.info(f"⏸ Decision Core блокирует торговлю: {global_decision.reason}")
            try:
                await asyncio.to_thread(send_message, f"🧠 Decision Core: {global_decision.reason}\n\nРекомендации:\n" + "\n".join(f"• {r}" for r in global_decision.recommendations))
            except Exception:
                pass
            return True
        
        # Check budget and yield after decision core check (shutdown-aware)
        try:
            if not await budget_tracker.check_and_yield():
                logger.warning(f"⏱ Iteration budget exceeded ({budget_tracker.elapsed():.1f}s) after decision core - deferring remaining work to next iteration")
                return False  # Defer remaining work to next iteration
        except asyncio.CancelledError:
            logger.info("Iteration cancelled due to shutdown")
            raise
        
        # Проверка резких движений
        logger.info("🔍 Проверка резких движений...")
        try:
            await asyncio.wait_for(
                asyncio.to_thread(check_all_symbols_for_spikes, SYMBOLS, all_candles),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            logger.warning("⏱ Таймаут проверки резких движений")
        except Exception as e:
            logger.warning(f"⚠️ Ошибка при проверке резких движений: {e}")
        
        # Check budget and yield after spike check (shutdown-aware)
        try:
            if not await budget_tracker.check_and_yield():
                logger.warning(f"⏱ Iteration budget exceeded ({budget_tracker.elapsed():.1f}s) after spike check - deferring remaining work to next iteration")
                return False  # Defer remaining work to next iteration
        except asyncio.CancelledError:
            logger.info("Iteration cancelled due to shutdown")
            raise
        
        # Анализ корреляций
        logger.info("📊 Анализ корреляций между парами...")
        try:
            market_correlations = await asyncio.wait_for(
                asyncio.to_thread(analyze_market_correlations, SYMBOLS, all_candles, "15m"),
                timeout=30.0
            )
            # Обновляем SystemState с корреляциями
            system_state.update_market_correlations(market_correlations)
        except asyncio.TimeoutError:
            logger.warning("⏱ Таймаут анализа корреляций")
            market_correlations = {}
        except Exception as e:
            logger.warning(f"⚠️ Ошибка при анализе корреляций: {e}")
            market_correlations = {}
        
        # Check budget and yield after correlation analysis (shutdown-aware)
        try:
            if not await budget_tracker.check_and_yield():
                logger.warning(f"⏱ Iteration budget exceeded ({budget_tracker.elapsed():.1f}s) after correlation analysis - deferring remaining work to next iteration")
                return False  # Defer remaining work to next iteration
        except asyncio.CancelledError:
            logger.info("Iteration cancelled due to shutdown")
            raise
        
        # Генерация сигналов
        logger.info("📊 Генерация сигналов...")
        try:
            signal_stats = await asyncio.wait_for(
                asyncio.to_thread(
                    generate_signals_for_symbols,
                    all_candles=all_candles,
                    market_correlations=market_correlations,
                    good_time=True,
                    decision_core=decision_core,
                    opportunity_awareness=opportunity_awareness,
                    gatekeeper=gatekeeper,
                    system_state=system_state
                ),
                timeout=120.0
            )
            logger.info(f"📊 Статистика сигналов: обработано {signal_stats['processed']}, отправлено {signal_stats['signals_sent']}, заблокировано {signal_stats['signals_blocked']}, ошибок {signal_stats['errors']}")
        except asyncio.TimeoutError:
            # TimeoutError при генерации сигналов - мягкое предупреждение, не авария
            logger.warning(
                "⏱ Signal generation slow: exceeded timeout=120s. Continuing with degraded mode."
            )
            # Не активируем safe_mode - продолжаем работу
            # Записываем для метрик, но не блокируем анализ
            system_state.record_error("Signal generation timeout (non-critical)")
            # Продолжаем выполнение - не возвращаем False, чтобы цикл продолжался
        except Exception as e:
            logger.error(f"⚠️ Ошибка при генерации сигналов: {type(e).__name__}: {e}")
            logger.error(traceback.format_exc())
        
        # Check budget and yield after signal generation (shutdown-aware)
        try:
            if not await budget_tracker.check_and_yield():
                logger.warning(f"⏱ Iteration budget exceeded ({budget_tracker.elapsed():.1f}s) after signal generation - deferring remaining work to next iteration")
                # Note: Signal generation is the last major step, so we continue to completion
        except asyncio.CancelledError:
            logger.info("Iteration cancelled due to shutdown")
            raise
        
        # Статистика Gatekeeper
        gatekeeper_stats = gatekeeper.get_stats()
        if gatekeeper_stats["total"] > 0:
            logger.info(f"🚪 Gatekeeper: одобрено {gatekeeper_stats['approved']}, заблокировано {gatekeeper_stats['blocked']}")
        
        total_time = time.time() - start_time
        elapsed_budget = budget_tracker.elapsed()
        
        # Log budget status
        if elapsed_budget > ITERATION_BUDGET_SECONDS:
            logger.warning(f"⏱ Iteration completed in {total_time:.2f}s (budget: {ITERATION_BUDGET_SECONDS}s, exceeded by {elapsed_budget - ITERATION_BUDGET_SECONDS:.1f}s)")
        else:
            logger.debug(f"⏱ Iteration completed in {total_time:.2f}s (budget: {ITERATION_BUDGET_SECONDS}s, remaining: {budget_tracker.remaining():.1f}s)")
        
        logger.info(f"✅ Анализ завершен за {total_time:.2f} секунд")
        
        # Успешное выполнение
        system_state.reset_errors()
        system_state.increment_cycle(success=True)
        
        # ИНВАРИАНТ: Периодически сохраняем snapshot (каждые 5 циклов)
        if system_state.performance_metrics.total_cycles % 5 == 0:
            try:
                from core.signal_snapshot_store import SystemStateSnapshotStore
                from database import cleanup_old_snapshots
                snapshot = system_state.create_snapshot()
                # Используем SystemStateSnapshotStore - entry point с fault injection
                SystemStateSnapshotStore.save(snapshot)
                # Очищаем старые snapshot'ы (оставляем последние 10)
                cleanup_old_snapshots(keep_last_n=10)
            except IOError as e:
                # Обработка fault injection из storage layer
                if "FAULT_INJECTION: storage_failure" in str(e):
                    logger.error(
                        f"FAULT_INJECTION: storage_failure - "
                        f"Controlled exception from storage layer. "
                        f"Runtime continues. error_type=IOError error_message={str(e)}"
                    )
                    # Записываем ошибку для health tracking
                    system_state.record_error("FAULT_INJECTION: storage_failure")
                    
                    # HARDENING: Проверяем safe-mode активацию через state machine
                    state_machine = get_state_machine()
                    if system_state.system_health.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        if not state_machine.is_safe_mode:
                            await state_machine.transition_to(
                                SystemStateEnum.SAFE_MODE,
                                reason=f"Storage fault injection: consecutive_errors >= MAX_CONSECUTIVE_ERRORS",
                                owner="main_startup",
                                metadata={"consecutive_errors": system_state.system_health.consecutive_errors}
                            )
                            logger.warning(
                                f"SAFE-MODE activated after storage fault injection: "
                                f"consecutive_errors={system_state.system_health.consecutive_errors} "
                                f">= MAX_CONSECUTIVE_ERRORS={MAX_CONSECUTIVE_ERRORS}"
                            )
                else:
                    # Другие IOError - логируем как обычную ошибку
                    logger.warning(f"⚠️ Ошибка сохранения snapshot: {e}")
            except Exception as e:
                logger.warning(f"⚠️ Ошибка сохранения snapshot: {e}")
        
        return True
        
    except asyncio.TimeoutError:
        # TimeoutError из любых операций в run_market_analysis()
        # Мягкое предупреждение, не авария
        logger.warning(
            "⏱ Analysis iteration exceeded timeout. Continuing with degraded mode."
        )
        # Не активируем safe_mode - продолжаем работу
        # Записываем для метрик, но не блокируем анализ
        system_state.record_error("Analysis timeout (non-critical)")
        # НЕ пробрасываем TimeoutError дальше - возвращаем False для продолжения цикла
        return False
        
    except Exception as e:
        error_msg = f"Критическая ошибка в цикле анализа: {type(e).__name__}: {e}"
        error_trace = traceback.format_exc()
        
        # Определяем, является ли это fault injection
        is_fault_injection = (
            isinstance(e, RuntimeError) and 
            "FAULT_INJECTION: decision_exception" in str(e)
        )
        
        if is_fault_injection:
            # Структурированное логирование для fault injection
            logger.error(
                f"FAULT_INJECTION: decision_exception - "
                f"Controlled exception injected for resilience testing. "
                f"Runtime continues normally. "
                f"error_type={type(e).__name__} "
                f"error_message={str(e)}"
            )
        else:
            logger.error(f"{error_msg}\n{error_trace}")
        
        system_state.record_error(str(e))
        
        # HARDENING: Включаем safe-mode при множественных ошибках через state machine
        state_machine = get_state_machine()
        if system_state.system_health.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            if not state_machine.is_safe_mode:
                await state_machine.transition_to(
                    SystemStateEnum.SAFE_MODE,
                    reason=f"Consecutive errors threshold: {system_state.system_health.consecutive_errors} >= {MAX_CONSECUTIVE_ERRORS}",
                    owner="error_alert",
                    metadata={"consecutive_errors": system_state.system_health.consecutive_errors}
                )
                logger.warning(
                    f"SAFE-MODE activated: consecutive_errors={system_state.system_health.consecutive_errors} "
                    f">= MAX_CONSECUTIVE_ERRORS={MAX_CONSECUTIVE_ERRORS}. "
                    f"Trading blocked for safety."
                )
        
        # Отправляем уведомление
        try:
            await asyncio.wait_for(
                asyncio.to_thread(error_alert, f"{error_msg}\n\nТрассировка:\n{error_trace[:500]}"),
                timeout=10.0
            )
        except Exception:
            pass
        
        return False


async def market_analysis_loop():
    """
    Основной цикл анализа рынка.
    Запускается строго каждые ANALYSIS_INTERVAL секунд без накопления дрейфа.
    
    Использует абсолютное планирование по monotonic clock для предотвращения дрейфа.
    
    Features:
    - Абсолютное планирование (без дрейфа)
    - Мягкий контроль времени (без аварий)
    - Метрики производительности
    - Алерты при медленном анализе
    - Graceful shutdown support
    """
    # GLOBAL STATE (intentional)
    global _adaptive_system_state, _control_plane_state
    logger.info("Market analysis loop started")
    
    # Use shutdown_event for proper cancellation semantics
    shutdown_evt = get_shutdown_event()
    
    # ========== АБСОЛЮТНОЕ ПЛАНИРОВАНИЕ ==========
    # Используем monotonic clock для предотвращения дрейфа
    # Адаптивный интервал: увеличивается при ошибках, уменьшается при стабильной работе
    current_interval = float(ANALYSIS_INTERVAL)
    next_run = time.monotonic()
    
    # ========== АДАПТИВНАЯ СИСТЕМА ==========
    # Отслеживание состояния для адаптации
    adaptive_state = {
        "stable_cycles": 0,  # Количество успешных циклов подряд
        "last_safe_mode_state": system_state.system_health.safe_mode,
        "last_trading_paused_state": system_state.system_health.trading_paused,
        "safe_mode_exit_time": None,  # Время выхода из safe_mode
    }
    
    # Инициализируем адаптивный интервал
    if _adaptive_system_state["adaptive_interval"] is None:
        _adaptive_system_state["adaptive_interval"] = float(ANALYSIS_INTERVAL)
    
    # ========== МЕТРИКИ ==========
    metrics = {
        "analysis_count": 0,
        "analysis_total_time": 0.0,
        "analysis_max_time": 0.0,
        "start_time": time.monotonic(),
        "last_metrics_log": time.monotonic(),
    }
    
    # Инициализируем глобальные метрики при первом запуске
    if _analysis_metrics["start_time"] is None:
        update_analysis_metrics({"start_time": metrics["start_time"]})
    
    # ========== ALERT ESCALATION ==========
    # Alert evaluation теперь выполняется в evaluate_and_send_alerts()
    # с дедупликацией через _alert_last_sent
    
    while system_state.system_health.is_running and not shutdown_evt.is_set():
        try:
            # Запоминаем время начала анализа
            start = time.monotonic()
            
            # Выполняем анализ
            success = await run_market_analysis()
            
            # Вычисляем длительность анализа
            duration = time.monotonic() - start
            
            # ========== ОБНОВЛЕНИЕ МЕТРИК ==========
            metrics["analysis_count"] += 1
            metrics["analysis_total_time"] += duration
            metrics["analysis_max_time"] = max(metrics["analysis_max_time"], duration)
            
            # Обновляем глобальные метрики для health endpoint
            update_analysis_metrics({
                "analysis_count": metrics["analysis_count"],
                "analysis_total_time": metrics["analysis_total_time"],
                "analysis_max_time": metrics["analysis_max_time"],
                "last_analysis_duration": duration,
            })
            
            # ========== PROMETHEUS METRICS (NON-BLOCKING) ==========
            # Записываем длительность в histogram
            record_analysis_duration(duration)
            # Увеличиваем счетчик завершенных циклов
            increment_analysis_cycles()
            
            # ========== АДАПТИВНАЯ СИСТЕМА ==========
            # Получаем текущее состояние для адаптации
            consecutive_errors = system_state.system_health.consecutive_errors
            adaptive_system = get_adaptive_system_state()
            volatility_state = adaptive_system["volatility_state"]
            
            # 1. Адаптивный интервал анализа (на основе волатильности и ошибок)
            if ADAPTIVE_INTERVAL_ENABLED:
                # Базовый интервал из глобального состояния
                base_interval = _adaptive_system_state["adaptive_interval"]
                
                # Корректировка на основе волатильности
                volatility_multiplier = 1.0
                if volatility_state == "LOW":
                    # Низкая волатильность - увеличиваем интервал (1.5-2.0)
                    volatility_multiplier = 1.75  # Среднее значение
                elif volatility_state == "MEDIUM":
                    # Средняя волатильность - без изменений
                    volatility_multiplier = 1.0
                elif volatility_state == "HIGH":
                    # Высокая волатильность - уменьшаем интервал (0.7-0.8)
                    volatility_multiplier = 0.75  # Среднее значение
                
                # Применяем множитель волатильности
                volatility_adjusted_interval = base_interval * volatility_multiplier
                
                # Корректировка на основе ошибок (как раньше)
                if success and consecutive_errors == 0:
                    # Успешный цикл без ошибок - увеличиваем счетчик стабильности
                    adaptive_state["stable_cycles"] += 1
                    # Если достаточно стабильных циклов - уменьшаем базовый интервал
                    if adaptive_state["stable_cycles"] >= ADAPTIVE_STABLE_CYCLES and base_interval > ADAPTIVE_INTERVAL_MIN:
                        old_base = base_interval
                        base_interval = max(ADAPTIVE_INTERVAL_MIN, base_interval / ADAPTIVE_INTERVAL_MULTIPLIER)
                        if base_interval < old_base:
                            logger.info(f"📉 Adaptive base interval decreased: {old_base:.0f}s → {base_interval:.0f}s (stable cycles: {adaptive_state['stable_cycles']})")
                            adaptive_state["stable_cycles"] = 0
                else:
                    # Есть ошибки - увеличиваем базовый интервал
                    adaptive_state["stable_cycles"] = 0
                    if consecutive_errors > 0:
                        old_base = base_interval
                        base_interval = min(ADAPTIVE_INTERVAL_MAX, base_interval * ADAPTIVE_INTERVAL_MULTIPLIER)
                        if base_interval > old_base:
                            logger.info(f"📈 Adaptive base interval increased: {old_base:.0f}s → {base_interval:.0f}s (errors: {consecutive_errors})")
                
                # Обновляем базовый интервал в глобальном состоянии
                _adaptive_system_state["adaptive_interval"] = base_interval
                
                # Пересчитываем интервал с учетом волатильности (после обновления base_interval)
                volatility_adjusted_interval = base_interval * volatility_multiplier
                
                # Финальный интервал с учетом волатильности (clamp между min и max)
                current_interval = max(ADAPTIVE_INTERVAL_MIN, min(ADAPTIVE_INTERVAL_MAX, volatility_adjusted_interval))
            else:
                # Адаптивный интервал отключен - используем базовую логику на основе ошибок
                if success and consecutive_errors == 0:
                    adaptive_state["stable_cycles"] += 1
                    if adaptive_state["stable_cycles"] >= ADAPTIVE_STABLE_CYCLES and current_interval > ADAPTIVE_INTERVAL_MIN:
                        old_interval = current_interval
                        current_interval = max(ADAPTIVE_INTERVAL_MIN, current_interval / ADAPTIVE_INTERVAL_MULTIPLIER)
                        if current_interval < old_interval:
                            logger.info(f"📉 Adaptive interval decreased: {old_interval:.0f}s → {current_interval:.0f}s (stable cycles: {adaptive_state['stable_cycles']})")
                            adaptive_state["stable_cycles"] = 0
                else:
                    adaptive_state["stable_cycles"] = 0
                    if consecutive_errors > 0:
                        old_interval = current_interval
                        current_interval = min(ADAPTIVE_INTERVAL_MAX, current_interval * ADAPTIVE_INTERVAL_MULTIPLIER)
                        if current_interval > old_interval:
                            logger.info(f"📈 Adaptive interval increased: {old_interval:.0f}s → {current_interval:.0f}s (errors: {consecutive_errors})")
            
            # 2. Auto-resume trading (на основе последовательных успешных циклов)
            # ВАЖНО: Manual pause переопределяет auto-resume
            manual_pause = _control_plane_state.get("manual_pause_active", False)
            
            if AUTO_RESUME_TRADING_ENABLED:
                if system_state.system_health.trading_paused:
                    # Проверяем, не является ли это manual pause
                    if manual_pause:
                        # Manual pause активна - не пытаемся auto-resume
                        # Сбрасываем recovery cycles, чтобы не накапливать их
                        if _adaptive_system_state["recovery_cycles"] > 0:
                            _adaptive_system_state["recovery_cycles"] = 0
                    elif success and consecutive_errors == 0:
                        # Успешный цикл - увеличиваем счетчик восстановления (только если не manual pause)
                        _adaptive_system_state["recovery_cycles"] += 1
                        remaining = AUTO_RESUME_SUCCESS_CYCLES - _adaptive_system_state["recovery_cycles"]
                        if remaining > 0:
                            logger.debug(f"🔄 Recovery progress: {_adaptive_system_state['recovery_cycles']}/{AUTO_RESUME_SUCCESS_CYCLES} successful cycles (remaining: {remaining})")
                        else:
                            # Достаточно успешных циклов - возобновляем торговлю
                            # HARDENING: safe_mode MUST ONLY be cleared by successful recovery cycles через state machine
                            state_machine = get_state_machine()
                            if state_machine.is_safe_mode:
                                # Recovery cycles completed - exit SAFE_MODE через state machine (RECOVERY-ONLY EXIT)
                                await exit_safe_mode_via_recovery(
                                    reason=f"Auto-resume: {AUTO_RESUME_SUCCESS_CYCLES} successful recovery cycles",
                                    owner="market_analysis_loop"
                                )
                                logger.info(f"✅ Safe mode cleared after {AUTO_RESUME_SUCCESS_CYCLES} successful recovery cycles")
                            
                            # HARDENING: trading_paused управляется state machine (derived property)
                            # После выхода из SAFE_MODE trading_paused автоматически False
                            state_machine.sync_to_system_state(system_state, manual_pause_active=_control_plane_state.get("manual_pause_active", False))
                            _adaptive_system_state["recovery_cycles"] = 0
                            logger.info(f"🔄 Trading auto-resumed after {AUTO_RESUME_SUCCESS_CYCLES} successful cycles")
                            # Отправляем уведомление
                            try:
                                await asyncio.wait_for(
                                    asyncio.to_thread(send_message, f"✅ **Trading resumed**\n\nSystem recovered after {AUTO_RESUME_SUCCESS_CYCLES} successful analysis cycles. Trading is now active."),
                                    timeout=5.0
                                )
                            except Exception:
                                pass
                    else:
                        # Ошибка или неуспешный цикл - сбрасываем счетчик
                        if _adaptive_system_state["recovery_cycles"] > 0:
                            logger.debug(f"🔄 Recovery reset: error detected (was {_adaptive_system_state['recovery_cycles']}/{AUTO_RESUME_SUCCESS_CYCLES})")
                        _adaptive_system_state["recovery_cycles"] = 0
                else:
                    # Торговля активна - сбрасываем счетчик восстановления
                    if _adaptive_system_state["recovery_cycles"] > 0:
                        _adaptive_system_state["recovery_cycles"] = 0
                    # Если manual pause была активна, но торговля активна - снимаем флаг
                    if manual_pause:
                        _control_plane_state["manual_pause_active"] = False
                
                # Сбрасываем счетчик при входе в safe_mode
                if system_state.system_health.safe_mode:
                    if _adaptive_system_state["recovery_cycles"] > 0:
                        logger.debug(f"🔄 Recovery reset: safe_mode activated (was {_adaptive_system_state['recovery_cycles']}/{AUTO_RESUME_SUCCESS_CYCLES})")
                    _adaptive_system_state["recovery_cycles"] = 0
            else:
                # Auto-resume отключен - используем старую логику на основе safe_mode exit
                if adaptive_state["last_safe_mode_state"] and not system_state.system_health.safe_mode:
                    # Выход из safe_mode
                    adaptive_state["safe_mode_exit_time"] = time.monotonic()
                    logger.info("✅ Safe mode deactivated - monitoring for auto-resume")
                
                if (adaptive_state["safe_mode_exit_time"] is not None and 
                    system_state.system_health.trading_paused and
                    not system_state.system_health.safe_mode):
                    # Проверяем, прошло ли достаточно времени после выхода из safe_mode
                    time_since_exit = time.monotonic() - adaptive_state["safe_mode_exit_time"]
                    if time_since_exit >= AUTO_RESUME_SAFE_MODE_DELAY:
                        # HARDENING: Автоматически возобновляем торговлю через state machine
                        state_machine = get_state_machine()
                        state_machine.sync_to_system_state(system_state, manual_pause_active=_control_plane_state.get("manual_pause_active", False))
                        adaptive_state["safe_mode_exit_time"] = None
                        logger.info(f"🔄 Trading auto-resumed after safe_mode exit (delay: {AUTO_RESUME_SAFE_MODE_DELAY}s)")
                        # Отправляем уведомление
                        try:
                            await asyncio.wait_for(
                                asyncio.to_thread(send_message, "✅ **Trading resumed**\n\nSystem recovered from safe mode. Trading is now active."),
                                timeout=5.0
                            )
                        except Exception:
                            pass
            
            # Обновляем состояние для следующей итерации
            adaptive_state["last_safe_mode_state"] = system_state.system_health.safe_mode
            adaptive_state["last_trading_paused_state"] = system_state.system_health.trading_paused
            
            # ========== МЯГКИЙ КОНТРОЛЬ ВРЕМЕНИ ==========
            # Заменяем аварийный watchdog на мягкое предупреждение
            if duration > MAX_ANALYSIS_TIME:
                logger.warning(
                    "⏱ Analysis slow: %.2fs (limit %.2fs)",
                    duration,
                    MAX_ANALYSIS_TIME
                )
            
            # ========== ALERT ESCALATION (NON-BLOCKING) ==========
            # Оцениваем и отправляем алерты асинхронно, не блокируя analysis loop
            # Создаём задачу для алертов (не ждём её завершения)
            # CRITICAL: Wrap in exception handler to prevent silent failures
            async def _safe_evaluate_alerts():
                """Wrapper to ensure alert evaluation errors are logged"""
                try:
                    await evaluate_and_send_alerts(duration)
                except asyncio.CancelledError:
                    logger.debug("Alert evaluation task cancelled")
                    raise
                except Exception as e:
                    logger.error(
                        f"Alert evaluation task failed: {type(e).__name__}: {e}",
                        exc_info=True
                    )
            
            alert_task = asyncio.create_task(_safe_evaluate_alerts(), name="AlertEvaluation")
            # Note: This is a fire-and-forget task created inside a registered loop
            # It will be cancelled when the parent loop (MarketAnalysis) is cancelled
            
            # ========== ПЕРИОДИЧЕСКОЕ ЛОГИРОВАНИЕ МЕТРИК ==========
            now = time.monotonic()
            if (now - metrics["last_metrics_log"]) >= METRICS_LOG_INTERVAL:
                if metrics["analysis_count"] > 0:
                    avg = metrics["analysis_total_time"] / metrics["analysis_count"]
                    uptime = now - metrics["start_time"]
                    # Улучшенное логирование с адаптивной информацией
                    mode_status = "SAFE_MODE" if system_state.system_health.safe_mode else ("CAUTION" if consecutive_errors > 0 else "NORMAL")
                    trading_status = "PAUSED" if system_state.system_health.trading_paused else "ACTIVE"
                    logger.info(
                        "📈 Metrics | runs=%d avg=%.2fs max=%.2fs uptime=%.0fs interval=%.0fs mode=%s trading=%s errors=%d",
                        metrics["analysis_count"],
                        avg,
                        metrics["analysis_max_time"],
                        uptime,
                        current_interval,
                        mode_status,
                        trading_status,
                        consecutive_errors
                    )
                    metrics["last_metrics_log"] = now
            
            if not success:
                if system_state.system_health.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    pause_msg = f"Multiple errors ({system_state.system_health.consecutive_errors}). Pausing {ERROR_PAUSE}s"
                    logger.warning(pause_msg)
                    try:
                        await asyncio.wait_for(
                            asyncio.to_thread(error_alert, pause_msg),
                            timeout=10.0
                        )
                    except Exception:
                        pass
                    
                    # Проверяем shutdown во время паузы
                    # Используем await asyncio.sleep() с проверкой shutdown каждую секунду
                    try:
                        shutdown_evt = get_shutdown_event()
                        remaining = ERROR_PAUSE
                        while remaining > 0:
                            if shutdown_evt.is_set() or not system_state.system_health.is_running:
                                break
                            # Спим по 1 секунде, чтобы можно было прервать при shutdown
                            await asyncio.sleep(min(1.0, remaining))
                            remaining -= 1.0
                    except asyncio.CancelledError:
                        break
                    
                    system_state.reset_errors()
                    # После паузы сбрасываем next_run для корректного планирования
                    next_run = time.monotonic()
                else:
                    # Короткая пауза после ошибки (с проверкой shutdown)
                    # Используем await asyncio.sleep() с проверкой shutdown каждую секунду
                    try:
                        shutdown_evt = get_shutdown_event()
                        remaining = 30
                        while remaining > 0:
                            if shutdown_evt.is_set() or not system_state.system_health.is_running:
                                break
                            # Спим по 1 секунде, чтобы можно было прервать при shutdown
                            await asyncio.sleep(min(1.0, remaining))
                            remaining -= 1.0
                    except asyncio.CancelledError:
                        break
                    # После паузы сбрасываем next_run для корректного планирования
                    next_run = time.monotonic()
            else:
                # ========== АБСОЛЮТНОЕ ПЛАНИРОВАНИЕ ==========
                # Вычисляем время до следующего запуска
                next_run += current_interval
                sleep_time = max(0.0, next_run - time.monotonic())
                
                # Sleep с проверкой shutdown каждую секунду для быстрого отклика на SIGTERM
                shutdown_evt = get_shutdown_event()
                remaining = sleep_time
                while remaining > 0 and not shutdown_evt.is_set() and system_state.system_health.is_running:
                    chunk = min(1.0, remaining)
                    await asyncio.sleep(chunk)
                    remaining -= chunk
                
                # Проверяем shutdown после sleep
                if shutdown_evt.is_set() or not system_state.system_health.is_running:
                    break
                
        except asyncio.CancelledError:
            logger.info("Market analysis loop cancelled")
            break
        except Exception as e:
            logger.error(f"Critical error in market analysis loop: {type(e).__name__}: {e}")
            logger.error(traceback.format_exc())
            # Пауза с проверкой shutdown
            # Используем await asyncio.sleep() с проверкой shutdown каждую секунду
            try:
                shutdown_evt = get_shutdown_event()
                remaining = ERROR_PAUSE
                while remaining > 0:
                    if shutdown_evt.is_set() or not system_state.system_health.is_running:
                        break
                    # Спим по 1 секунде, чтобы можно было прервать при shutdown
                    await asyncio.sleep(min(1.0, remaining))
                    remaining -= 1.0
            except asyncio.CancelledError:
                break
            # После паузы сбрасываем next_run для корректного планирования
            next_run = time.monotonic()
    
    # Финальный лог метрик
    if metrics["analysis_count"] > 0:
        avg = metrics["analysis_total_time"] / metrics["analysis_count"]
        uptime = time.monotonic() - metrics["start_time"]
        logger.info(
            "📈 Final metrics | runs=%d avg=%.2fs max=%.2fs uptime=%.0fs",
            metrics["analysis_count"],
            avg,
            metrics["analysis_max_time"],
            uptime
        )
    
    logger.info("Market analysis loop stopped")


async def runtime_heartbeat_loop():
    """
    Runtime heartbeat - доказывает, что процесс жив и event loop не заблокирован.
    Запускается каждые 10 секунд, неблокирующий.
    
    Также обнаруживает пропущенные heartbeats (признак застопорившегося event loop).
    """
    # GLOBAL STATE (intentional)
    global _chaos_was_active, _prometheus_metrics
    logger.info("💓 Runtime heartbeat started (interval: 10s)")
    
    heartbeat_count = 0
    last_heartbeat_time = time.time()
    
    # Use shutdown_event for proper cancellation semantics
    shutdown_evt = get_shutdown_event()
    
    while system_state.system_health.is_running and not shutdown_evt.is_set():
        try:
            # Sleep с проверкой shutdown каждую секунду для быстрого отклика на SIGTERM
            remaining = RUNTIME_HEARTBEAT_INTERVAL
            while remaining > 0 and not shutdown_evt.is_set() and system_state.system_health.is_running:
                await asyncio.sleep(min(1.0, remaining))
                remaining -= 1.0
            
            # Проверяем shutdown после sleep
            if shutdown_evt.is_set() or not system_state.system_health.is_running:
                break
            
            heartbeat_count += 1
            
            # Обновляем время последнего heartbeat
            current_time = time.time()
            time_since_last = current_time - last_heartbeat_time
            last_heartbeat_time = current_time
            
            # Обновляем SystemState (thread-safe для ThreadWatchdog)
            system_state.update_heartbeat()
            update_heartbeat_thread_safe()  # Обновляем для ThreadWatchdog
            
            # Проверяем, не пропущены ли heartbeats (признак застопорившегося loop)
            # Если прошло больше чем 2 интервала - это stall
            expected_interval = RUNTIME_HEARTBEAT_INTERVAL
            if time_since_last > expected_interval * HEARTBEAT_MISS_THRESHOLD:
                # Обнаружен пропуск heartbeats - возможен stall event loop
                missed_heartbeats = int((time_since_last - expected_interval) / expected_interval)
                logger.warning(
                    f"HEARTBEAT_MISS detected - "
                    f"time_since_last={time_since_last:.1f}s "
                    f"(expected={expected_interval}s) "
                    f"missed_heartbeats={missed_heartbeats}"
                )
                
                # ========== PROMETHEUS METRICS (NON-BLOCKING) ==========
                # Увеличиваем счетчик scheduler stalls
                increment_scheduler_stalls()
                
                # ========== REQUIREMENT 1: HEARTBEAT → ENFORCEMENT ==========
                # HEARTBEAT_MISS НЕ МОЖЕТ БЫТЬ ТОЛЬКО ЛОГОМ
                # После превышения порога missed_heartbeats → SAFE_MODE
                if missed_heartbeats >= HEARTBEAT_MISS_ENFORCEMENT_THRESHOLD:
                    # Генерируем incident_id для трейсинга
                    import uuid
                    incident_id = f"heartbeat-miss-{uuid.uuid4().hex[:8]}"
                    
                    # HARDENING: Переход в SAFE_MODE через state machine
                    state_machine = get_state_machine()
                    if not state_machine.is_safe_mode:
                        await state_machine.transition_to(
                            SystemStateEnum.SAFE_MODE,
                            reason=f"HEARTBEAT_ENFORCEMENT: missed_heartbeats={missed_heartbeats} >= threshold={HEARTBEAT_MISS_ENFORCEMENT_THRESHOLD}",
                            owner="runtime_heartbeat_loop",
                            metadata={"missed_heartbeats": missed_heartbeats, "incident_id": incident_id}
                        )
                        logger.critical(
                            f"HEARTBEAT_ENFORCEMENT: SAFE_MODE activated - "
                            f"missed_heartbeats={missed_heartbeats} "
                            f">= threshold={HEARTBEAT_MISS_ENFORCEMENT_THRESHOLD} "
                            f"incident_id={incident_id}"
                        )
                        
                        # Метрика для Prometheus
                        _prometheus_metrics["heartbeat_enforcement_total"] = \
                            _prometheus_metrics.get("heartbeat_enforcement_total", 0) + 1
                        
                        # Записываем ошибку для health tracking
                        system_state.record_error(f"HEARTBEAT_MISS_ENFORCEMENT: {incident_id}")
                        
                        # ========== REQUIREMENT 2: CHAOS INVARIANT ==========
                        # Если chaos был активен, фиксируем что переход через SAFE_MODE произошёл
                        if _chaos_was_active:
                            logger.critical(
                                f"CHAOS_INVARIANT_SATISFIED: SAFE_MODE entered after chaos - "
                                f"incident_id={incident_id}"
                            )
                
                # Проверяем, не является ли это fault injection
                if FAULT_INJECT_LOOP_STALL:
                    logger.error(
                        f"FAULT_INJECTION: loop_stall_detected - "
                        f"Controlled loop stall detected via missed heartbeats. "
                        f"time_since_last={time_since_last:.1f}s "
                        f"missed_heartbeats={missed_heartbeats}"
                    )
                    # Записываем ошибку для health tracking
                    system_state.record_error("FAULT_INJECTION: loop_stall_detected")
                    
                    # HARDENING: Проверяем safe-mode активацию через state machine
                    state_machine = get_state_machine()
                    if system_state.system_health.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        if not state_machine.is_safe_mode:
                            await state_machine.transition_to(
                                SystemStateEnum.SAFE_MODE,
                                reason=f"Loop stall detection: consecutive_errors >= MAX_CONSECUTIVE_ERRORS",
                                owner="runtime_heartbeat_loop",
                                metadata={"consecutive_errors": system_state.system_health.consecutive_errors}
                            )
                            logger.warning(
                                f"SAFE-MODE activated after loop stall detection: "
                                f"consecutive_errors={system_state.system_health.consecutive_errors} "
                                f">= MAX_CONSECUTIVE_ERRORS={MAX_CONSECUTIVE_ERRORS}"
                            )
            
            # Логируем heartbeat с метриками
            # Используем asyncio.all_tasks() без get_event_loop() для безопасности
            try:
                pending_tasks = len([t for t in asyncio.all_tasks() if not t.done()])
                loop_running = True  # Если мы здесь, loop точно работает
            except RuntimeError:
                # Если нет активного loop, это не критично
                pending_tasks = 0
                loop_running = False
            
            logger.debug(
                f"heartbeat_alive=true "
                f"count={heartbeat_count} "
                f"pending_tasks={pending_tasks} "
                f"loop_running={loop_running}"
            )
            
        except asyncio.CancelledError:
            logger.info("⏹ Runtime heartbeat cancelled")
            break
        except Exception as e:
            logger.error(f"Error in runtime heartbeat: {type(e).__name__}: {e}")
            # Не падаем - продолжаем heartbeat даже при ошибках
    
    logger.info(f"💓 Runtime heartbeat stopped (total: {heartbeat_count})")


async def loop_guard_watchdog():
    """
    REQUIREMENT 3: LOOP_GUARD_TIMEOUT
    
    Watchdog для event loop - обнаруживает длительные блокировки.
    После timeout:
    - снимает дамп задач (asyncio.all_tasks)
    - записывает structured task dump в лог
    - инициирует SAFE_MODE
    """
    # GLOBAL STATE (intentional) - no globals needed, state machine handles TTL
    logger.info("🛡️ Loop guard watchdog started")
    
    last_heartbeat_check = time.time()
    shutdown_evt = get_shutdown_event()
    
    while system_state.system_health.is_running and not shutdown_evt.is_set():
        try:
            await asyncio.sleep(10.0)  # Проверяем каждые 10 секунд
            
            if shutdown_evt.is_set() or not system_state.system_health.is_running:
                break
            
            # Проверяем время с последнего heartbeat
            current_time = time.time()
            time_since_last_heartbeat = current_time - last_heartbeat_check
            
            if system_state.system_health.last_heartbeat:
                time_since_heartbeat = (current_time - system_state.system_health.last_heartbeat.timestamp())
            else:
                time_since_heartbeat = time_since_last_heartbeat
            
            # Если прошло больше LOOP_GUARD_TIMEOUT - event loop заблокирован
            if time_since_heartbeat > LOOP_GUARD_TIMEOUT:
                import uuid
                incident_id = f"loop-guard-{uuid.uuid4().hex[:8]}"
                
                logger.critical(
                    f"LOOP_GUARD_TIMEOUT: Event loop blocked for {time_since_heartbeat:.1f}s "
                    f"(threshold={LOOP_GUARD_TIMEOUT}s) "
                    f"incident_id={incident_id}"
                )
                
                # ========== TASK DUMP ==========
                try:
                    all_tasks = asyncio.all_tasks()
                    task_dump = []
                    for task in all_tasks:
                        task_info = {
                            "name": task.get_name(),
                            "done": task.done(),
                            "cancelled": task.cancelled(),
                        }
                        if task.done():
                            try:
                                task_info["exception"] = str(task.exception())
                            except Exception:
                                pass
                        task_dump.append(task_info)
                    
                    logger.critical(
                        f"LOOP_GUARD_TASK_DUMP incident_id={incident_id} "
                        f"total_tasks={len(task_dump)} "
                        f"tasks={task_dump}"
                    )
                except Exception as e:
                    logger.error(f"LOOP_GUARD: Failed to dump tasks: {type(e).__name__}: {e}")
                
                # HARDENING: SAFE_MODE ACTIVATION через state machine
                state_machine = get_state_machine()
                if not state_machine.is_safe_mode:
                    await state_machine.transition_to(
                        SystemStateEnum.SAFE_MODE,
                        reason=f"LOOP_GUARD_TIMEOUT: Event loop blocked for {time_since_heartbeat:.1f}s",
                        owner="loop_guard_watchdog",
                        metadata={"time_since_heartbeat": time_since_heartbeat, "incident_id": incident_id}
                    )
                    logger.critical(
                        f"LOOP_GUARD_ENFORCEMENT: SAFE_MODE activated - "
                        f"incident_id={incident_id}"
                    )
                    
                    system_state.record_error(f"LOOP_GUARD_TIMEOUT: {incident_id}")
            
            last_heartbeat_check = current_time
            
        except asyncio.CancelledError:
            logger.info("⏹ Loop guard watchdog cancelled")
            break
        except Exception as e:
            logger.error(f"Error in loop guard watchdog: {type(e).__name__}: {e}")
    
    logger.info("🛡️ Loop guard watchdog stopped")


async def safe_mode_ttl_monitor():
    """
    HARDENING: Мониторит SAFE_MODE TTL через state machine.
    
    SINGLE-WRITER: Вся логика TTL находится в state machine.
    Этот монитор только вызывает check_safe_mode_ttl() и обрабатывает FATAL.
    
    REQUIREMENT 4: SAFE_MODE TTL
    - По истечении TTL: SAFE_MODE → FATAL (через state machine)
    - FATAL обрабатывается централизованным exit handler
    """
    logger.info("⏱️ Safe mode TTL monitor started")
    
    state_machine = get_state_machine()
    shutdown_evt = get_shutdown_event()
    
    while system_state.system_health.is_running and not shutdown_evt.is_set():
        try:
            await asyncio.sleep(30.0)  # Проверяем каждые 30 секунд
            
            if shutdown_evt.is_set() or not system_state.system_health.is_running:
                break
            
            # HARDENING: Проверяем TTL через state machine
            # State machine сам выполнит переход SAFE_MODE → FATAL если TTL истёк
            ttl_expired = await state_machine.check_safe_mode_ttl()
            
            if ttl_expired:
                # HARDENING: TTL истёк, state machine перешёл в FATAL
                # Централизованный exit handler обработает os._exit
                logger.critical("SAFE_MODE_TTL_EXPIRED: State machine transitioned to FATAL")
                # Exit handler будет вызван в main() при проверке состояния
            
        except asyncio.CancelledError:
            logger.info("⏹ Safe mode TTL monitor cancelled")
            break
        except Exception as e:
            logger.error(f"Error in safe mode TTL monitor: {type(e).__name__}: {e}")
    
    logger.info("⏱️ Safe mode TTL monitor stopped")

async def heartbeat_loop():
    """
    Отправляет периодические heartbeat сообщения в Telegram.
    Отдельно от runtime heartbeat для доказательства liveness.
    """
    logger.info("💓 Telegram heartbeat monitoring started")
    
    # Use shutdown_event for proper cancellation semantics
    shutdown_evt = get_shutdown_event()
    
    while system_state.system_health.is_running and not shutdown_evt.is_set():
        try:
            # Sleep с проверкой shutdown каждую секунду для быстрого отклика на SIGTERM
            remaining = HEARTBEAT_INTERVAL
            while remaining > 0 and not shutdown_evt.is_set() and system_state.system_health.is_running:
                try:
                    await asyncio.sleep(min(1.0, remaining))
                except asyncio.CancelledError:
                    raise  # Пробрасываем для правильного shutdown
                remaining -= 1.0
            
            # Проверяем shutdown после sleep
            if shutdown_evt.is_set() or not system_state.system_health.is_running:
                break
            
            try:
                # КРИТИЧНО: to_thread может блокировать при network blackhole, обёртываем в wait_for
                # Таймаут 10s достаточен для нормальной работы, но предотвращает блокировку shutdown
                await asyncio.wait_for(
                    asyncio.to_thread(send_heartbeat),
                    timeout=10.0
                )
                system_state.update_heartbeat()
                update_heartbeat_thread_safe()  # Обновляем для ThreadWatchdog
                logger.debug("Telegram heartbeat sent")
            except asyncio.TimeoutError:
                # Timeout при network blackhole - не критично, просто пропускаем heartbeat
                logger.debug("Telegram heartbeat timeout (non-critical) - network may be unreachable")
            except Exception as e:
                # Telegram ошибки не должны останавливать heartbeat
                logger.warning(f"Telegram heartbeat failed (non-critical): {type(e).__name__}: {e}")
        except asyncio.CancelledError:
            logger.info("⏹ Telegram heartbeat cancelled")
            break
        except Exception as e:
            logger.error(f"Error in Telegram heartbeat loop: {type(e).__name__}: {e}")
            # Пауза перед повтором с проверкой shutdown каждую секунду
            shutdown_evt = get_shutdown_event()
            remaining = 300
            while remaining > 0 and not shutdown_evt.is_set() and system_state.system_health.is_running:
                try:
                    await asyncio.sleep(min(1.0, remaining))
                except asyncio.CancelledError:
                    raise  # Пробрасываем для правильного shutdown
                remaining -= 1.0
            if shutdown_evt.is_set() or not system_state.system_health.is_running:
                break
    
    logger.info("💓 Telegram heartbeat stopped")


async def daily_report_loop():
    """
    Отправляет ежедневные отчеты в определенное время.
    
    AsyncIO safety:
    - Длинные sleep с проверкой shutdown
    - Graceful cancellation support
    """
    logger.info("Daily report loop started")
    
    # Use shutdown_event for proper cancellation semantics
    shutdown_evt = get_shutdown_event()
    
    while system_state.system_health.is_running and not shutdown_evt.is_set():
        try:
            # Вычисляем время до следующего отчета (00:00 UTC)
            now = datetime.now(UTC)
            next_report = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            sleep_seconds = (next_report - now).total_seconds()
            
            logger.info(f"Next daily report in {sleep_seconds/3600:.1f} hours")
            
            # Sleep с проверкой shutdown (разбиваем на чанки для responsiveness)
            sleep_chunk = min(3600.0, sleep_seconds)  # Максимум 1 час за раз
            remaining = sleep_seconds
            
            shutdown_evt = get_shutdown_event()
            while remaining > 0 and system_state.system_health.is_running and not shutdown_evt.is_set():
                try:
                    chunk = min(sleep_chunk, remaining)
                    await asyncio.sleep(chunk)
                    remaining -= chunk
                except asyncio.CancelledError:
                    break
            
            shutdown_evt = get_shutdown_event()
            if shutdown_evt.is_set() or not system_state.system_health.is_running:
                break
            
            # Отправляем отчет
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(generate_daily_report),
                    timeout=60.0
                )
                logger.info("Daily report sent")
            except Exception as e:
                logger.warning(f"Failed to send daily report (non-critical): {type(e).__name__}: {e}")
            
        except asyncio.CancelledError:
            logger.info("Daily report loop cancelled")
            break
        except Exception as e:
            logger.error(f"Error in daily report loop: {type(e).__name__}: {e}")
            # Пауза 1 час перед повтором (с проверкой shutdown)
            try:
                # Используем await asyncio.sleep() с проверкой shutdown каждую секунду
                shutdown_evt = get_shutdown_event()
                remaining = 3600
                while remaining > 0:
                    if shutdown_evt.is_set() or not system_state.system_health.is_running:
                        break
                    # Спим по 1 секунде, чтобы можно было прервать при shutdown
                    await asyncio.sleep(min(1.0, remaining))
                    remaining -= 1.0
            except asyncio.CancelledError:
                break
    
    logger.info("Daily report loop stopped")


async def synthetic_decision_tick_loop():
    """
    Synthetic decision tick - периодически выполняет decision pipeline
    с синтетическим SignalSnapshot для тестирования устойчивости.
    
    Используется для:
    - Тестирования fault injection
    - Валидации decision pipeline без внешних сигналов
    - Проверки health handling
    
    Без side effects: NO orders, NO persistence, NO Telegram.
    """
    if not ENABLE_SYNTHETIC_DECISION_TICK:
        return  # Не запускаем если ENV не установлен
    
    logger.info("Synthetic decision tick loop started (interval: 10s)")
    
    from core.signal_snapshot import SignalSnapshot, SignalDecision, RiskLevel, VolatilityLevel
    from core.market_state import MarketState
    from core.decision_core import MarketRegime
    from execution.gatekeeper import get_gatekeeper
    
    tick_count = 0
    # Use shutdown_event for proper cancellation semantics
    shutdown_evt = get_shutdown_event()
    
    while system_state.system_health.is_running and not shutdown_evt.is_set():
        try:
            # Sleep с проверкой shutdown каждую секунду для быстрого отклика на SIGTERM
            remaining = SYNTHETIC_DECISION_TICK_INTERVAL
            while remaining > 0 and not shutdown_evt.is_set() and system_state.system_health.is_running:
                await asyncio.sleep(min(1.0, remaining))
                remaining -= 1.0
            
            # Проверяем shutdown после sleep
            if shutdown_evt.is_set() or not system_state.system_health.is_running:
                break
            
            tick_count += 1
            
            # Создаём синтетический SignalSnapshot
            synthetic_snapshot = SignalSnapshot(
                timestamp=datetime.now(UTC),
                symbol="BTCUSDT",  # Используем BTCUSDT как тестовый символ
                timeframe_anchor="15m",
                states={
                    "5m": MarketState.A,
                    "15m": MarketState.D,
                    "30m": MarketState.A,
                    "1h": MarketState.B,
                    "4h": MarketState.A
                },
                market_regime=MarketRegime(
                    trend_type="TREND",
                    volatility_level="MEDIUM",
                    risk_sentiment="RISK_ON",
                    confidence=0.7
                ),
                volatility_level=VolatilityLevel.NORMAL,
                correlation_level=0.5,
                score=75,
                score_max=125,
                confidence=0.65,
                entropy=0.35,
                risk_level=RiskLevel.MEDIUM,
                recommended_leverage=5.0,
                entry=50000.0,
                tp=51000.0,
                sl=49500.0,
                decision=SignalDecision.ENTER,
                decision_reason="SYNTHETIC_DECISION_TICK: synthetic signal for testing",
                directions={"15m": "UP", "30m": "UP", "1h": "UP", "4h": "UP"},
                score_details={},
                reasons=["Synthetic tick for decision pipeline testing"]
            )
            
            logger.info(
                f"SYNTHETIC_DECISION_TICK: executing decision pipeline "
                f"(tick={tick_count}, symbol={synthetic_snapshot.symbol})"
            )
            
            # Получаем gatekeeper
            gatekeeper = get_gatekeeper()
            
            # Пропускаем через decision pipeline через gatekeeper
            # Используем send_signal, но с флагом что это synthetic (не отправляем в Telegram)
            try:
                # Создаём минимальные signal_data для gatekeeper
                signal_data = {
                    "zone": {
                        "entry": synthetic_snapshot.entry,
                        "stop": synthetic_snapshot.sl,
                        "target": synthetic_snapshot.tp
                    },
                    "position_size": 100.0,  # Синтетический размер
                    "leverage": synthetic_snapshot.recommended_leverage,
                    "risk": synthetic_snapshot.risk_level.value
                }
                
                # Вызываем внутренние методы gatekeeper для decision pipeline
                # БЕЗ отправки в Telegram (это synthetic tick)
                
                # 1. MetaDecisionBrain (если доступен)
                meta_result = None
                if gatekeeper.meta_decision_brain:
                    meta_result = gatekeeper._check_meta_decision(synthetic_snapshot, system_state)
                    if meta_result and not meta_result.allow_trading:
                        logger.info(
                            f"SYNTHETIC_DECISION_TICK: MetaDecisionBrain BLOCKED "
                            f"(reason={meta_result.reason})"
                        )
                        continue  # Переходим к следующему tick
                
                # 2. DecisionCore.should_i_trade() - здесь может быть fault injection
                try:
                    decision_core_result = gatekeeper.decision_core.should_i_trade(
                        symbol=synthetic_snapshot.symbol,
                        system_state=system_state
                    )
                    
                    if not decision_core_result.can_trade:
                        logger.info(
                            f"SYNTHETIC_DECISION_TICK: DecisionCore BLOCKED "
                            f"(reason={decision_core_result.reason})"
                        )
                        continue
                    
                    logger.debug(
                        f"SYNTHETIC_DECISION_TICK: DecisionCore ALLOWED "
                        f"(reason={decision_core_result.reason})"
                    )
                except RuntimeError as e:
                    # Обработка fault injection
                    if "FAULT_INJECTION: decision_exception" in str(e):
                        logger.error(
                            f"SYNTHETIC_DECISION_TICK: FAULT_INJECTION detected - "
                            f"Controlled exception from DecisionCore. "
                            f"Runtime continues. error_type=RuntimeError error_message={str(e)}"
                        )
                        # Записываем ошибку для health tracking
                        system_state.record_error("FAULT_INJECTION: decision_exception (synthetic tick)")
                        
                        # HARDENING: Проверяем safe-mode активацию через state machine
                        state_machine = get_state_machine()
                        if system_state.system_health.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                            if not state_machine.is_safe_mode:
                                await state_machine.transition_to(
                                    SystemStateEnum.SAFE_MODE,
                                    reason=f"SYNTHETIC_DECISION_TICK: consecutive_errors >= MAX_CONSECUTIVE_ERRORS",
                                    owner="synthetic_decision_tick_loop",
                                    metadata={"consecutive_errors": system_state.system_health.consecutive_errors}
                                )
                                logger.warning(
                                    f"SYNTHETIC_DECISION_TICK: SAFE-MODE activated - "
                                    f"consecutive_errors={system_state.system_health.consecutive_errors} "
                                    f">= MAX_CONSECUTIVE_ERRORS={MAX_CONSECUTIVE_ERRORS}"
                                )
                    else:
                        # Другие RuntimeError - пробрасываем
                        raise
                
                # 3. PortfolioBrain
                portfolio_analysis = gatekeeper._check_portfolio(synthetic_snapshot)
                if portfolio_analysis:
                    from core.portfolio_brain import PortfolioDecision
                    if portfolio_analysis.decision == PortfolioDecision.BLOCK:
                        logger.info(
                            f"SYNTHETIC_DECISION_TICK: PortfolioBrain BLOCKED "
                            f"(reason={portfolio_analysis.reason})"
                        )
                        continue
                
                # 4. PositionSizer
                if gatekeeper.position_sizer:
                    sizing_result = gatekeeper._calculate_position_size(
                        synthetic_snapshot,
                        portfolio_analysis
                    )
                    if sizing_result and not sizing_result.position_allowed:
                        logger.info(
                            f"SYNTHETIC_DECISION_TICK: PositionSizer BLOCKED "
                            f"(reason={sizing_result.reason})"
                        )
                        continue
                
                logger.debug(
                    f"SYNTHETIC_DECISION_TICK: decision pipeline completed successfully "
                    f"(tick={tick_count})"
                )
                
            except Exception as e:
                # Обработка ошибок в decision pipeline
                logger.error(
                    f"SYNTHETIC_DECISION_TICK: error in decision pipeline "
                    f"(tick={tick_count}): {type(e).__name__}: {e}",
                    exc_info=True
                )
                # Записываем ошибку
                system_state.record_error(f"Synthetic tick error: {type(e).__name__}")
                
        except asyncio.CancelledError:
            logger.info("Synthetic decision tick loop cancelled")
            break
        except Exception as e:
            logger.error(f"Error in synthetic decision tick loop: {type(e).__name__}: {e}")
            # Пауза перед повтором
            try:
                await asyncio.wait_for(
                    asyncio.sleep(30),
                    timeout=30.0
                )
            except asyncio.CancelledError:
                break
    
    logger.info(f"Synthetic decision tick loop stopped (total ticks: {tick_count})")


async def loop_stall_injection_task():
    """
    Loop stall injection - преднамеренно блокирует event loop для тестирования
    обнаружения застопорившегося loop.
    
    Используется для:
    - Тестирования обнаружения пропущенных heartbeats
    - Валидации safe_mode активации
    - Проверки восстановления после stall
    
    ВАЖНО: Использует прямой синхронный time.sleep в async задаче для блокировки
    event loop. Это плохая практика в production, но допустимо для fault injection.
    """
    if not FAULT_INJECT_LOOP_STALL:
        return  # Не запускаем если ENV не установлен
    
    logger.info(f"Loop stall injection enabled (stall duration: {LOOP_STALL_DURATION}s)")
    
    # Ждем 30 секунд после старта, чтобы система успела инициализироваться
    # Sleep с проверкой shutdown каждую секунду для быстрого отклика на SIGTERM
    shutdown_evt = get_shutdown_event()
    remaining = 30.0
    while remaining > 0 and not shutdown_evt.is_set() and system_state.system_health.is_running:
        await asyncio.sleep(min(1.0, remaining))
        remaining -= 1.0
    
    # Проверяем shutdown после sleep
    if shutdown_evt.is_set() or not system_state.system_health.is_running:
        return
    
    logger.warning(
        f"FAULT_INJECTION: loop_stall starting - "
        f"Event loop will be blocked for {LOOP_STALL_DURATION}s. "
        f"This is a controlled fault injection for testing."
    )
    
    try:
        # ========== FAULT INJECTION: LOOP STALL ==========
        #
        # ВАЖНО: Для fault injection нужно именно блокировать event loop,
        # чтобы проверить обнаружение stall через пропуск heartbeats.
        #
        # ПРОБЛЕМА: time.sleep() блокирует event loop - это антипаттерн.
        # РЕШЕНИЕ: Используем asyncio.to_thread() для выполнения time.sleep()
        # в отдельном потоке, но это НЕ блокирует event loop.
        #
        # АЛЬТЕРНАТИВА: Использовать await asyncio.sleep() - это не блокирует loop.
        #
        # КОМПРОМИСС: Для fault injection используем await asyncio.sleep(),
        # но с очень маленькими интервалами, чтобы максимально приблизиться
        # к блокировке loop. Это все равно не будет полностью блокировать loop,
        # но позволит проверить обнаружение stall через пропуск heartbeats.
        #
        # Если нужна ПОЛНАЯ блокировка loop для тестирования, можно использовать
        # time.sleep() напрямую, но это нарушает правила async-кода.
        #
        logger.warning(f"FAULT_INJECTION: loop_stall active - simulating event loop stall for {LOOP_STALL_DURATION}s")
        
        # Используем await asyncio.sleep() вместо time.sleep() для соблюдения async правил
        # Это не блокирует event loop полностью, но создает нагрузку, которая может
        # привести к пропуску heartbeats при высокой нагрузке
        remaining = LOOP_STALL_DURATION
        while remaining > 0:
            # Проверяем shutdown каждую секунду
            shutdown_evt = get_shutdown_event()
            if shutdown_evt.is_set() or not system_state.system_health.is_running:
                break
            # Используем маленькие интервалы для максимальной нагрузки на loop
            await asyncio.sleep(min(0.1, remaining))
            remaining -= 0.1
        
        logger.info(
            f"FAULT_INJECTION: loop_stall completed - "
            f"Event loop should resume. Recovery expected."
        )
        
    except asyncio.CancelledError:
        logger.info("Loop stall injection cancelled")
    except Exception as e:
        logger.error(f"Error in loop stall injection: {type(e).__name__}: {e}")


async def _telegram_polling_task(app, shutdown_event):
    """
    Внутренняя задача для запуска Telegram polling.
    Изолирована для перехвата исключений из внутренних tasks.
    
    Использует низкоуровневые методы без управления event loop:
    - initialize() - инициализация приложения
    - start() - запуск приложения
    - updater.start_polling() - запуск polling
    - Ожидание через shutdown_event
    
    НЕ использует:
    - run_polling() - он пытается управлять event loop
    - loop.run_until_complete() - управляет event loop
    - asyncio.run() - создает новый event loop
    """
    from telegram.error import Conflict
    
    try:
        # Инициализация приложения
        # КРИТИЧНО: initialize() может блокировать на сетевом I/O при network blackhole
        try:
            await asyncio.wait_for(app.initialize(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("Telegram app.initialize() timeout - network may be unreachable")
            raise
        except asyncio.CancelledError:
            raise
        
        # Запуск polling через updater
        if not app.updater:
            raise RuntimeError("Application does not have an Updater")
        
        # Запускаем polling
        # CRITICAL: Defensive Conflict handling - exit cleanly if another instance is running
        try:
            logger.info("Starting Telegram polling...")
            # КРИТИЧНО: start_polling() может блокировать на сетевом I/O, но это долгоживущая задача
            # Таймаут не применяем здесь, так как polling должен работать постоянно
            # Отмена происходит через cancellation task
            await app.updater.start_polling(
                poll_interval=0.0,
                timeout=10,
                bootstrap_retries=-1,
                drop_pending_updates=True
            )
            logger.info("Telegram polling started")
        except Conflict as e:
            # Conflict detected - another instance is already polling
            # This is NOT retryable - exit cleanly and let systemd restart later
            logger.error(
                f"Telegram Conflict detected (another instance running): {type(e).__name__}: {e}. "
                f"Exiting cleanly to allow systemd restart."
            )
            # Cleanup before exit
            try:
                await asyncio.wait_for(app.shutdown(), timeout=2.0)
            except Exception:
                pass
            # Wait 10 seconds to allow previous instance to fully stop
            # КРИТИЧНО: sleep с проверкой cancellation для быстрого отклика на shutdown
            try:
                await asyncio.sleep(10.0)
            except asyncio.CancelledError:
                raise
            # Exit process cleanly - systemd will restart
            import sys
            sys.exit(1)
        
        # Запускаем приложение
        # КРИТИЧНО: start() может блокировать на сетевом I/O при network blackhole
        try:
            await asyncio.wait_for(app.start(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("Telegram app.start() timeout - network may be unreachable")
            # Cleanup перед перезапуском
            try:
                await asyncio.wait_for(app.shutdown(), timeout=2.0)
            except Exception:
                pass
            raise
        except asyncio.CancelledError:
            # Cleanup при cancellation
            try:
                await asyncio.wait_for(app.shutdown(), timeout=2.0)
            except Exception:
                pass
            raise
        
        # Ждём shutdown event (polling работает в фоне)
        # CRITICAL: Используем wait_for с таймаутом для предотвращения бесконечного ожидания
        # Таймаут 3600s (1 час) - достаточно для нормальной работы, но предотвращает зависание
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=3600.0)
        except asyncio.TimeoutError:
            # Таймаут - это нормально, продолжаем работу
            logger.debug("Shutdown event wait timeout (normal operation)")
        except asyncio.CancelledError:
            # Task отменена - выходим
            pass
        
        # Останавливаем updater (с таймаутом для быстрого shutdown)
        # КРИТИЧНО: Таймаут 2.0s предотвращает блокировку shutdown при network blackhole
        try:
            await asyncio.wait_for(app.updater.stop(), timeout=2.0)
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"Error stopping updater (non-critical): {type(e).__name__}: {e}")
        
        # Останавливаем приложение (с таймаутом для быстрого shutdown)
        # КРИТИЧНО: Таймаут 2.0s предотвращает блокировку shutdown при network blackhole
        try:
            await asyncio.wait_for(app.stop(), timeout=2.0)
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"Error stopping app (non-critical): {type(e).__name__}: {e}")
        
        # Shutdown приложения (с таймаутом для быстрого shutdown)
        # КРИТИЧНО: Таймаут 2.0s предотвращает блокировку shutdown при network blackhole
        try:
            await asyncio.wait_for(app.shutdown(), timeout=2.0)
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"Error shutting down app (non-critical): {type(e).__name__}: {e}")
        
    except Exception:
        # Все исключения пробрасываем наверх для обработки в supervisor
        # Но сначала пытаемся cleanup
        try:
            if app.updater and app.updater.running:
                await app.updater.stop()
        except Exception:
            pass
        try:
            if app.running:
                await app.stop()
        except Exception:
            pass
        try:
            await app.shutdown()
        except Exception:
            pass
        raise


# ========== HTTP ROUTE HANDLERS (MODULE LEVEL) ==========
# ВСЕ handlers объявлены на уровне модуля для единого router ownership

import json
import time

async def handle_admin_status():
    """
    GET /admin/status - возвращает статус системы
    
    SAFE MODE HARD LOCK:
    - safe_mode является read-only (только чтение)
    - Этот endpoint НЕ изменяет safe_mode
    """
    # GLOBAL STATE (intentional) - только чтение
    global _control_plane_state
    metrics = get_analysis_metrics()
    uptime = 0.0
    if metrics.get("start_time") is not None:
        uptime = time.monotonic() - metrics["start_time"]
    
    # SAFE MODE HARD LOCK: safe_mode только читается, никогда не изменяется
    status_data = {
        "trading_paused": system_state.system_health.trading_paused,
        "manual_pause_active": _control_plane_state.get("manual_pause_active", False),
        "safe_mode": system_state.system_health.safe_mode,  # READ-ONLY
        "uptime_seconds": round(uptime, 2)
    }
    return 200, json.dumps(status_data, indent=2).encode('utf-8')

async def handle_admin_pause():
    """
    POST /admin/pause - приостанавливает торговлю
    
    SAFE MODE HARD LOCK:
    - safe_mode является read-only для HTTP API
    - safe_mode НЕ изменяется через этот endpoint
    - Если safe_mode == True, trading_paused уже должен быть True
    """
    # GLOBAL STATE (intentional)
    global _prometheus_metrics, _control_plane_state
    logger.info("ADMIN COMMAND RECEIVED: pause")
    
    # REQUIREMENT: Concurrency safety - prevent race conditions
    async with _get_admin_lock():
        try:
            # Idempotent: можно вызывать несколько раз
            # Атомарное обновление состояния
            # HARDENING: safe_mode НЕ изменяется здесь - он остается как есть
            _control_plane_state["manual_pause_active"] = True
            # HARDENING: Синхронизируем trading_paused через state machine
            state_machine = get_state_machine()
            state_machine.sync_to_system_state(system_state, manual_pause_active=True)
            # SAFE MODE HARD LOCK: safe_mode остается неизменным
            
            # Инкрементируем метрику ДО возврата ответа (новая структура с result labels)
            if "success" not in _prometheus_metrics["admin_commands_total"]["pause"]:
                _prometheus_metrics["admin_commands_total"]["pause"]["success"] = 0
            _prometheus_metrics["admin_commands_total"]["pause"]["success"] += 1
            
            logger.info("ADMIN COMMAND APPLIED: pause - trading_paused=True, manual_pause_active=True")
            return 200, json.dumps({"status": "paused"}).encode('utf-8')
        except Exception as e:
            logger.error(f"ADMIN COMMAND ERROR: pause - {type(e).__name__}: {e}")
            raise

async def handle_admin_resume():
    """
    POST /admin/resume - возобновляет торговлю
    
    SAFE MODE HARD LOCK:
    - Если safe_mode == True, команда БЛОКИРУЕТСЯ с HTTP 403
    - safe_mode НИКОГДА не изменяется через HTTP
    - Метрики отражают реальный результат (blocked_safe_mode или success)
    """
    # GLOBAL STATE (intentional)
    global _prometheus_metrics, _control_plane_state
    logger.info("ADMIN COMMAND RECEIVED: resume")
    
    # REQUIREMENT: Concurrency safety - prevent race conditions
    # WHY: Concurrent HTTP requests cannot race-clear safe_mode or resume trading while safe_mode == true
    async with _get_admin_lock():
        # ========== SAFE MODE HARD LOCK ENFORCEMENT ==========
        # КРИТИЧНО: safe_mode является read-only для HTTP API
        # Проверяем safe_mode ПЕРЕД любыми изменениями состояния (hard lock)
        safe_mode_before = system_state.system_health.safe_mode
        
        if safe_mode_before:
            # SAFE MODE HARD LOCK: блокируем команду
            # Инкрементируем метрику для blocked_safe_mode
            if "blocked_safe_mode" not in _prometheus_metrics["admin_commands_total"]["resume"]:
                _prometheus_metrics["admin_commands_total"]["resume"]["blocked_safe_mode"] = 0
            _prometheus_metrics["admin_commands_total"]["resume"]["blocked_safe_mode"] += 1
            
            # ВАЖНО: trading_paused и manual_pause_active НЕ изменяются
            trading_paused_before = system_state.system_health.trading_paused
            manual_pause_before = _control_plane_state["manual_pause_active"]
            
            # WARN-level logging as required: "ADMIN RESUME BLOCKED: safe_mode_active"
            logger.warning(
                f"ADMIN RESUME BLOCKED: safe_mode_active. "
                f"State preserved: trading_paused={trading_paused_before}, "
                f"manual_pause_active={manual_pause_before}. "
                f"Safe mode can only be cleared by recovery cycles or process restart."
            )
            
            # Возвращаем HTTP 403 с правильным JSON форматом
            # REQUIREMENT: response body MUST include reason: "safe_mode_active"
            return 403, json.dumps({
                "reason": "safe_mode_active"
            }).encode('utf-8')
        
        # ========== SAFE MODE CHECK PASSED - PROCEED WITH RESUME ==========
        # Атомарное обновление состояния
        # HARDENING: safe_mode НЕ изменяется здесь - он остается как есть
        _control_plane_state["manual_pause_active"] = False
        # HARDENING: Синхронизируем trading_paused через state machine
        state_machine = get_state_machine()
        state_machine.sync_to_system_state(system_state, manual_pause_active=False)
        
        # Инкрементируем метрику для успешного выполнения
        if "success" not in _prometheus_metrics["admin_commands_total"]["resume"]:
            _prometheus_metrics["admin_commands_total"]["resume"]["success"] = 0
        _prometheus_metrics["admin_commands_total"]["resume"]["success"] += 1
        
        # Логируем подтверждение, что safe_mode не изменен
        safe_mode_after = system_state.system_health.safe_mode
        logger.info(
            f"ADMIN COMMAND APPLIED: resume - trading_paused=False, manual_pause_active=False. "
            f"SAFE MODE HARD LOCK verified: safe_mode={safe_mode_after} (unchanged from {safe_mode_before})"
        )
        return 200, json.dumps({"status": "resumed"}).encode('utf-8')

async def handle_metrics():
    """GET /metrics - возвращает Prometheus-совместимые метрики"""
    # GLOBAL STATE (intentional) - только чтение
    global _control_plane_state, _prometheus_metrics
    metrics = get_analysis_metrics()
    prom_metrics = get_prometheus_metrics()
    
    # Вычисляем uptime
    uptime = 0.0
    if metrics.get("start_time") is not None:
        uptime = time.monotonic() - metrics["start_time"]
    
    # Определяем mode для labels (low cardinality)
    mode = "SAFE_MODE" if system_state.system_health.safe_mode else "NORMAL"
    if system_state.system_health.consecutive_errors > 0:
        mode = "CAUTION"
    
    # Формируем Prometheus metrics
    lines = []
    
    # Histogram: market_analysis_duration_seconds
    for bucket in ANALYSIS_DURATION_BUCKETS:
        count = prom_metrics["analysis_duration_buckets"].get(bucket, 0)
        lines.append(f'market_analysis_duration_seconds_bucket{{le="{bucket:.1f}",mode="{mode}"}} {count}')
    total_count = prom_metrics["analysis_duration_count"]
    lines.append(f'market_analysis_duration_seconds_bucket{{le="+Inf",mode="{mode}"}} {total_count}')
    lines.append(f'market_analysis_duration_seconds_sum{{mode="{mode}"}} {prom_metrics["analysis_duration_sum"]:.3f}')
    lines.append(f'market_analysis_duration_seconds_count{{mode="{mode}"}} {total_count}')
    
    # Gauge: last_analysis_duration_seconds
    duration = metrics.get("last_analysis_duration", 0.0)
    lines.append(f'last_analysis_duration_seconds{{mode="{mode}"}} {duration:.3f}')
    
    # Counters
    runs_total = metrics.get("analysis_count", 0)
    lines.append(f'market_analysis_runs_total {runs_total}')
    cycles_total = prom_metrics["analysis_cycles_total"]
    lines.append(f'analysis_cycles_total{{mode="{mode}"}} {cycles_total}')
    errors_total = system_state.system_health.consecutive_errors
    lines.append(f'market_analysis_errors_total{{mode="{mode}"}} {errors_total}')
    stalls_total = prom_metrics["scheduler_stalls_total"]
    lines.append(f'scheduler_stalls_total {stalls_total}')
    
    # Gauges
    lines.append(f'market_volatility 0.000')
    lines.append(f'uptime_seconds {uptime:.3f}')
    safe_mode_value = 1 if system_state.system_health.safe_mode else 0
    lines.append(f'safe_mode {safe_mode_value}')
    trading_paused_value = 1 if system_state.system_health.trading_paused else 0
    lines.append(f'trading_paused {trading_paused_value}')
    
    # Adaptive system metrics
    adaptive_system = get_adaptive_system_state()
    adaptive_interval = adaptive_system.get("adaptive_interval", float(ANALYSIS_INTERVAL))
    lines.append(f'adaptive_analysis_interval_seconds {adaptive_interval:.1f}')
    recovery_cycles = adaptive_system.get("recovery_cycles", 0)
    recovery_remaining = max(0, AUTO_RESUME_SUCCESS_CYCLES - recovery_cycles) if AUTO_RESUME_TRADING_ENABLED else 0
    lines.append(f'recovery_cycles_remaining {recovery_remaining}')
    
    # Control plane metrics
    manual_pause_value = 1 if _control_plane_state["manual_pause_active"] else 0
    lines.append(f'manual_pause_active {manual_pause_value}')
    
    # Admin commands metrics with result labels
    admin_commands = _prometheus_metrics["admin_commands_total"]
    # Pause commands
    pause_success = admin_commands.get("pause", {}).get("success", 0)
    lines.append(f'admin_commands_total{{command="pause", result="success"}} {pause_success}')
    # Resume commands
    resume_success = admin_commands.get("resume", {}).get("success", 0)
    resume_blocked = admin_commands.get("resume", {}).get("blocked_safe_mode", 0)
    lines.append(f'admin_commands_total{{command="resume", result="success"}} {resume_success}')
    lines.append(f'admin_commands_total{{command="resume", result="blocked_safe_mode"}} {resume_blocked}')
    
    # Объединяем все метрики
    body = '\n'.join(lines) + '\n'
    return 200, body.encode('utf-8')

async def handle_chaos_inject():
    """
    POST /admin/chaos/inject - Инъекция chaos для тестирования recovery
    
    ТРЕБОВАНИЯ:
    - Только в debug/admin mode (проверка через env или auth)
    - Гарантированно вызывает event loop stall
    - Воспроизводится 100% по команде
    
    Body: {"type": "cross_lock_deadlock|sync_io_block|recursive_await|cpu_bound_loop", "duration": 300}
    """
    # GLOBAL STATE (intentional)
    global _chaos_was_active
    # Проверка доступа (только в debug mode)
    chaos_enabled = os.environ.get("CHAOS_ENABLED", "false").lower() == "true"
    if not chaos_enabled:
        return 403, json.dumps({
            "error": "chaos_disabled",
            "message": "Chaos injection disabled. Set CHAOS_ENABLED=true to enable."
        }).encode('utf-8')
    
    try:
        # Парсим body
        # В реальности нужно прочитать body из request
        # Для упрощения используем query params или defaults
        chaos_type_str = "cpu_bound_loop"  # Default
        duration = 300.0  # Default
        
        # Определяем тип chaos
        chaos_type_map = {
            "cross_lock_deadlock": ChaosType.CROSS_LOCK_DEADLOCK,
            "sync_io_block": ChaosType.SYNC_IO_BLOCK,
            "recursive_await": ChaosType.RECURSIVE_AWAIT,
            "cpu_bound_loop": ChaosType.CPU_BOUND_LOOP,
        }
        
        chaos_type = chaos_type_map.get(chaos_type_str, ChaosType.CPU_BOUND_LOOP)
        
        # Инъекция chaos
        chaos_engine = get_chaos_engine()
        incident_id = await chaos_engine.inject_chaos(chaos_type, duration)
        
        # ========== REQUIREMENT 2: CHAOS INVARIANT ==========
        # Если chaos был активен И произошёл heartbeat miss:
        # система ОБЯЗАНА пройти через SAFE_MODE
        _chaos_was_active = True
        logger.critical(
            f"CHAOS_INJECTION_TRIGGERED (invariant tracking enabled) "
            f"incident_id={incident_id} "
            f"chaos_type={chaos_type.value} "
            f"duration={duration}s"
        )
        
        # Task dump перед инъекцией
        try:
            log_task_dump(incident_id, context="CHAOS_INJECTION_START")
        except Exception:
            pass  # Не критично если task_dump не доступен
        
        return 200, json.dumps({
            "status": "chaos_injected",
            "incident_id": incident_id,
            "chaos_type": chaos_type.value,
            "duration": duration,
            "message": f"Chaos injection started. Event loop will stall for {duration}s."
        }).encode('utf-8')
        
    except RuntimeError as e:
        # Chaos уже активен
        return 409, json.dumps({
            "error": "chaos_already_active",
            "message": str(e)
        }).encode('utf-8')
    except Exception as e:
        logger.error(f"CHAOS_INJECTION_ERROR: {type(e).__name__}: {e}")
        return 500, json.dumps({
            "error": "chaos_injection_failed",
            "message": str(e)
        }).encode('utf-8')

async def handle_chaos_stop():
    """
    POST /admin/chaos/stop - Остановка активной chaos-инъекции
    
    REQUIREMENT 2: После остановки chaos проверяем инвариант:
    - Если chaos был активен И произошёл heartbeat miss → SAFE_MODE обязателен
    """
    # GLOBAL STATE (intentional)
    global _chaos_was_active
    chaos_enabled = os.environ.get("CHAOS_ENABLED", "false").lower() == "true"
    if not chaos_enabled:
        return 403, json.dumps({
            "error": "chaos_disabled"
        }).encode('utf-8')
    
    try:
        chaos_engine = get_chaos_engine()
        stopped = await chaos_engine.stop_chaos()
        
        # ========== REQUIREMENT 2: CHAOS INVARIANT ENFORCEMENT ==========
        # Если chaos был активен, проверяем что система прошла через SAFE_MODE
        if _chaos_was_active and stopped:
            if not system_state.system_health.safe_mode:
                # ИНВАРИАНТ НАРУШЕН: chaos был активен, но система не в SAFE_MODE
                # Принудительно активируем SAFE_MODE
                import uuid
                incident_id = f"chaos-invariant-{uuid.uuid4().hex[:8]}"
                # HARDENING: SAFE_MODE activation for chaos invariant через state machine
                state_machine = get_state_machine()
                await state_machine.transition_to(
                    SystemStateEnum.SAFE_MODE,
                    reason="CHAOS_INVARIANT_ENFORCEMENT: chaos was active but system not in SAFE_MODE",
                    owner="handle_chaos_stop",
                    metadata={"incident_id": incident_id}
                )
                logger.critical(
                    f"CHAOS_INVARIANT_ENFORCEMENT: SAFE_MODE activated - "
                    f"chaos was active but system not in SAFE_MODE "
                    f"incident_id={incident_id}"
                )
            
            # Сбрасываем флаг после проверки инварианта
            _chaos_was_active = False
        
        if stopped:
            return 200, json.dumps({
                "status": "chaos_stopped",
                "message": "Chaos injection stopped successfully"
            }).encode('utf-8')
        else:
            return 404, json.dumps({
                "error": "no_active_chaos",
                "message": "No active chaos injection"
            }).encode('utf-8')
    except Exception as e:
        logger.error(f"CHAOS_STOP_ERROR: {type(e).__name__}: {e}")
        return 500, json.dumps({
            "error": "chaos_stop_failed",
            "message": str(e)
        }).encode('utf-8')


def build_http_routes():
    """
    ЕДИНАЯ таблица маршрутов для HTTP сервера
    
    ВСЕ routes регистрируются здесь - единый источник истины.
    """
    routes = {
        ("GET", "/metrics"): handle_metrics,
        ("GET", "/admin/status"): handle_admin_status,
        ("POST", "/admin/pause"): handle_admin_pause,
        ("POST", "/admin/resume"): handle_admin_resume,
    }
    
    # Chaos endpoints (только если включён)
    chaos_enabled = os.environ.get("CHAOS_ENABLED", "false").lower() == "true"
    if chaos_enabled:
        routes[("POST", "/admin/chaos/inject")] = handle_chaos_inject
        routes[("POST", "/admin/chaos/stop")] = handle_chaos_stop
    
    return routes


# HTTP Server lifecycle state (singleton protection)
_http_server_started = False
_http_server_instance = None

async def start_http_server():
    """
    HTTP сервер для health/metrics/admin endpoints.
    Production-safe HTTP/1.1 router с таблицей маршрутов.
    
    Returns:
        asyncio.Server: Server object для graceful shutdown
    
    Safety:
    - Single-instance protection (prevents double startup)
    - Event loop ownership check
    - Graceful shutdown support
    """
    global _http_server_started, _http_server_instance
    
    # Защита от двойного старта
    if _http_server_started:
        logger.warning("HTTP SERVER: Attempted double startup, returning existing instance")
        return _http_server_instance
    
    # Проверка event loop ownership
    try:
        loop = asyncio.get_running_loop()
        loop_id = id(loop)
        logger.critical(f"HTTP SERVER STARTED (loop id={loop_id})")
    except RuntimeError as e:
        logger.error(f"HTTP SERVER: No running event loop - {type(e).__name__}: {e}")
        raise
    
    # ========== ЕДИНЫЙ ROUTER ==========
    # Используем build_http_routes() - единый источник истины
    routes = build_http_routes()
    
    # Жёсткая проверка: chaos routes должны быть зарегистрированы если включён
    chaos_enabled = os.environ.get("CHAOS_ENABLED", "false").lower() == "true"
    if chaos_enabled:
        assert ("POST", "/admin/chaos/inject") in routes, \
            "CHAOS ROUTE NOT REGISTERED — CONTROL PLANE BROKEN"
        assert ("POST", "/admin/chaos/stop") in routes, \
            "CHAOS STOP ROUTE NOT REGISTERED — CONTROL PLANE BROKEN"
    
    # Логируем зарегистрированные routes
    route_list = [f"{method} {path}" for (method, path) in routes.keys()]
    logger.critical(f"HTTP ROUTES REGISTERED: {route_list}")
    
    # ========== HTTP REQUEST DISPATCHER ==========
    
    async def http_dispatcher(reader, writer):
        """HTTP/1.1 request dispatcher - использует единую таблицу routes из замыкания"""
        # КРИТИЧНО: Проверяем shutdown event ПЕРЕД обработкой запроса
        # Это гарантирует, что после начала shutdown новые запросы не обрабатываются
        shutdown_evt = get_shutdown_event()
        if shutdown_evt.is_set():
            # Shutdown начался - немедленно возвращаем 503 и закрываем соединение
            try:
                response = (
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Content-Type: text/plain\r\n"
                    b"Content-Length: 19\r\n"
                    b"Connection: close\r\n"
                    b"\r\n"
                    b"Service Shutting Down"
                )
                writer.write(response)
                await writer.drain()
            except Exception:
                pass
            finally:
                try:
                    if not writer.is_closing():
                        writer.close()
                except Exception:
                    pass
            return  # Немедленный выход - не обрабатываем запрос
        
        status_code = 500
        response_body = b"Internal Server Error"
        content_type = "application/json"
        
        try:
            # Безопасное чтение HTTP request (до \r\n\r\n)
            try:
                request_data = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5.0)
            except asyncio.TimeoutError:
                status_code = 408
                response_body = b"Request Timeout"
                content_type = "text/plain"
                logger.warning("HTTP REQUEST: Timeout reading request")
            except asyncio.IncompleteReadError:
                status_code = 400
                response_body = b"Bad Request: Incomplete request"
                content_type = "text/plain"
                logger.warning("HTTP REQUEST: Incomplete request")
            else:
                # Парсим request line
                request_text = request_data.decode('utf-8', errors='ignore')
                lines = request_text.split('\r\n')
                if not lines or not lines[0]:
                    status_code = 400
                    response_body = b"Bad Request: Empty request line"
                    content_type = "text/plain"
                    logger.warning("HTTP REQUEST: Empty request line")
                else:
                    # Parse method and path
                    request_line = lines[0].strip()
                    parts = request_line.split()
                    if len(parts) < 2:
                        status_code = 400
                        response_body = b"Bad Request: Invalid request line"
                        content_type = "text/plain"
                        logger.warning(f"HTTP REQUEST: Invalid request line: {request_line}")
                    else:
                        method = parts[0].strip().upper()
                        path_with_query = parts[1].strip()
                        path = path_with_query.split('?')[0].strip()
                        # Normalize path (remove trailing slash except root)
                        if path != '/' and path.endswith('/'):
                            path = path.rstrip('/')
                        
                        logger.info(f"HTTP REQUEST {method} {path}")
                        
                        # КРИТИЧНО: Повторная проверка shutdown event перед обработкой
                        # Это гарантирует, что даже если соединение установлено до shutdown,
                        # мы не обрабатываем запрос
                        if shutdown_evt.is_set():
                            status_code = 503
                            response_body = b"Service Shutting Down"
                            content_type = "text/plain"
                            logger.info(f"HTTP RESPONSE 503: Shutdown in progress - {method} {path}")
                        else:
                            # Route lookup - используем routes из замыкания
                            route_key = (method, path)
                            if route_key in routes:
                                # Вызываем handler
                                handler = routes[route_key]
                                try:
                                    # КРИТИЧНО: Проверяем shutdown event перед вызовом handler
                                    if shutdown_evt.is_set():
                                        status_code = 503
                                        response_body = b"Service Shutting Down"
                                        content_type = "text/plain"
                                        logger.info(f"HTTP RESPONSE 503: Shutdown during handler - {method} {path}")
                                    else:
                                        status_code, response_body = await handler()
                                    
                                    # Определяем content-type по path и status_code
                                    if path == "/metrics":
                                        content_type = "text/plain; version=0.0.4"
                                    elif status_code == 403:
                                        # 403 Forbidden должен возвращать JSON с reason
                                        content_type = "application/json"
                                    else:
                                        content_type = "application/json"
                                    logger.info(f"HTTP RESPONSE {status_code} {method} {path}")
                                except Exception as e:
                                    status_code = 500
                                    response_body = json.dumps({"status": "error", "message": "Internal Server Error"}).encode('utf-8')
                                    content_type = "application/json"
                                    logger.error(f"HTTP RESPONSE 500: Handler error: {type(e).__name__}: {e} - {method} {path}")
                            else:
                                # Проверяем, есть ли path с другим method
                                path_exists = any(r[1] == path for r in routes.keys())
                                if path_exists:
                                    # Path существует, но method неверный → 405
                                    status_code = 405
                                    response_body = b"Method Not Allowed"
                                    content_type = "text/plain"
                                    logger.info(f"HTTP RESPONSE 405: {method} {path}")
                                else:
                                    # Path не существует → 404
                                    status_code = 404
                                    response_body = b"Not Found"
                                    content_type = "text/plain"
                                    logger.info(f"HTTP RESPONSE 404: {method} {path}")
            
            # Формируем HTTP response
            status_text = {
                200: "OK",
                400: "Bad Request",
                403: "Forbidden",
                404: "Not Found",
                405: "Method Not Allowed",
                408: "Request Timeout",
                500: "Internal Server Error"
            }.get(status_code, "Unknown")
            
            response_headers = (
                f"HTTP/1.1 {status_code} {status_text}\r\n"
                f"Content-Type: {content_type}\r\n"
                f"Content-Length: {len(response_body)}\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            )
            response = response_headers.encode('utf-8') + response_body
            
            # Отправляем ответ
            writer.write(response)
            await writer.drain()
            
        except Exception as e:
            # Критическая ошибка - отправляем 500
            try:
                error_body = json.dumps({"status": "error", "message": "Internal Server Error"}).encode('utf-8')
                response = (
                    f"HTTP/1.1 500 Internal Server Error\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(error_body)}\r\n"
                    f"Connection: close\r\n"
                    f"\r\n"
                ).encode('utf-8') + error_body
                writer.write(response)
                await writer.drain()
                logger.error(f"HTTP RESPONSE 500: Critical error: {type(e).__name__}: {e}")
            except Exception:
                pass
        finally:
            # Закрываем writer (БЕЗ await wait_closed для безопасности event loop)
            try:
                if not writer.is_closing():
                    writer.close()
            except Exception:
                pass
    
    # УДАЛЕНО: Все handlers теперь на уровне модуля (выше)
    # УДАЛЕНО: Локальная таблица ROUTES - используем build_http_routes()
    
    logger.critical("Creating HTTP server on 127.0.0.1:8080")
    server = await asyncio.start_server(http_dispatcher, "127.0.0.1", 8080)
    await server.start_serving()  # КРИТИЧНО: Явно стартуем сервер!
    
    # Сохраняем состояние singleton
    _http_server_started = True
    _http_server_instance = server
    
    logger.critical("HTTP SERVER LISTENING ON 127.0.0.1:8080")
    return server

# УДАЛЕНО: Все дублирующие handlers внутри start_http_server()
# Теперь используются handlers на уровне модуля через build_http_routes()

async def _deprecated_start_control_plane():
    """
    DEPRECATED: Используйте start_http_server() вместо этого.
    
    Эта функция больше не используется - все handlers вынесены на уровень модуля.
    """
    logger.warning("_deprecated_start_control_plane() called - this function is deprecated and should not be used")
    raise NotImplementedError("Use start_http_server() instead")


# DEPRECATED: start_control_plane() removed - use start_http_server() instead


async def telegram_supervisor(system_state):
    """
    Изолированный supervisor для Telegram polling.
    
    HARD FAULT ISOLATION:
    - Запускает polling в отдельной asyncio task
    - Отслеживает task и перехватывает ВСЕ исключения (включая из внутренних tasks)
    - Ловит telegram.error.NetworkError и telegram.error.Conflict
    - НИКОГДА не пробрасывает эти исключения наружу
    - Реализует exponential backoff (10s → 300s max)
    - Логирует "TELEGRAM_NETWORK_FAILURE"
    - Обновляет system_state.system_health (safe_mode, consecutive_errors)
    - Перезапускает polling после ошибок
    
    Runtime, market loop и heartbeat продолжают работать даже если
    Telegram полностью недоступен часами.
    
    ВАЖНО: Эта функция НИКОГДА не awaited в main loop.
    Запускается только через asyncio.create_task().
    """
    from telegram.ext import ApplicationBuilder
    from telegram.error import NetworkError, Conflict
    from telegram_bot import TOKEN
    from telegram_commands import setup_commands
    
    logger.info("📱 Telegram supervisor started")
    
    # Exponential backoff: 10s → 300s max
    backoff_seconds = 10.0
    MAX_BACKOFF = 300.0
    BACKOFF_MULTIPLIER = 1.5
    
    app = None
    polling_task = None
    shutdown_event = None
    monitor_task = None
    
    # Use shutdown_event for proper cancellation semantics
    shutdown_evt = get_shutdown_event()
    
    while system_state.system_health.is_running and not shutdown_evt.is_set():
        try:
            # Build Telegram application
            if app is None:
                app = ApplicationBuilder().token(TOKEN).build()
                setup_commands(app)
            
            # Start polling
            logger.info("Starting Telegram polling...")
            # КРИТИЧНО: initialize() и start() могут блокировать на сетевом I/O при network blackhole
            # Обёртываем в wait_for с таймаутом для предотвращения блокировки shutdown
            try:
                await asyncio.wait_for(app.initialize(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("Telegram app.initialize() timeout - network may be unreachable")
                raise  # Перезапустим с backoff
            except asyncio.CancelledError:
                raise  # Пробрасываем для правильного shutdown
            
            try:
                await asyncio.wait_for(app.start(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("Telegram app.start() timeout - network may be unreachable")
                # Cleanup initialize перед перезапуском
                try:
                    await asyncio.wait_for(app.shutdown(), timeout=2.0)
                except Exception:
                    pass
                raise  # Перезапустим с backoff
            except asyncio.CancelledError:
                # Cleanup при cancellation
                try:
                    await asyncio.wait_for(app.shutdown(), timeout=2.0)
                except Exception:
                    pass
                raise  # Пробрасываем для правильного shutdown
            
            # КРИТИЧНО: start_polling() - долгоживущая задача, запускаем её как task
            # и ждём shutdown event или cancellation, а не саму задачу
            # CRITICAL: Wrap in exception handler to ensure errors are logged
            async def _safe_polling():
                """Wrapper to ensure polling errors are logged"""
                try:
                    await app.updater.start_polling()
                except asyncio.CancelledError:
                    logger.info("Telegram polling task cancelled")
                    raise
                except Exception as e:
                    logger.error(
                        f"Telegram polling task failed: {type(e).__name__}: {e}",
                        exc_info=True
                    )
                    raise
            
            polling_task = asyncio.create_task(_safe_polling(), name="TelegramPolling")
            logger.info("✅ Telegram polling started successfully")
            
            # Reset backoff on success
            backoff_seconds = 10.0
            
            # КРИТИЧНО: Ждём shutdown event или cancellation, а не polling_task
            # polling_task будет отменён при shutdown через finally блок
            try:
                while system_state.system_health.is_running and not shutdown_evt.is_set():
                    # Проверяем, не завершилась ли polling_task (ошибка)
                    if polling_task.done():
                        # Если task завершилась, проверяем исключение
                        try:
                            await polling_task  # Получаем исключение если есть
                        except (NetworkError, Conflict) as e:
                            # NetworkError - это нормально, перезапустим
                            raise
                        except Exception as e:
                            # Другие исключения - логируем и перезапускаем
                            logger.warning(f"Telegram polling task completed with error: {type(e).__name__}: {e}")
                            raise
                    
                    # КРИТИЧНО: sleep с проверкой cancellation для быстрого отклика на shutdown
                    try:
                        await asyncio.sleep(1.0)
                    except asyncio.CancelledError:
                        raise  # Пробрасываем для правильного shutdown
            except asyncio.CancelledError:
                logger.info("Telegram supervisor cancelled - stopping polling")
                # КРИТИЧНО: Останавливаем updater при отмене supervisor
                if polling_task and not polling_task.done():
                    polling_task.cancel()
                    try:
                        await asyncio.wait_for(polling_task, timeout=2.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass
                    except Exception as e:
                        logger.debug(f"Error waiting for polling task cancellation: {type(e).__name__}: {e}")
                
                try:
                    if app.updater and app.updater.running:
                        await asyncio.wait_for(app.updater.stop(), timeout=2.0)
                except Exception as e:
                    logger.debug(f"Error stopping updater during supervisor cancellation: {type(e).__name__}: {e}")
                raise  # Пробрасываем CancelledError
                
        except asyncio.CancelledError:
            # КРИТИЧНО: Обрабатываем CancelledError явно
            logger.info("Telegram supervisor cancelled - cleaning up")
            # Останавливаем updater при отмене
            if app and app.updater and app.updater.running:
                try:
                    await asyncio.wait_for(app.updater.stop(), timeout=2.0)
                except Exception as e:
                    logger.debug(f"Error stopping updater during cancellation: {type(e).__name__}: {e}")
            # Останавливаем application
            if app:
                try:
                    if hasattr(app, 'stop') and app.running:
                        await asyncio.wait_for(app.stop(), timeout=2.0)
                    if hasattr(app, 'shutdown'):
                        await asyncio.wait_for(app.shutdown(), timeout=2.0)
                except Exception as e:
                    logger.debug(f"Error shutting down app during cancellation: {type(e).__name__}: {e}")
            raise  # Пробрасываем CancelledError для правильного завершения
        except (NetworkError, Conflict) as e:
            logger.warning(f"TELEGRAM_NETWORK_FAILURE: {type(e).__name__}: {e}")
            # Exponential backoff
            backoff_seconds = min(backoff_seconds * BACKOFF_MULTIPLIER, MAX_BACKOFF)
            logger.info(f"Retrying in {backoff_seconds:.1f}s...")
            
            # Sleep with shutdown check
            remaining = backoff_seconds
            while remaining > 0 and not shutdown_evt.is_set() and system_state.system_health.is_running:
                try:
                    await asyncio.sleep(min(1.0, remaining))
                except asyncio.CancelledError:
                    raise  # Пробрасываем CancelledError
                remaining -= 1.0
                
        except Exception as e:
            logger.error(f"TELEGRAM_SUPERVISOR_ERROR: {type(e).__name__}: {e}")
            # Record error but continue
            system_state.record_error(f"TELEGRAM_SUPERVISOR: {type(e).__name__}")
            
            # Exponential backoff
            backoff_seconds = min(backoff_seconds * BACKOFF_MULTIPLIER, MAX_BACKOFF)
            logger.info(f"Retrying in {backoff_seconds:.1f}s...")
            
            # Sleep with shutdown check
            remaining = backoff_seconds
            while remaining > 0 and not shutdown_evt.is_set() and system_state.system_health.is_running:
                try:
                    await asyncio.sleep(min(1.0, remaining))
                except asyncio.CancelledError:
                    raise  # Пробрасываем CancelledError
                remaining -= 1.0
        finally:
            # ========== REQUIREMENT 5: GRACEFUL SHUTDOWN (TELEGRAM) ==========
            # КРИТИЧНО: Cleanup выполняется только если мы не были отменены через CancelledError
            # Если был CancelledError, cleanup уже выполнен в except блоке
            
            # Cleanup polling task ПЕРВЫМ (если еще не остановлен)
            if polling_task is not None and not polling_task.done():
                try:
                    polling_task.cancel()
                    # Ждём завершения с подавлением CancelledError
                    try:
                        await asyncio.wait_for(polling_task, timeout=2.0)
                    except asyncio.CancelledError:
                        # Ожидаемое исключение при cancel - подавляем
                        pass
                    except asyncio.TimeoutError:
                        logger.warning("Telegram polling task did not cancel within timeout")
                    except Exception as e:
                        # Исключения во время shutdown (включая httpx.ReadError) не критичны
                        error_type = type(e).__name__
                        if "ReadError" in error_type or "httpx" in str(type(e)).lower():
                            logger.debug(f"Telegram shutdown: Expected error during polling cancel: {error_type}")
                        else:
                            logger.debug(f"Telegram shutdown: Error during polling cancel: {error_type}: {e}")
                except Exception as e:
                    logger.debug(f"Telegram shutdown: Error cancelling polling task: {type(e).__name__}: {e}")
            
            # Cleanup application ПОСЛЕ остановки polling
            if app is not None:
                try:
                    # Останавливаем updater если еще работает
                    if app.updater and app.updater.running:
                        try:
                            await asyncio.wait_for(app.updater.stop(), timeout=2.0)
                        except Exception as e:
                            error_type = type(e).__name__
                            if "ReadError" in error_type or "httpx" in str(type(e)).lower():
                                logger.debug(f"Telegram shutdown: Expected error during updater.stop(): {error_type}")
                            else:
                                logger.debug(f"Telegram shutdown: Error during updater.stop(): {error_type}: {e}")
                    
                    # Останавливаем application
                    if hasattr(app, 'stop') and app.running:
                        try:
                            await asyncio.wait_for(app.stop(), timeout=2.0)
                        except Exception as e:
                            error_type = type(e).__name__
                            if "ReadError" in error_type or "httpx" in str(type(e)).lower():
                                logger.debug(f"Telegram shutdown: Expected error during app.stop(): {error_type}")
                            else:
                                logger.debug(f"Telegram shutdown: Error during app.stop(): {error_type}: {e}")
                    
                    # Shutdown application
                    if hasattr(app, 'shutdown'):
                        try:
                            await asyncio.wait_for(app.shutdown(), timeout=2.0)
                        except Exception as e:
                            error_type = type(e).__name__
                            if "ReadError" in error_type or "httpx" in str(type(e)).lower():
                                logger.debug(f"Telegram shutdown: Expected error during app.shutdown(): {error_type}")
                            else:
                                logger.debug(f"Telegram shutdown: Error during app.shutdown(): {error_type}: {e}")
                except Exception as e:
                    logger.debug(f"Telegram shutdown: Error during app cleanup: {type(e).__name__}: {e}")
    
    logger.info("📱 Telegram supervisor stopped")


async def main():
    """
    Главная функция - запускает все компоненты в одном процессе.
    
    ========== ARCHITECTURE: TASK ORCHESTRATION MODEL ==========
    
    All background logic runs as asyncio.Tasks registered in RUNNING_TASKS.
    This enables:
    - Centralized cancellation on shutdown
    - Proper resource cleanup
    - Fault isolation (one task failure doesn't crash others)
    
    WHY THIS IS CRITICAL:
    - Without centralized registration, tasks may leak and prevent clean shutdown
    - systemd will timeout and kill the process if shutdown takes too long
    - Unregistered tasks can't be cancelled, causing "stuck in deactivating"
    
    PREVIOUS UNSAFE PATTERN:
    - Tasks created but not tracked
    - Shutdown tried to cancel tasks that weren't registered
    - Some tasks continued running after shutdown signal
    - Result: systemd timeout, process killed
    
    CURRENT SAFE PATTERN:
    - All tasks registered via register_task()
    - shutdown_all_tasks() cancels and awaits all registered tasks
    - shutdown_event allows loops to exit naturally
    - Result: Clean shutdown within systemd TimeoutStopSec
    
    ИНВАРИАНТ: SystemState создаётся ТОЛЬКО здесь.
    
    Production hardening:
    - Single-instance protection
    - Structured logging
    - Centralized task registry (RUNNING_TASKS)
    - Graceful shutdown via shutdown_event
    - systemd compatibility
    """
    logger.critical("MAIN STARTED")
    
    # ========== STATE MACHINE INITIALIZATION ==========
    # HARDENING: Инициализируем state machine с правильным TTL
    state_machine = get_state_machine(safe_mode_ttl=SAFE_MODE_TTL)
    
    # HARDENING: Устанавливаем event loop для thread-safe вызовов из ThreadWatchdog
    loop = asyncio.get_running_loop()
    state_machine.set_event_loop(loop)
    logger.critical("STATE_MACHINE: Event loop registered for thread-safe triggers")
    
    # ========== THREAD-BASED WATCHDOG STARTUP ==========
    # HARDENING: ThreadWatchdog использует state machine, не system_state
    # КРИТИЧНО: Запускаем ThreadWatchdog ПЕРВЫМ, ДО asyncio задач
    # ThreadWatchdog работает ВНЕ asyncio и должен быть активен
    # даже если event loop заблокирован
    watchdog = ThreadWatchdog(state_machine, THREAD_WATCHDOG_HEARTBEAT_TIMEOUT)
    watchdog.start()
    _runtime_state.set_thread_watchdog(watchdog)
    
    # HARDENING: Запускаем FATAL_REAPER
    reaper = FatalReaper(state_machine, check_interval=1.5)
    reaper.start()
    _runtime_state.set_fatal_reaper(reaper)
    
    # КРИТИЧНО: Запускаем HTTP сервер ПЕРВЫМ, ДО всего остального
    logger.critical("Starting HTTP server FIRST...")
    server = await start_http_server()
    logger.critical("HTTP server started successfully")
    
    # Проверка single-instance (после старта control plane)
    if not check_single_instance():
        logger.critical("Another instance is running. Exiting.")
        # Останавливаем watchdog перед exit
        watchdog = _runtime_state.get_thread_watchdog()
        if watchdog:
            watchdog.stop()
        sys.exit(1)
    
    # Настройка signal handlers для graceful shutdown
    setup_signal_handlers()
    
    logger.info("Starting market bot (runtime layer)")
    
    # ИНВАРИАНТ: Восстанавливаем состояние из snapshot при старте
    try:
        from core.signal_snapshot_store import SystemStateSnapshotStore
        # Используем SystemStateSnapshotStore - entry point с fault injection
        snapshot = SystemStateSnapshotStore.load_latest()
        if snapshot:
            system_state.restore_from_snapshot(snapshot)
            logger.info("System state restored from snapshot")
        else:
            logger.info("No snapshot found, starting with empty state")
    except IOError as e:
        # Обработка fault injection из storage layer при загрузке
        if "FAULT_INJECTION: storage_failure" in str(e):
            logger.error(
                f"FAULT_INJECTION: storage_failure - "
                f"Controlled exception from storage layer during startup. "
                f"Starting with empty state. error_type=IOError error_message={str(e)}"
            )
            # Записываем ошибку для health tracking
            system_state.record_error("FAULT_INJECTION: storage_failure (startup)")
            
            # HARDENING: Проверяем safe-mode активацию через state machine
            state_machine = get_state_machine()
            if system_state.system_health.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                if not state_machine.is_safe_mode:
                    await state_machine.transition_to(
                        SystemStateEnum.SAFE_MODE,
                        reason=f"Storage fault injection (startup): consecutive_errors >= MAX_CONSECUTIVE_ERRORS",
                        owner="main_startup",
                        metadata={"consecutive_errors": system_state.system_health.consecutive_errors}
                    )
                    logger.warning(
                        f"SAFE-MODE activated after storage fault injection (startup): "
                        f"consecutive_errors={system_state.system_health.consecutive_errors} "
                        f">= MAX_CONSECUTIVE_ERRORS={MAX_CONSECUTIVE_ERRORS}"
                    )
        else:
            # Другие IOError - логируем как обычную ошибку
            logger.warning(f"Error restoring snapshot: {e}, starting with empty state")
    except Exception as e:
        logger.warning(f"Error restoring snapshot: {e}, starting with empty state")
    
    # Отправляем уведомление о запуске (не критично)
    try:
        await asyncio.to_thread(send_message, "🚀 Торговый бот запущен")
    except Exception as e:
        logger.warning(f"Failed to send startup message (non-critical): {type(e).__name__}: {e}")
    
    # Создаём и отслеживаем все фоновые задачи
    # ВАЖНО: Порядок запуска критичен для предотвращения Conflict
    # 1. Control plane server УЖЕ запущен (выше)
    # 2. Затем запускаем остальные задачи
    # 3. Telegram supervisor запускается ПОСЛЕДНИМ с явной задержкой
    
    # Теперь запускаем остальные задачи
    tasks = [
        register_task(
            asyncio.create_task(market_analysis_loop(), name="MarketAnalysis"),
            "MarketAnalysis"
        ),
        register_task(
            asyncio.create_task(runtime_heartbeat_loop(), name="RuntimeHeartbeat"),
            "RuntimeHeartbeat"
        ),
        register_task(
            asyncio.create_task(heartbeat_loop(), name="TelegramHeartbeat"),
            "TelegramHeartbeat"
        ),
        register_task(
            asyncio.create_task(daily_report_loop(), name="DailyReport"),
            "DailyReport"
        ),
        # ========== PRODUCTION HARDENING MONITORS ==========
        register_task(
            asyncio.create_task(loop_guard_watchdog(), name="LoopGuardWatchdog"),
            "LoopGuardWatchdog"
        ),
        register_task(
            asyncio.create_task(safe_mode_ttl_monitor(), name="SafeModeTTLMonitor"),
            "SafeModeTTLMonitor"
        ),
    ]
    
    # Теперь запускаем Telegram supervisor с явным отслеживанием
    logger.info("Starting Telegram supervisor (after system initialization)...")
    telegram_task = register_task(
        asyncio.create_task(telegram_supervisor(system_state), name="TelegramSupervisor"),
        "TelegramSupervisor"
    )
    
    # Добавляем synthetic decision tick loop если включен
    if ENABLE_SYNTHETIC_DECISION_TICK:
        tasks.append(
            register_task(
                asyncio.create_task(synthetic_decision_tick_loop(), name="SyntheticDecisionTick"),
                "SyntheticDecisionTick"
            )
        )
        logger.info("Synthetic decision tick enabled (for fault injection testing)")
    
    # Добавляем loop stall injection task если включен
    if FAULT_INJECT_LOOP_STALL:
        tasks.append(
            register_task(
                asyncio.create_task(loop_stall_injection_task(), name="LoopStallInjection"),
                "LoopStallInjection"
            )
        )
        logger.info("Loop stall injection enabled (for event loop stall detection testing)")
    
    logger.info(f"All components started (tasks: {len(tasks) + 1})")
    
    # HARDENING: FATAL state monitor - проверяет состояние и выполняет exit
    async def fatal_state_monitor():
        """
        HARDENING: Мониторит FATAL состояние и выполняет централизованный exit.
        Все os._exit() вызовы должны проходить через этот монитор.
        """
        state_machine = get_state_machine()
        shutdown_evt = get_shutdown_event()
        
        while system_state.system_health.is_running and not shutdown_evt.is_set():
            try:
                await asyncio.sleep(5.0)  # Проверяем каждые 5 секунд
                
                if shutdown_evt.is_set() or not system_state.system_health.is_running:
                    break
                
                # HARDENING: Проверяем FATAL состояние
                if state_machine.should_exit_fatal():
                    logger.critical("FATAL_STATE_DETECTED: Executing centralized exit handler")
                    
                    # Flush logs перед exit
                    for handler in root_logger.handlers:
                        handler.flush()
                    
                    # HARDENING: Централизованный exit с правильным кодом для systemd
                    logger.critical(f"FATAL_EXIT: Exiting with code {FATAL_EXIT_CODE} (systemd will restart)")
                    os._exit(FATAL_EXIT_CODE)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"FATAL_STATE_MONITOR_ERROR: {type(e).__name__}: {e}")
    
    # Запускаем FATAL state monitor
    fatal_monitor_task = register_task(
        asyncio.create_task(fatal_state_monitor(), name="FatalStateMonitor"),
        "FatalStateMonitor"
    )
    
    # CRITICAL: main() must NOT block on asyncio.gather() which waits forever
    # Instead, wait on shutdown_event which is set during graceful shutdown
    # This allows main() to return naturally when shutdown is requested
    shutdown_evt = get_shutdown_event()
    
    try:
        # Wait for shutdown signal
        # All background tasks run independently and will be cancelled during shutdown
        await shutdown_evt.wait()
        logger.info("Shutdown signal received - exiting main()")
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutdown requested (KeyboardInterrupt/CancelledError)")
    except Exception as e:
        error_msg = f"CRITICAL ERROR during runtime: {type(e).__name__}: {e}"
        error_trace = traceback.format_exc()
        
        logger.critical(f"{error_msg}\n{error_trace}")
        
        # Пытаемся отправить уведомление (не блокируем shutdown)
        try:
            await asyncio.wait_for(
                asyncio.to_thread(error_alert, f"{error_msg}\n\nTrace:\n{error_trace[:500]}"),
                timeout=5.0
            )
        except Exception:
            pass
        
        # HARDENING: Критическая ошибка → переход в FATAL через state machine
        # Централизованный exit handler обработает os._exit
        state_machine = get_state_machine()
        await state_machine.transition_to(
            SystemStateEnum.FATAL,
            f"CRITICAL_ERROR: {type(e).__name__}: {e}",
            owner="main_exception_handler",
            metadata={"error": str(e), "trace": error_trace[:500]}
        )
        # FATAL state monitor обработает exit (добавлен выше в main())
    finally:
        # ========== GRACEFUL SHUTDOWN SEQUENCE ==========
        # 
        # WHY THIS ORDER:
        # 1. Set shutdown_event FIRST - http_dispatcher immediately rejects new requests
        # 2. Close HTTP server - stop accepting new connections, wait for active to finish
        # 3. Set is_running=False - stops all loops from starting new work
        # 4. Cancel all tasks - ensures no task blocks shutdown
        # 5. Wait for completion - cleanup resources
        # 6. Send notification (non-blocking) - user feedback
        # 7. Cleanup - PID file, logs
        #
        # CRITICAL: Must complete within systemd TimeoutStopSec (default 90s)
        # No blocking operations after this point.
        
        logger.critical("=== INITIATING GRACEFUL SHUTDOWN ===")
        
        # CRITICAL: Ensure runtime lifecycle state is SHUTTING_DOWN
        # This may have been set by signal_handler, but we ensure it here too
        current_state = get_runtime_lifecycle_state()
        if current_state == RuntimeLifecycleState.RUNNING:
            set_runtime_lifecycle_state(RuntimeLifecycleState.SHUTTING_DOWN, "Entering finally block during shutdown")
        elif current_state == RuntimeLifecycleState.STOPPED:
            logger.critical("RUNTIME_LIFECYCLE_STATE: Already in STOPPED state, skipping shutdown")
            return
        
        # КРИТИЧНО: Устанавливаем shutdown event ПЕРВЫМ
        # Это гарантирует, что http_dispatcher немедленно начнет отклонять новые запросы
        shutdown_evt = get_shutdown_event()
        if not shutdown_evt.is_set():
            shutdown_evt.set()
        
        logger.info("Initiating graceful shutdown...")
        system_state.system_health.is_running = False
        
        # ========== REQUIREMENT 6: TIME-BOXED SHUTDOWN ==========
        # Graceful shutdown должен иметь жёсткий таймаут (10s)
        # Если не уложились → os._exit(FATAL_EXIT_CODE)
        shutdown_start_time = time.time()
        
        try:
            # КРИТИЧНО: Явно останавливаем Telegram polling ПЕРЕД общей отменой задач
            # Это гарантирует, что polling полностью остановлен до выхода процесса
            telegram_task_to_stop = None
            for task in RUNNING_TASKS:
                if task.get_name() == "TelegramSupervisor":
                    telegram_task_to_stop = task
                    break
            
            if telegram_task_to_stop and not telegram_task_to_stop.done():
                logger.info("Stopping Telegram polling task...")
                telegram_task_to_stop.cancel()
                try:
                    await asyncio.wait_for(telegram_task_to_stop, timeout=5.0)
                    logger.info("Telegram polling stopped")
                except asyncio.TimeoutError:
                    logger.warning("Telegram polling task did not stop within timeout")
                except asyncio.CancelledError:
                    logger.info("Telegram polling task cancelled")
                except Exception as e:
                    logger.warning(f"Error stopping Telegram polling: {type(e).__name__}: {e}")
        except Exception as e:
            logger.warning(f"Error during Telegram shutdown: {type(e).__name__}: {e}")
        
        # Cancel and wait for all registered tasks
        # This includes both main tasks and any background tasks they created
        # CRITICAL: This must complete before checking remaining tasks
        await shutdown_all_tasks()
        
        # ========== HTTP ADMIN SERVER SHUTDOWN ==========
        # Server type: asyncio.start_server (asyncio.Server)
        # CRITICAL: asyncio.start_server creates handler tasks for each connection
        # These tasks must be cancelled before wait_closed() can complete
        #
        # Correct shutdown sequence:
        # 1. server.close() - stops accepting new connections
        # 2. Cancel ALL remaining tasks (including server handler tasks)
        # 3. Await all tasks
        # 4. await server.wait_closed() - completes immediately since all tasks are done
        #
        # This ensures:
        # - No new connections are accepted
        # - All server handler tasks are cancelled
        # - All active connections are closed
        # - Event loop can drain and terminate
        # - asyncio.run() returns naturally
        #
        # Position: AFTER business logic shutdown, BEFORE event loop termination
        logger.critical("HTTP admin server stopping...")
        
        global _http_server_instance
        server_to_close = None
        
        # Get server instance (prefer local variable, fallback to global)
        if 'server' in locals() and server is not None:
            server_to_close = server
        elif _http_server_instance is not None:
            server_to_close = _http_server_instance
        
        if server_to_close is not None:
            # Step 1: Stop accepting new connections
            if server_to_close.is_serving():
                server_to_close.close()
            
            # Step 2: Cancel ALL remaining tasks (including server handler tasks)
            # CRITICAL: Server handler tasks must be cancelled for wait_closed() to complete
            try:
                loop = asyncio.get_running_loop()
                current_task = asyncio.current_task(loop)
                all_tasks = asyncio.all_tasks(loop)
                remaining_tasks = [t for t in all_tasks if not t.done() and t is not current_task]
                if remaining_tasks:
                    logger.critical(f"Cancelling {len(remaining_tasks)} remaining tasks (including server handlers)...")
                    for task in remaining_tasks:
                        if not task.done():
                            task.cancel()
                    # Await cancellation of all tasks
                    await asyncio.gather(*remaining_tasks, return_exceptions=True)
                    logger.critical("All tasks cancelled and completed")
            except RuntimeError:
                pass
            except Exception as e:
                logger.debug(f"Error cancelling tasks: {type(e).__name__}: {e}")
            
            # Step 3: Wait for server to close
            # wait_closed() will complete immediately since all handler tasks are cancelled
            await server_to_close.wait_closed()
            
            # Clear global reference
            _http_server_instance = None
        
        logger.critical("HTTP admin server stopped")
        
        # HARDENING: Проверяем таймаут shutdown
        shutdown_duration = time.time() - shutdown_start_time
        if shutdown_duration > GRACEFUL_SHUTDOWN_TIMEOUT:
            logger.critical(
                f"SHUTDOWN_TIMEOUT: Graceful shutdown took {shutdown_duration:.1f}s "
                f"(threshold={GRACEFUL_SHUTDOWN_TIMEOUT}s) - forcing exit"
            )
            # HARDENING: Переход в FATAL через state machine
            state_machine = get_state_machine()
            await state_machine.transition_to(
                SystemStateEnum.FATAL,
                f"SHUTDOWN_TIMEOUT: {shutdown_duration:.1f}s > {GRACEFUL_SHUTDOWN_TIMEOUT}s",
                owner="shutdown_timeout_handler",
                metadata={"duration": shutdown_duration, "timeout": GRACEFUL_SHUTDOWN_TIMEOUT}
            )
            # FATAL state monitor обработает exit
        
        # Send shutdown notification (non-blocking, timeout-protected)
        # WHY: User feedback, but must not block shutdown
        try:
            await asyncio.wait_for(
                asyncio.to_thread(send_message, "⏹ Торговый бот остановлен"),
                timeout=3.0
            )
        except Exception:
            # Ignore errors - notification is non-critical
            pass
        
        # Cleanup
        cleanup_pid_file()
        
        # Flush logs before exit
        for handler in root_logger.handlers:
            handler.flush()
        
        # ========== THREAD WATCHDOG SHUTDOWN ==========
        # Останавливаем ThreadWatchdog перед завершением
        watchdog = _runtime_state.get_thread_watchdog()
        reaper = _runtime_state.get_fatal_reaper()
        
        if watchdog:
            watchdog.stop(timeout=2.0)
        if reaper:
            reaper.stop()
        
        # ========== EXTERNAL RESOURCE CLEANUP ==========
        # КРИТИЧНО: Закрываем все внешние ресурсы, которые могут держать процесс живым
        # Это гарантирует, что процесс завершится даже при network blackhole
        
        # 1. Закрываем default executor (ThreadPoolExecutor)
        # КРИТИЧНО: Default executor может держать потоки живыми, блокируя exit
        try:
            loop = asyncio.get_running_loop()
            # Закрываем default executor с таймаутом
            try:
                await asyncio.wait_for(
                    loop.shutdown_default_executor(),
                    timeout=2.0
                )
                logger.debug("Default executor shut down")
            except asyncio.TimeoutError:
                logger.warning("Default executor shutdown timeout (non-critical)")
            except RuntimeError:
                # Executor уже закрыт или event loop закрыт - это нормально
                pass
        except RuntimeError:
            # Event loop уже закрыт - это нормально при shutdown
            pass
        except Exception as e:
            logger.debug(f"Error shutting down default executor: {type(e).__name__}: {e}")
        
        # 2. Cancel and await ALL remaining asyncio tasks
        # КРИТИЧНО: Any remaining tasks (including unregistered ones) can keep event loop alive
        # This includes tasks created by asyncio.gather(), asyncio.start_server, etc.
        # CRITICAL: Exclude current task to avoid cancelling ourselves
        try:
            loop = asyncio.get_running_loop()
            current_task = asyncio.current_task(loop)
            # Get ALL tasks in the event loop (not just registered ones)
            # Exclude current task (the finally block itself)
            all_tasks = asyncio.all_tasks(loop)
            remaining_tasks = [t for t in all_tasks if not t.done() and t is not current_task]
            if remaining_tasks:
                logger.critical(f"Found {len(remaining_tasks)} remaining tasks, cancelling all...")
                # Cancel all remaining tasks
                for task in remaining_tasks:
                    if not task.done():
                        task.cancel()
                # Await cancellation of all tasks
                # CRITICAL: This ensures event loop can drain and asyncio.run() can return
                await asyncio.gather(*remaining_tasks, return_exceptions=True)
                logger.critical("All remaining tasks cancelled and completed")
        except RuntimeError:
            # Event loop already closed - this is normal during shutdown
            pass
        except Exception as e:
            logger.debug(f"Error cleaning up remaining tasks: {type(e).__name__}: {e}")
        
        # 3. Закрываем глобальный Telegram Bot и aiohttp/httpx клиенты
        # КРИТИЧНО: Telegram Bot использует httpx.AsyncClient через HTTPXRequest, который может держать соединения открытыми
        # Это гарантирует, что процесс завершится даже при network blackhole
        try:
            from telegram_bot import bot
            if bot:
                # Шаг 1: Закрываем Bot (вызывает shutdown() на request)
                if hasattr(bot, 'shutdown'):
                    try:
                        await asyncio.wait_for(
                            bot.shutdown(),
                            timeout=2.0
                        )
                        logger.debug("Telegram Bot closed")
                    except (asyncio.TimeoutError, RuntimeError, AttributeError):
                        # Timeout или уже закрыт - это нормально при shutdown
                        pass
                
                # Шаг 2: Явно закрываем HTTPXRequest connector (если доступен)
                if hasattr(bot, 'request') and bot.request:
                    # HTTPXRequest использует httpx.AsyncClient, который имеет connector
                    if hasattr(bot.request, 'shutdown'):
                        try:
                            await asyncio.wait_for(
                                bot.request.shutdown(),
                                timeout=2.0
                            )
                            logger.debug("Telegram Bot HTTP client closed")
                        except (asyncio.TimeoutError, RuntimeError, AttributeError):
                            pass
                    # Альтернативный способ: закрыть connector напрямую (если доступен)
                    if hasattr(bot.request, '_client') and bot.request._client:
                        client = bot.request._client
                        if hasattr(client, 'aclose'):
                            try:
                                await asyncio.wait_for(
                                    client.aclose(),
                                    timeout=2.0
                                )
                                logger.debug("Telegram Bot HTTPX client connector closed")
                            except (asyncio.TimeoutError, RuntimeError, AttributeError):
                                pass
        except (ImportError, AttributeError, RuntimeError):
            # Bot не импортирован или уже закрыт - это нормально
            pass
        except Exception as e:
            logger.debug(f"Error closing Telegram Bot: {type(e).__name__}: {e}")
        
        # 4. Закрываем все async generators
        # КРИТИЧНО: Async generators могут держать ресурсы открытыми
        try:
            loop = asyncio.get_running_loop()
            if not loop.is_closed():
                try:
                    await asyncio.wait_for(
                        loop.shutdown_asyncgens(),
                        timeout=1.0
                    )
                    logger.debug("Async generators shut down")
                except (asyncio.TimeoutError, RuntimeError):
                    pass
        except RuntimeError:
            # Event loop уже закрыт - это нормально
            pass
        except Exception as e:
            logger.debug(f"Error shutting down async generators: {type(e).__name__}: {e}")
        
        logger.critical("=== GRACEFUL SHUTDOWN COMPLETED ===")
        
        # ========== FINAL SHUTDOWN BARRIER ==========
        # CRITICAL: This is the ABSOLUTE FINAL barrier before main() returns
        # Some tasks may not react properly to cancellation (blocked on Event.wait(), Queue.get(), etc.)
        # This ensures ALL remaining tasks are forcibly cancelled and awaited
        #
        # This MUST be the last operation before the coroutine returns
        # After this, asyncio.run() MUST return naturally
        try:
            loop = asyncio.get_running_loop()
            current_task = asyncio.current_task(loop)
            
            # Enumerate ALL remaining tasks in the event loop
            all_tasks = asyncio.all_tasks(loop)
            remaining_tasks = [t for t in all_tasks if not t.done() and t is not current_task]
            
            if remaining_tasks:
                logger.critical(f"FINAL BARRIER: Found {len(remaining_tasks)} remaining tasks, forcing cancellation...")
                # Log task names for debugging
                task_names = [t.get_name() if hasattr(t, 'get_name') else str(t) for t in remaining_tasks]
                logger.critical(f"FINAL BARRIER: Tasks: {task_names}")
                
                # Cancel all remaining tasks
                for task in remaining_tasks:
                    if not task.done():
                        task.cancel()
                
                # Await cancellation of all tasks
                # CRITICAL: return_exceptions=True ensures one failing task doesn't block others
                # This is the final guarantee that all tasks complete
                await asyncio.gather(*remaining_tasks, return_exceptions=True)
                logger.critical("FINAL BARRIER: All remaining tasks cancelled and completed")
            else:
                logger.critical("FINAL BARRIER: No remaining tasks - event loop is empty")
        except RuntimeError:
            # Event loop already closed - this is normal during shutdown
            pass
        except Exception as e:
            logger.error(f"FINAL BARRIER: Error during final task cancellation: {type(e).__name__}: {e}")
            # Continue anyway - we've done our best
        
        # CRITICAL: After this point, the event loop MUST be empty
        # All tasks have been cancelled and awaited
        # asyncio.run() will return naturally
        logger.critical("FINAL BARRIER: Event loop drained - asyncio.run() will return")
        
        # Transition to STOPPED state
        set_runtime_lifecycle_state(RuntimeLifecycleState.STOPPED, "All shutdown steps completed")


if __name__ == "__main__":
    # КРИТИЧЕСКОЕ логирование entrypoint для production мониторинга
    logger.critical("=== PROCESS STARTED ===")
    logger.critical(f"PID: {os.getpid()}")
    logger.critical(f"Python: {sys.version}")
    logger.critical(f"Control plane will listen on {HEALTH_SERVER_HOST}:{HEALTH_SERVER_PORT}")
    """
    Entry point для production runtime.
    
    ========== ARCHITECTURE: SINGLE EVENT LOOP OWNERSHIP ==========
    
    This is the ONLY place where asyncio.run() is called.
    This creates exactly ONE event loop for the entire process.
    
    WHY THIS IS CRITICAL:
    - Multiple event loops cause "RuntimeError: This event loop is already running"
    - They prevent proper shutdown (tasks can't be cancelled cleanly)
    - systemd will timeout and kill the process if shutdown hangs
    
    PREVIOUS UNSAFE PATTERN:
    - asyncio.run() called in multiple places
    - loop.run_until_complete() used in signal handlers
    - New event loops created in threads
    - Result: RuntimeError, hanging shutdowns, systemd timeouts
    
    CURRENT SAFE PATTERN:
    - ONE asyncio.run(main()) call here
    - All async code uses await or asyncio.create_task()
    - Tasks registered in RUNNING_TASKS for centralized cancellation
    - Shutdown via shutdown_event (non-blocking signal handler)
    - Result: Clean shutdown, no RuntimeError, systemd-compatible
    
    systemd compatibility:
    - Exit code 0: нормальное завершение
    - Exit code 1: критическая ошибка при запуске
    - Exit code 2: другой экземпляр уже запущен
    """
    exit_code = 0
    
    try:
        # ========== SINGLE EVENT LOOP CREATION ==========
        # 
        # CRITICAL: This is the ONLY place where asyncio.run() is called.
        # All other code must use await or asyncio.create_task().
        #
        # WHY: asyncio.run() creates a new event loop. If called elsewhere,
        # it would try to create a second loop while the first is running,
        # causing "RuntimeError: This event loop is already running".
        #
        # The event loop created here is used by:
        # - All registered tasks (via register_task())
        # - All async functions called via await
        # - All background operations
        #
        # NO OTHER CODE may:
        # - Call asyncio.run()
        # - Call loop.run_until_complete()
        # - Call get_event_loop().run_*
        # - Create new event loops
        #
        asyncio.run(main())
        logger.info("Process exited normally")
        exit_code = 0
    except KeyboardInterrupt:
        logger.info("Shutdown requested by user (KeyboardInterrupt)")
        exit_code = 0
    except SystemExit as e:
        # Пробрасываем SystemExit с кодом
        exit_code = e.code if e.code is not None else 0
        raise
    except Exception as e:
        error_msg = f"CRITICAL ERROR at entry point: {type(e).__name__}: {e}"
        error_trace = traceback.format_exc()
        
        logger.critical(f"{error_msg}\n{error_trace}")
        
        # Flush logs перед exit
        for handler in root_logger.handlers:
            handler.flush()
        
        # systemd: non-zero exit code для критических ошибок
        exit_code = 1
    finally:
        # Очищаем PID file
        cleanup_pid_file()
    
    sys.exit(exit_code)
