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
from utils.env import env_flag
from error_alert import error_alert
from telegram_bot import send_message, send_message_async
from health_monitor import send_heartbeat, send_heartbeat_async, HEARTBEAT_INTERVAL
from loops import fault_injection, monitors, paper_monitor, periodic, runtime_heartbeat, telegram_supervisor

# Новые модули для контролируемой архитектуры
from system_state_machine import get_state_machine, SystemState as SystemStateEnum
from systemd_integration import get_systemd_integration, ExitCode

# Импорты для анализа рынка (будем вызывать напрямую)
from config import SYMBOLS
from data_loader import validate_symbols

# Символы, прошедшие проверку при старте (None = ещё не валидировалось)
_active_symbols: list = []

# Экосистема

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

# Validate interval bounds at startup
if ADAPTIVE_INTERVAL_MIN >= ADAPTIVE_INTERVAL_MAX:
    raise ValueError(
        f"ADAPTIVE_INTERVAL_MIN ({ADAPTIVE_INTERVAL_MIN}) must be strictly less than "
        f"ADAPTIVE_INTERVAL_MAX ({ADAPTIVE_INTERVAL_MAX})"
    )
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
STARTUP_GRACE_SECONDS = 60.0  # Первые 60с после старта — stall check пропускается

# ========== PRODUCTION HARDENING CONSTANTS ==========
HEARTBEAT_MISS_ENFORCEMENT_THRESHOLD = 2  # После 2 пропущенных heartbeats → SAFE_MODE
LOOP_GUARD_TIMEOUT = 300.0  # 300 секунд - максимальное время блокировки event loop
ITERATION_BUDGET_SECONDS = 60.0  # 60 секунд - жесткий лимит времени на одну итерацию анализа (с большим запасом от LOOP_GUARD_TIMEOUT)
SAFE_MODE_TTL = 600.0  # 600 секунд (10 минут) - TTL для SAFE_MODE
# В SAFE_MODE цикл анализа ходит не реже этого интервала: AUTO_RESUME_SUCCESS_CYCLES
# чистых оборотов должны уложиться в SAFE_MODE_TTL, иначе автомат уйдёт в FATAL раньше,
# чем система докажет, что здорова (при 300 с три оборота — 900 с > 600 с TTL).
SAFE_MODE_RECOVERY_INTERVAL = SAFE_MODE_TTL / (AUTO_RESUME_SUCCESS_CYCLES + 2)
GRACEFUL_SHUTDOWN_TIMEOUT = 10.0  # 10 секунд - жёсткий таймаут на graceful shutdown


# ========== CHAOS TRACKING (для инварианта) ==========
# Флаг «был ли chaos активен» (REQUIREMENT 2) — cp_state.chaos["was_active"]
# в control_plane/state.py: его пишут обработчики хаоса, читает runtime_heartbeat_loop.
# HARDENING: _safe_mode_entered_at УДАЛЕН - теперь управляется state machine


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
def setup_structured_logging(enable_file: bool = False):
    """
    Настраивает структурированное логирование.

    enable_file=False (при импорте) — только stdout. Файловый handler создаёт файл
    на диске, а импорт модуля не должен ничего создавать: `import runner` в тестах
    оставлял после себя runner.log, а в контейнере под non-root падал бы
    PermissionError на /data/logs ещё до первой строки кода.

    enable_file=True вызывается из main(). В Docker файл не нужен вовсе: stdout
    забирает json-file драйвер, который умеет ротацию, — в отличие от FileHandler,
    который писал бы в один файл без ограничения размера.
    """
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    formatter = StructuredFormatter()

    # Console handler (для systemd/journalctl/docker logs) — всегда
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    if enable_file:
        # RotatingFileHandler, а не FileHandler: logrotate на хосте не покрывал
        # /data/logs, а постоянно дописываемый файл не «стареет» и под mtime-чистку
        # не попадает — рос без границы.
        from logging.handlers import RotatingFileHandler
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=50 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # Токен бота — часть URL запросов к Telegram (/bot<токен>/getUpdates), а httpx
    # на уровне INFO пишет каждый URL. Так утёк прежний токен (строки лога попали в
    # tests/rso_report_*.json, а те — в публичный репозиторий), и так же в логах
    # контейнера оказался новый после выкладки 10.09.2026.
    from utils.log_redaction import install_log_redaction
    install_log_redaction(root)

    return root

# Инициализируем логирование (без файла — файл подключает main())
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
        # Re-arm ThreadWatchdog so it can detect future stalls
        # Сторож живёт в _runtime_state. Раньше здесь читалось имя _thread_watchdog,
        # которого в модуле нет вовсе (flake8 молчал из-за лишнего global): каждое
        # успешное восстановление падало с NameError, и сторож не перевзводился.
        watchdog = get_thread_watchdog()
        if watchdog is not None:
            with watchdog.lifecycle_lock:
                watchdog.lifecycle_state = ThreadWatchdogState.ARMED
                watchdog.triggered = False
            logger.info("ThreadWatchdog re-armed after recovery")
    return success

def _sync_flags_from_machine() -> None:
    """
    Слушатель переходов автомата (регистрирует main()): флаги system_health.safe_mode
    и trading_paused — из состояния автомата и ручной паузы. До 11.09.2026 переход в
    SAFE_MODE флаги не трогал: торговлю держал предохранитель (он смотрит сам автомат),
    но Decision Core, отчёты и восстановление в цикле анализа видели «всё в порядке».
    """
    get_state_machine().sync_to_system_state(
        system_state, manual_pause_active=_control_plane_state.get("manual_pause_active", False))


# ========== ОБЩЕЕ СОСТОЯНИЕ ПРОЦЕССА ==========
# Живёт в control_plane/state.py (пункт 5 плана отложенного, шаг 6б). Здесь —
# ссылки на те же объекты под прежними именами: изменения по ключам видны всем,
# кто их держит, включая команды бота (telegram_commands импортирует функции runner).
from control_plane import state as cp_state
_analysis_metrics = cp_state.analysis_metrics
ANALYSIS_DURATION_BUCKETS = cp_state.ANALYSIS_DURATION_BUCKETS
_prometheus_metrics = cp_state.prometheus_metrics
_adaptive_system_state = cp_state.adaptive_system_state
_control_plane_state = cp_state.control_plane_state
_TimeoutLock = cp_state.TimeoutLock
_metrics_lock = cp_state.metrics_lock
_get_admin_lock = cp_state.get_admin_lock


# Функции метрик живут в control_plane/state.py (шаг 6в); здесь прежние имена:
# их зовёт цикл анализа, а команды бота импортируют их из runner.
get_analysis_metrics = cp_state.get_analysis_metrics
update_analysis_metrics = cp_state.update_analysis_metrics
get_prometheus_metrics = cp_state.get_prometheus_metrics
record_analysis_duration = cp_state.record_analysis_duration
increment_scheduler_stalls = cp_state.increment_scheduler_stalls
increment_analysis_cycles = cp_state.increment_analysis_cycles
get_adaptive_system_state = cp_state.get_adaptive_system_state
update_volatility_state = cp_state.update_volatility_state


def pause_trading_manually():
    """
    Приостанавливает торговлю вручную (через admin/telegram).
    
    Returns:
        bool: True если успешно, False если уже приостановлена
    """
    
    with _metrics_lock:
        if _control_plane_state["manual_pause_active"]:
            return False  # Уже приостановлена
        _control_plane_state["manual_pause_active"] = True
        _prometheus_metrics["admin_commands_total"]["pause"]["success"] += 1
        _adaptive_system_state["recovery_cycles"] = 0

    # HARDENING: Синхронизируем trading_paused через state machine (вне лока — не использует _metrics_lock)
    state_machine = get_state_machine()
    state_machine.sync_to_system_state(system_state, manual_pause_active=True)

    logger.info("Trading paused manually via control plane")
    return True

def resume_trading_manually():
    """
    Возобновляет торговлю вручную (через admin/telegram).

    Returns:
        tuple: (success: bool, message: str)
    """

    # TOCTOU fix: all state checks and mutation happen inside a single lock
    # acquisition to prevent another thread from changing state between check
    # and set.
    with _metrics_lock:
        # Проверяем safe_mode (имеет приоритет) — under lock to avoid TOCTOU
        if system_state.system_health.safe_mode:
            return (False, "Cannot resume: system is in safe_mode")

        # Проверяем, не активна ли уже manual pause
        if not _control_plane_state["manual_pause_active"]:
            return (False, "Trading is already active")

        _control_plane_state["manual_pause_active"] = False
        _prometheus_metrics["admin_commands_total"]["resume"]["success"] += 1
        _adaptive_system_state["recovery_cycles"] = 0

    # HARDENING: Синхронизируем trading_paused через state machine (вне лока — не использует _metrics_lock)
    state_machine = get_state_machine()
    state_machine.sync_to_system_state(system_state, manual_pause_active=False)

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
    now = time.monotonic()
    with _metrics_lock:
        last_sent = _alert_last_sent.get(alert_key, 0.0)
    return (now - last_sent) >= ALERT_COOLDOWN

def _mark_alert_sent(alert_key: str):
    """Отмечает, что алерт был отправлен"""
    with _metrics_lock:
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
                logger.warning("WARN alert: Analysis duration %.2fs > %.2fs", duration, ALERT_ANALYSIS_TIME)
        
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
                logger.warning("WARN alert: Consecutive errors %s >= %s", system_state.system_health.consecutive_errors, WARN_ERROR_THRESHOLD)
        
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
                logger.warning("WARN alert: Volatility %.3f > %.3f", volatility, VOLATILITY_THRESHOLD)
        
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
                logger.error("CRITICAL alert: Analysis duration %.2fs > %.2fs", duration, MAX_ANALYSIS_TIME)
        
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
                logger.error("CRITICAL alert: Consecutive errors %s >= %s", system_state.system_health.consecutive_errors, CRITICAL_ERROR_THRESHOLD)
        
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
                logger.error("CRITICAL alert: System entered safe_mode")
        
        # CRITICAL: Scheduler stall detected (via heartbeat miss)
        # Пропускаем проверку во время стартового grace period — предотвращает ложные срабатывания при инициализации
        if uptime >= STARTUP_GRACE_SECONDS and system_state.system_health.last_heartbeat:
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
                    logger.error("CRITICAL alert: Scheduler stall detected (missed %d heartbeats)", missed_heartbeats)
        
        # Отправляем все алерты (неблокирующе)
        for alert in alerts_to_send:
            try:
                # Отправляем через Telegram (неблокирующе)
                await send_message_async(alert["message"])
                logger.info("Alert sent: %s - %s", alert['level'], alert['type'])
                
                # HARDENING: CRITICAL alerts: приостанавливаем торговлю через manual pause
                # FIX: используем _metrics_lock для thread-safe мутации (как в pause_trading_manually)
                if alert.get("pause_trading") and alert["level"] == "CRITICAL":
                    with _metrics_lock:
                        _control_plane_state["manual_pause_active"] = True
                        _adaptive_system_state["recovery_cycles"] = 0
                    state_machine = get_state_machine()
                    state_machine.sync_to_system_state(system_state, manual_pause_active=True)
                    logger.error("Trading paused due to CRITICAL alert: %s", alert['type'])
                    
            except asyncio.TimeoutError:
                logger.warning("Timeout sending alert: %s - %s", alert['level'], alert['type'])
            except Exception as e:
                logger.warning("Error sending alert %s - %s: %s: %s", alert['level'], alert['type'], type(e).__name__, e)
                
    except Exception as e:
        # Не блокируем analysis loop при ошибках в алертах
        logger.warning("Error in alert evaluation: %s: %s", type(e).__name__, e)

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
                logger.warning("Another instance is running (PID: %d). Exiting.", old_pid)
                return False
            except ProcessLookupError:
                # Процесс не существует - старый PID file
                logger.info("Removing stale PID file (PID: %d no longer exists)", old_pid)
                pid_path.unlink()
        except (ValueError, IOError) as e:
            logger.warning("Error reading PID file: %s. Removing it.", e)
            try:
                pid_path.unlink()
            except Exception:
                logger.debug("Failed to unlink stale PID file", exc_info=True)

    # Создаём новый PID file
    try:
        with open(pid_path, 'w') as f:
            f.write(str(os.getpid()))
        logger.info("PID file created: %s (PID: %d)", PID_FILE, os.getpid())
        return True
    except Exception as e:
        logger.error("Failed to create PID file: %s", e)
        return False

def cleanup_pid_file():
    """Удаляет PID file при завершении"""
    pid_path = Path(PID_FILE)
    if pid_path.exists():
        try:
            pid_path.unlink()
            logger.info("PID file removed")
        except Exception as e:
            logger.warning("Failed to remove PID file: %s", e)

# ========== СТОРОЖА ПРОЦЕССА ==========
# ThreadWatchdog, FatalReaper, метки сердцебиения — в control_plane/watchdogs.py
# (пункт 5 плана отложенного, шаг 9). Здесь прежние имена: ими пользуются main(),
# восстановление из SAFE_MODE и тесты. system_state сторожам передаётся здесь.
from control_plane import watchdogs
from control_plane.watchdogs import (
    FATAL_EXIT_CODE,
    THREAD_WATCHDOG_HEARTBEAT_TIMEOUT,
    FatalReaper,
    ThreadWatchdog,
    ThreadWatchdogState,
    _runtime_state,
    get_thread_watchdog,
    update_heartbeat_thread_safe,
)

watchdogs.configure(system_state=system_state)


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
                "RUNTIME_LIFECYCLE_STATE_TRANSITION_DENIED: from=%s to=%s reason=%s",
                old_state.value, new_state.value, reason
            )
            return False
        
        _runtime_lifecycle_state = new_state
        logger.critical(
            "RUNTIME_LIFECYCLE_STATE_TRANSITION: from=%s to=%s reason=%s",
            old_state.value, new_state.value, reason
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
            "RUNTIME_LIFECYCLE_STATE: Shutdown already in progress (state=%s), ignoring %s signal",
            current_state.value, signal_name
        )
        return
    
    # Transition to SHUTTING_DOWN
    if not set_runtime_lifecycle_state(RuntimeLifecycleState.SHUTTING_DOWN, f"Received {signal_name} signal"):
        logger.critical("RUNTIME_LIFECYCLE_STATE: Failed to transition to SHUTTING_DOWN, already shutting down")
        return
    
    logger.critical("Received %s signal. Initiating graceful shutdown...", signal_name)
    
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
            logger.warning("Error stopping ThreadWatchdog: %s: %s", type(e).__name__, e)
    
    if reaper:
        try:
            reaper.stop()
        except Exception as e:
            logger.warning("Error stopping FATAL_REAPER: %s: %s", type(e).__name__, e)
    
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
            "RUNTIME_LIFECYCLE_STATE: Task registration blocked - runtime lifecycle state is %s, cannot register task '%s'",
            current_state.value, name
        )
        # Cancel the task immediately since we can't register it
        task.cancel()
        return task
    
    task.set_name(name)
    RUNNING_TASKS.add(task)
    logger.debug("Task registered: %s (total: %d)", name, len(RUNNING_TASKS))
    
    def task_done_callback(t: asyncio.Task):
        """Auto-removes task from registry when done; logs unhandled exceptions."""
        RUNNING_TASKS.discard(t)
        if not t.cancelled():
            exc = t.exception()
            if exc:
                logger.error(
                    "Task '%s' failed with unhandled exception: %s: %s",
                    name, type(exc).__name__, exc,
                    exc_info=exc,
                )
        logger.debug("Task completed: %s (remaining: %d)", name, len(RUNNING_TASKS))
    
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
    logger.info("Cancelling %d registered tasks...", len(tasks_to_cancel))
    
    # Cancel all tasks with logging
    for task in tasks_to_cancel:
        task_name = task.get_name() if hasattr(task, 'get_name') else str(task)
        if not task.done():
            logger.debug("Cancelling task: %s", task_name)
            task.cancel()
        else:
            logger.debug("Task already done: %s", task_name)
    
    # Wait for completion with logging
    # CRITICAL: Use return_exceptions=True so one failing task doesn't block others
    # CRITICAL: Await without timeout to ensure all tasks complete
    results = await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
    
    # Log completion status for each task
    for task, result in zip(tasks_to_cancel, results):
        task_name = task.get_name() if hasattr(task, 'get_name') else str(task)
        if isinstance(result, Exception):
            if isinstance(result, asyncio.CancelledError):
                logger.debug("Task cancelled: %s", task_name)
            else:
                logger.warning("Task completed with exception: %s: %s: %s", task_name, type(result).__name__, result)
        else:
            logger.debug("Task completed successfully: %s", task_name)
    
    logger.info("All %d registered tasks cancelled and completed", len(tasks_to_cancel))


# ========== ЦИКЛ АНАЛИЗА РЫНКА ==========
# Живёт в loops/market_analysis.py (пункт 5 плана отложенного, шаг 8б). Всё, что
# принадлежит runner, — настройки, его функции, system_state, RUNNING_TASKS —
# передаётся туда один раз, здесь, под прежними именами.
from loops import market_analysis


def _get_active_symbols() -> list:
    """Для цикла анализа: main() переприсваивает _active_symbols после проверки символов."""
    return _active_symbols


market_analysis.configure(
    ADAPTIVE_INTERVAL_ENABLED=ADAPTIVE_INTERVAL_ENABLED,
    ADAPTIVE_INTERVAL_MAX=ADAPTIVE_INTERVAL_MAX,
    ADAPTIVE_INTERVAL_MIN=ADAPTIVE_INTERVAL_MIN,
    ADAPTIVE_INTERVAL_MULTIPLIER=ADAPTIVE_INTERVAL_MULTIPLIER,
    ADAPTIVE_STABLE_CYCLES=ADAPTIVE_STABLE_CYCLES,
    ANALYSIS_INTERVAL=ANALYSIS_INTERVAL,
    AUTO_RESUME_SAFE_MODE_DELAY=AUTO_RESUME_SAFE_MODE_DELAY,
    AUTO_RESUME_SUCCESS_CYCLES=AUTO_RESUME_SUCCESS_CYCLES,
    AUTO_RESUME_TRADING_ENABLED=AUTO_RESUME_TRADING_ENABLED,
    ERROR_PAUSE=ERROR_PAUSE,
    ITERATION_BUDGET_SECONDS=ITERATION_BUDGET_SECONDS,
    MAX_ANALYSIS_TIME=MAX_ANALYSIS_TIME,
    MAX_CONSECUTIVE_ERRORS=MAX_CONSECUTIVE_ERRORS,
    METRICS_LOG_INTERVAL=METRICS_LOG_INTERVAL,
    RUNNING_TASKS=RUNNING_TASKS,
    SAFE_MODE_RECOVERY_INTERVAL=SAFE_MODE_RECOVERY_INTERVAL,
    evaluate_and_send_alerts=evaluate_and_send_alerts,
    exit_safe_mode_via_recovery=exit_safe_mode_via_recovery,
    get_shutdown_event=get_shutdown_event,
    system_state=system_state,
    get_active_symbols=_get_active_symbols,
)


def _state():
    """Для задач из loops/: текущий system_state, даже если его заменили."""
    return system_state


def _is_running() -> bool:
    """Для задач из loops/: читает текущий system_state, даже если его заменили."""
    return system_state.system_health.is_running


# ========== HTTP-ПАНЕЛЬ УПРАВЛЕНИЯ ==========
# Обработчики и сервер живут в control_plane/http.py (пункт 5 плана отложенного,
# шаг 6в). Настройки процесса передаются туда здесь, один раз; состояние процесса
# сервер берёт функцией _state на каждый запрос (см. main()).
import time

from control_plane import http as cp_http
cp_http.configure(analysis_interval=ANALYSIS_INTERVAL,
                  auto_resume_enabled=AUTO_RESUME_TRADING_ENABLED,
                  auto_resume_success_cycles=AUTO_RESUME_SUCCESS_CYCLES)



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
    # Файловый лог подключаем здесь, а не при импорте: до этой точки модуль обязан
    # импортироваться в любом окружении (тесты, статический анализ), ничего не создавая
    # на диске. По умолчанию файла нет вовсе — логи забирает stdout, а в Docker их
    # подхватывает json-file драйвер, который умеет ротацию. Прежний FileHandler писал
    # в один файл без ограничения размера, а logrotate на хосте был настроен на другой
    # путь и до него не доставал.
    if env_flag("LOG_TO_FILE", default=False):
        setup_structured_logging(enable_file=True)
        logger.info("Логирование: stdout + файл %s (ротация 50 МБ × 3)", LOG_FILE)

    logger.critical("MAIN STARTED")

    # ========== STATE MACHINE INITIALIZATION ==========
    # HARDENING: Инициализируем state machine с правильным TTL
    state_machine = get_state_machine(safe_mode_ttl=SAFE_MODE_TTL)
    # Флаги system_health следуют за автоматом после каждого перехода (11.09.2026)
    state_machine.set_transition_listener(_sync_flags_from_machine)
    
    # HARDENING: Устанавливаем event loop для thread-safe вызовов из ThreadWatchdog
    loop = asyncio.get_running_loop()
    state_machine.set_event_loop(loop)
    logger.critical("STATE_MACHINE: Event loop registered for thread-safe triggers")

    # HARDENING: Регистрируем loop в AsyncToSyncAdapter для вызовов из worker threads
    # (generate_signals_for_symbols запускается через asyncio.to_thread — без этого
    #  get_running_loop() падает с RuntimeError и все сигналы блокируются fail-safe)
    from core.system_guardian import AsyncToSyncAdapter
    AsyncToSyncAdapter.set_main_loop(loop)
    logger.critical("ASYNC_TO_SYNC_ADAPTER: Main event loop registered")
    
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
    server = await cp_http.start_http_server(_state, get_shutdown_event())
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
                "FAULT_INJECTION: storage_failure - Controlled exception from storage layer during startup. Starting with empty state. error_type=IOError error_message=%s",
                e
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
                        "SAFE-MODE activated after storage fault injection (startup): consecutive_errors=%s >= MAX_CONSECUTIVE_ERRORS=%s",
                        system_state.system_health.consecutive_errors, MAX_CONSECUTIVE_ERRORS
                    )
        else:
            # Другие IOError - логируем как обычную ошибку
            logger.warning("Error restoring snapshot: %s, starting with empty state", e)
    except Exception as e:
        logger.warning("Error restoring snapshot: %s, starting with empty state", e)
    
    # Одноразовая валидация символов против Bybit API
    global _active_symbols
    try:
        logger.info("🔍 Валидация символов против Bybit API...")
        _active_symbols = await asyncio.wait_for(
            asyncio.to_thread(validate_symbols, SYMBOLS),
            timeout=30.0,
        )
        if not _active_symbols:
            logger.critical("Ни один символ не вернул свечи. Проверьте соединение с Bybit.")
            _active_symbols = list(SYMBOLS)
    except asyncio.TimeoutError:
        logger.warning("Валидация символов превысила таймаут 30с — используем весь список")
        _active_symbols = list(SYMBOLS)
    except Exception as e:
        logger.warning("Валидация символов упала (%s) — используем весь список", e)
        _active_symbols = list(SYMBOLS)

    # Отправляем уведомление о запуске (не критично)
    try:
        await send_message_async("🚀 Торговый бот запущен")
    except Exception as e:
        logger.warning("Failed to send startup message (non-critical): %s: %s", type(e).__name__, e)
    
    # Сверка позиций с биржей при старте (3.9) — только когда ордера реальные.
    # Не удалась — торговля на паузе до /resume: без сверки неизвестно, что уже открыто.
    try:
        from trading_mode import sends_real_orders as _real_orders
        if _real_orders():
            from execution.exchange_ledger import reconcile_on_startup
            _report = await asyncio.wait_for(asyncio.to_thread(reconcile_on_startup), timeout=90.0)
            logger.info("Сверка позиций при старте: %s", _report.summary())
            if _report.messages:
                await send_message_async("🔄 Сверка позиций с биржей при старте:\n" + "\n".join(_report.messages))
    except Exception as e:
        logger.critical("Сверка позиций при старте не выполнена: %s: %s", type(e).__name__, e, exc_info=True)
        pause_trading_manually()
        try:
            await send_message_async(
                "🚨 Сверка позиций с биржей при старте не удалась — торговля на паузе. "
                "Проверьте позиции на бирже и снимите паузу /resume."
            )
        except Exception:
            logger.warning("Не удалось отправить тревогу о сверке", exc_info=True)

    # Создаём и отслеживаем все фоновые задачи
    # ВАЖНО: Порядок запуска критичен для предотвращения Conflict
    # 1. Control plane server УЖЕ запущен (выше)
    # 2. Затем запускаем остальные задачи
    # 3. Telegram supervisor запускается ПОСЛЕДНИМ с явной задержкой
    
    # Теперь запускаем остальные задачи
    tasks = [
        register_task(
            asyncio.create_task(market_analysis.market_analysis_loop(), name="MarketAnalysis"),
            "MarketAnalysis"
        ),
        register_task(
            asyncio.create_task(runtime_heartbeat.runtime_heartbeat_loop(_state, get_shutdown_event(), update_heartbeat_thread_safe, interval=RUNTIME_HEARTBEAT_INTERVAL, miss_threshold=HEARTBEAT_MISS_THRESHOLD, enforcement_threshold=HEARTBEAT_MISS_ENFORCEMENT_THRESHOLD, fault_inject_loop_stall=FAULT_INJECT_LOOP_STALL, max_consecutive_errors=MAX_CONSECUTIVE_ERRORS), name="RuntimeHeartbeat"),
            "RuntimeHeartbeat"
        ),
        register_task(
            asyncio.create_task(monitors.heartbeat_loop(_state, get_shutdown_event(), update_heartbeat_thread_safe), name="TelegramHeartbeat"),
            "TelegramHeartbeat"
        ),
        register_task(
            asyncio.create_task(periodic.daily_report_loop(_is_running, get_shutdown_event()), name="DailyReport"),
            "DailyReport"
        ),
        register_task(
            asyncio.create_task(periodic.weekly_report_loop(_is_running, get_shutdown_event()), name="WeeklyReport"),
            "WeeklyReport"
        ),
        register_task(
            asyncio.create_task(periodic.correlation_groups_loop(_is_running, get_shutdown_event()), name="CorrelationGroups"),
            "CorrelationGroups"
        ),
        # ========== PRODUCTION HARDENING MONITORS ==========
        register_task(
            asyncio.create_task(monitors.loop_guard_watchdog(_state, get_shutdown_event(), LOOP_GUARD_TIMEOUT), name="LoopGuardWatchdog"),
            "LoopGuardWatchdog"
        ),
        register_task(
            asyncio.create_task(monitors.safe_mode_ttl_monitor(_state, get_shutdown_event()), name="SafeModeTTLMonitor"),
            "SafeModeTTLMonitor"
        ),
        register_task(
            asyncio.create_task(paper_monitor.paper_trading_monitor_loop(_state, get_shutdown_event()), name="PaperTradingMonitor"),
            "PaperTradingMonitor"
        ),
        register_task(
            asyncio.create_task(periodic.outcome_tracker_loop(_is_running, get_shutdown_event()), name="OutcomeTracker"),
            "OutcomeTracker"
        ),
    ]
    
    # Теперь запускаем Telegram supervisor с явным отслеживанием
    logger.info("Starting Telegram supervisor (after system initialization)...")
    telegram_task = register_task(
        asyncio.create_task(telegram_supervisor.telegram_supervisor(_state, get_shutdown_event()), name="TelegramSupervisor"),
        "TelegramSupervisor"
    )
    
    # Добавляем synthetic decision tick loop если включен
    if ENABLE_SYNTHETIC_DECISION_TICK:
        tasks.append(
            register_task(
                asyncio.create_task(
                    fault_injection.synthetic_decision_tick_loop(_state, get_shutdown_event(), ENABLE_SYNTHETIC_DECISION_TICK, SYNTHETIC_DECISION_TICK_INTERVAL, MAX_CONSECUTIVE_ERRORS),
                    name="SyntheticDecisionTick"),
                "SyntheticDecisionTick"
            )
        )
        logger.info("Synthetic decision tick enabled (for fault injection testing)")
    
    # Добавляем loop stall injection task если включен
    if FAULT_INJECT_LOOP_STALL:
        tasks.append(
            register_task(
                asyncio.create_task(
                    fault_injection.loop_stall_injection_task(_state, get_shutdown_event(), FAULT_INJECT_LOOP_STALL, LOOP_STALL_DURATION),
                    name="LoopStallInjection"),
                "LoopStallInjection"
            )
        )
        logger.info("Loop stall injection enabled (for event loop stall detection testing)")
    
    logger.info("All components started (tasks: %s)", len(tasks) + 1)
    
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
                    logger.critical("FATAL_EXIT: Exiting with code %s (systemd will restart)", FATAL_EXIT_CODE)
                    os._exit(FATAL_EXIT_CODE)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("FATAL_STATE_MONITOR_ERROR: %s: %s", type(e).__name__, e)
    
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

        logger.critical("%s\n%s", error_msg, error_trace)
        
        # Пытаемся отправить уведомление (не блокируем shutdown)
        try:
            await asyncio.wait_for(
                asyncio.to_thread(error_alert, f"{error_msg}\n\nTrace:\n{error_trace[:500]}"),
                timeout=5.0
            )
        except Exception:
            logger.warning("Failed to send error alert for critical main loop error", exc_info=True)

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
                    logger.warning("Error stopping Telegram polling: %s: %s", type(e).__name__, e)
        except Exception as e:
            logger.warning("Error during Telegram shutdown: %s: %s", type(e).__name__, e)
        
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
        
        server_to_close = None
        
        # Get server instance (prefer local variable, fallback to global)
        if 'server' in locals() and server is not None:
            server_to_close = server
        elif cp_http.current_server() is not None:
            server_to_close = cp_http.current_server()
        
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
                    logger.critical("Cancelling %s remaining tasks (including server handlers)...", len(remaining_tasks))
                    for task in remaining_tasks:
                        if not task.done():
                            task.cancel()
                    # Await cancellation of all tasks
                    results = await asyncio.gather(*remaining_tasks, return_exceptions=True)
                    for r in results:
                        if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
                            logger.warning("Task shutdown exception: %s: %s", type(r).__name__, r)
                    logger.critical("All tasks cancelled and completed")
            except RuntimeError:
                logger.debug("RuntimeError during task cancellation (no event loop)", exc_info=True)
            except Exception as e:
                logger.debug("Error cancelling tasks: %s: %s", type(e).__name__, e)
            
            # Step 3: Wait for server to close
            # wait_closed() will complete immediately since all handler tasks are cancelled
            await server_to_close.wait_closed()
            
            # Clear global reference
            cp_http.forget_server()
        
        logger.critical("HTTP admin server stopped")
        
        # HARDENING: Проверяем таймаут shutdown
        shutdown_duration = time.time() - shutdown_start_time
        if shutdown_duration > GRACEFUL_SHUTDOWN_TIMEOUT:
            logger.critical(
                "SHUTDOWN_TIMEOUT: Graceful shutdown took %.1fs (threshold=%ss) - forcing exit",
                shutdown_duration, GRACEFUL_SHUTDOWN_TIMEOUT
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
            await send_message_async("⏹ Торговый бот остановлен")
        except Exception:
            # Уведомление не критично для остановки, но след в логе нужен.
            logger.debug("Уведомление об остановке бота не отправлено", exc_info=True)
        
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
            logger.debug("Error shutting down default executor: %s: %s", type(e).__name__, e)
        
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
                logger.critical("Found %s remaining tasks, cancelling all...", len(remaining_tasks))
                # Cancel all remaining tasks
                for task in remaining_tasks:
                    if not task.done():
                        task.cancel()
                # Await cancellation of all tasks
                # CRITICAL: This ensures event loop can drain and asyncio.run() can return
                results = await asyncio.gather(*remaining_tasks, return_exceptions=True)
                for r in results:
                    if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
                        logger.warning("Task shutdown exception: %s: %s", type(r).__name__, r)
                logger.critical("All remaining tasks cancelled and completed")
        except RuntimeError:
            # Event loop already closed - this is normal during shutdown
            pass
        except Exception as e:
            logger.debug("Error cleaning up remaining tasks: %s: %s", type(e).__name__, e)
        
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
                            logger.debug("Telegram Bot HTTP client close failed (timeout/runtime/attr)", exc_info=True)
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
                                logger.debug("Telegram Bot HTTPX connector close failed (timeout/runtime/attr)", exc_info=True)
        except (ImportError, AttributeError, RuntimeError):
            # Bot не импортирован или уже закрыт - это нормально
            pass
        except Exception as e:
            logger.debug("Error closing Telegram Bot: %s: %s", type(e).__name__, e)
        
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
                    logger.debug("Async generators shutdown failed (timeout/runtime)", exc_info=True)
        except RuntimeError:
            # Event loop уже закрыт - это нормально
            pass
        except Exception as e:
            logger.debug("Error shutting down async generators: %s: %s", type(e).__name__, e)
        
        # 5. Закрываем PostgreSQL pool / сбрасываем SQLite WAL
        try:
            from database import close_pg_pool, checkpoint_sqlite_wal
            close_pg_pool()
            checkpoint_sqlite_wal()
        except Exception as e:
            logger.debug("Error closing database: %s: %s", type(e).__name__, e)

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
                logger.critical("FINAL BARRIER: Found %s remaining tasks, forcing cancellation...", len(remaining_tasks))
                # Log task names for debugging
                task_names = [t.get_name() if hasattr(t, 'get_name') else str(t) for t in remaining_tasks]
                logger.critical("FINAL BARRIER: Tasks: %s", task_names)
                
                # Cancel all remaining tasks
                for task in remaining_tasks:
                    if not task.done():
                        task.cancel()
                
                # Await cancellation of all tasks
                # CRITICAL: return_exceptions=True ensures one failing task doesn't block others
                # This is the final guarantee that all tasks complete
                results = await asyncio.gather(*remaining_tasks, return_exceptions=True)
                for r in results:
                    if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
                        logger.warning("FINAL BARRIER task exception: %s: %s", type(r).__name__, r)
                logger.critical("FINAL BARRIER: All remaining tasks cancelled and completed")
            else:
                logger.critical("FINAL BARRIER: No remaining tasks - event loop is empty")
        except RuntimeError:
            # Event loop already closed - this is normal during shutdown
            pass
        except Exception as e:
            logger.error("FINAL BARRIER: Error during final task cancellation: %s: %s", type(e).__name__, e)
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
    logger.critical("PID: %s", os.getpid())
    logger.critical("Python: %s", sys.version)
    logger.critical("Control plane will listen on %s:%s", HEALTH_SERVER_HOST, HEALTH_SERVER_PORT)
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

        logger.critical("%s\n%s", error_msg, error_trace)
        
        # Flush logs перед exit
        for handler in root_logger.handlers:
            handler.flush()
        
        # systemd: non-zero exit code для критических ошибок
        exit_code = 1
    finally:
        # Очищаем PID file
        cleanup_pid_file()
    
    sys.exit(exit_code)
