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
"""
import asyncio
import logging
import sys
import traceback
import signal
import os
import time
from datetime import datetime, UTC, timedelta
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
ANALYSIS_INTERVAL = int(os.environ.get("BOT_INTERVAL", "300"))  # 5 минут
MAX_CONSECUTIVE_ERRORS = int(os.environ.get("MAX_CONSECUTIVE_ERRORS", "5"))
ERROR_PAUSE = int(os.environ.get("ERROR_PAUSE", "600"))  # 10 минут

# Analysis timing limits
MAX_ANALYSIS_TIME = float(os.environ.get("MAX_ANALYSIS_TIME", "30"))  # секунд - мягкий лимит
ALERT_ANALYSIS_TIME = float(os.environ.get("ALERT_ANALYSIS_TIME", "60"))  # секунд - порог для алерта
ALERT_COOLDOWN = int(os.environ.get("ALERT_COOLDOWN", "300"))  # секунд - cooldown между алертами
METRICS_LOG_INTERVAL = int(os.environ.get("METRICS_LOG_INTERVAL", "600"))  # секунд - интервал логирования метрик
RUNTIME_HEARTBEAT_INTERVAL = 10.0  # 10 секунд для runtime heartbeat

# Health server configuration
HEALTH_SERVER_HOST = os.environ.get("HEALTH_SERVER_HOST", "127.0.0.1")
HEALTH_SERVER_PORT = int(os.environ.get("HEALTH_SERVER_PORT", "8080"))
SYNTHETIC_DECISION_TICK_INTERVAL = 10.0  # 10 секунд для synthetic decision tick
ENABLE_SYNTHETIC_DECISION_TICK = os.environ.get("ENABLE_SYNTHETIC_DECISION_TICK", "false").lower() == "true"
FAULT_INJECT_LOOP_STALL = os.environ.get("FAULT_INJECT_LOOP_STALL", "false").lower() == "true"
LOOP_STALL_DURATION = 120.0  # 120 секунд для loop stall
HEARTBEAT_MISS_THRESHOLD = 2.0  # Пропуск 2 heartbeats = stall detected

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

def get_analysis_metrics():
    """Возвращает текущие метрики анализа для health endpoint"""
    return _analysis_metrics.copy()

def update_analysis_metrics(metrics_update: dict):
    """Обновляет глобальные метрики анализа"""
    global _analysis_metrics
    _analysis_metrics.update(metrics_update)

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
    """
    signal_name = signal.Signals(signum).name
    logger.info(f"Received {signal_name} signal. Initiating graceful shutdown...")
    
    # Set flags for immediate effect
    system_state.system_health.is_running = False
    
    # Set shutdown event (if event loop is running)
    # This is safe - if loop doesn't exist, it will be created on first access
    try:
        shutdown_evt = get_shutdown_event()
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
    
    Args:
        task: The asyncio.Task to register
        name: Human-readable name for logging
        
    Returns:
        The same task (for chaining)
    """
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
    3. Timeout protection (so systemd doesn't hang)
    
    This is the ONLY place where task cancellation should happen during shutdown.
    """
    if not RUNNING_TASKS:
        logger.info("No tasks to cancel")
        return
    
    logger.info(f"Cancelling {len(RUNNING_TASKS)} registered tasks...")
    
    # Cancel all tasks
    tasks_to_cancel = list(RUNNING_TASKS)
    for task in tasks_to_cancel:
        if not task.done():
            task.cancel()
    
    # Wait for completion with timeout
    # CRITICAL: Use return_exceptions=True so one failing task doesn't block others
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks_to_cancel, return_exceptions=True),
            timeout=timeout
        )
        logger.info("All tasks cancelled and completed")
    except asyncio.TimeoutError:
        logger.warning(f"Some tasks did not complete within {timeout}s timeout")
        # Log which tasks are still running
        still_running = [t.get_name() for t in tasks_to_cancel if not t.done()]
        if still_running:
            logger.warning(f"Still running tasks: {still_running}")


async def run_market_analysis():
    """
    Выполняет один цикл анализа рынка.
    Это async версия того, что делал main.py
    """
    import time
    
    start_time = time.time()
    logger.info(f"🚀 Начало анализа {len(SYMBOLS)} символов")
    
    # Проверка торгового времени
    if not is_good_time():
        logger.info("⏸ Не торговое время - пропускаем цикл")
        return True
    
    try:
        # Инициализация экосистемы
        logger.info("🧠 Инициализация экосистемы...")
        decision_core = get_decision_core()
        market_regime_brain = get_market_regime_brain()
        risk_exposure_brain = get_risk_exposure_brain()
        cognitive_filter = get_cognitive_filter()
        opportunity_awareness = get_opportunity_awareness()
        gatekeeper = get_gatekeeper()
        
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
        
        # Анализ "мозгами" экосистемы (синхронные операции в потоках)
        # Brain'ы обновляют SystemState напрямую, не через DecisionCore
        logger.debug("🧠 Анализ Market Regime Brain...")
        try:
            market_regime = await asyncio.wait_for(
                asyncio.to_thread(market_regime_brain.analyze, SYMBOLS, all_candles, system_state),
                timeout=30.0
            )
            logger.info(f"   Режим: {market_regime.trend_type}, Волатильность: {market_regime.volatility_level}, Risk: {market_regime.risk_sentiment}")
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
                
                # Проверяем safe-mode активацию
                if system_state.system_health.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    system_state.system_health.safe_mode = True
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
        
        # Статистика Gatekeeper
        gatekeeper_stats = gatekeeper.get_stats()
        if gatekeeper_stats["total"] > 0:
            logger.info(f"🚪 Gatekeeper: одобрено {gatekeeper_stats['approved']}, заблокировано {gatekeeper_stats['blocked']}")
        
        total_time = time.time() - start_time
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
                    
                    # Проверяем safe-mode активацию
                    if system_state.system_health.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        system_state.system_health.safe_mode = True
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
        
        # Включаем safe-mode при множественных ошибках (включая fault injection)
        if system_state.system_health.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            system_state.system_health.safe_mode = True
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
    logger.info("Market analysis loop started")
    
    # Use shutdown_event for proper cancellation semantics
    shutdown_evt = get_shutdown_event()
    
    # ========== АБСОЛЮТНОЕ ПЛАНИРОВАНИЕ ==========
    # Используем monotonic clock для предотвращения дрейфа
    interval = float(ANALYSIS_INTERVAL)
    next_run = time.monotonic()
    
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
    
    # ========== АЛЕРТЫ ==========
    last_alert_ts = 0.0
    
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
            
            # ========== МЯГКИЙ КОНТРОЛЬ ВРЕМЕНИ ==========
            # Заменяем аварийный watchdog на мягкое предупреждение
            if duration > MAX_ANALYSIS_TIME:
                logger.warning(
                    "⏱ Analysis slow: %.2fs (limit %.2fs)",
                    duration,
                    MAX_ANALYSIS_TIME
                )
            
            # ========== АЛЕРТЫ ПРИ МЕДЛЕННОМ АНАЛИЗЕ ==========
            now = time.monotonic()
            if duration > ALERT_ANALYSIS_TIME and (now - last_alert_ts) > ALERT_COOLDOWN:
                try:
                    alert_msg = f"⚠️ MarketAnalysis slow: {duration:.2f}s (limit {ALERT_ANALYSIS_TIME:.2f}s)"
                    await asyncio.wait_for(
                        asyncio.to_thread(error_alert, alert_msg),
                        timeout=10.0
                    )
                    last_alert_ts = now
                except Exception:
                    # Игнорируем ошибки отправки алерта
                    pass
            
            # ========== ПЕРИОДИЧЕСКОЕ ЛОГИРОВАНИЕ МЕТРИК ==========
            if (now - metrics["last_metrics_log"]) >= METRICS_LOG_INTERVAL:
                if metrics["analysis_count"] > 0:
                    avg = metrics["analysis_total_time"] / metrics["analysis_count"]
                    uptime = now - metrics["start_time"]
                    logger.info(
                        "📈 Metrics | runs=%d avg=%.2fs max=%.2fs uptime=%.0fs",
                        metrics["analysis_count"],
                        avg,
                        metrics["analysis_max_time"],
                        uptime
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
                next_run += interval
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
            
            # Обновляем SystemState
            system_state.update_heartbeat()
            
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
                    
                    # Проверяем safe-mode активацию
                    if system_state.system_health.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        system_state.system_health.safe_mode = True
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
                await asyncio.sleep(min(1.0, remaining))
                remaining -= 1.0
            
            # Проверяем shutdown после sleep
            if shutdown_evt.is_set() or not system_state.system_health.is_running:
                break
            
            try:
                await asyncio.to_thread(send_heartbeat)
                system_state.update_heartbeat()
                logger.debug("Telegram heartbeat sent")
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
                await asyncio.sleep(min(1.0, remaining))
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
                        
                        # Проверяем safe-mode активацию
                        if system_state.system_health.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                            system_state.system_health.safe_mode = True
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
        await app.initialize()
        
        # Запуск polling через updater
        if not app.updater:
            raise RuntimeError("Application does not have an Updater")
        
        # Запускаем polling
        # CRITICAL: Defensive Conflict handling - exit cleanly if another instance is running
        try:
            logger.info("Starting Telegram polling...")
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
                await app.shutdown()
            except Exception:
                pass
            # Wait 10 seconds to allow previous instance to fully stop
            await asyncio.sleep(10.0)
            # Exit process cleanly - systemd will restart
            import sys
            sys.exit(1)
        
        # Запускаем приложение
        await app.start()
        
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
        try:
            await asyncio.wait_for(app.updater.stop(), timeout=5.0)
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"Error stopping updater (non-critical): {type(e).__name__}: {e}")
        
        # Останавливаем приложение (с таймаутом для быстрого shutdown)
        try:
            await asyncio.wait_for(app.stop(), timeout=5.0)
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"Error stopping app (non-critical): {type(e).__name__}: {e}")
        
        # Shutdown приложения (с таймаутом для быстрого shutdown)
        try:
            await asyncio.wait_for(app.shutdown(), timeout=5.0)
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


async def health_server():
    """
    HTTP healthcheck server для мониторинга состояния сервиса.
    
    Endpoint: GET /health
    Response: JSON с status, uptime, last_analysis_duration, safe_mode
    
    Features:
    - Не блокирует event loop (aiohttp работает асинхронно)
    - Graceful shutdown support
    - Bind к 127.0.0.1 (безопасно для production)
    """
    try:
        import aiohttp
        from aiohttp import web
    except ImportError:
        logger.warning("aiohttp not available, health server disabled")
        return
    
    logger.info(f"Starting health server on {HEALTH_SERVER_HOST}:{HEALTH_SERVER_PORT}")
    
    app = web.Application()
    
    async def health_handler(request):
        """Обработчик GET /health"""
        try:
            # Получаем метрики анализа
            metrics = get_analysis_metrics()
            
            # Вычисляем uptime
            uptime = 0.0
            if metrics["start_time"] is not None:
                uptime = time.monotonic() - metrics["start_time"]
            
            # Определяем status
            # "degraded" если safe_mode активен или есть ошибки
            status = "ok"
            if system_state.system_health.safe_mode:
                status = "degraded"
            elif system_state.system_health.consecutive_errors > 0:
                status = "degraded"
            
            # Формируем ответ
            response_data = {
                "status": status,
                "uptime": round(uptime, 2),
                "last_analysis_duration": round(metrics.get("last_analysis_duration", 0.0), 2),
                "safe_mode": system_state.system_health.safe_mode,
                "analysis_count": metrics.get("analysis_count", 0),
                "consecutive_errors": system_state.system_health.consecutive_errors,
            }
            
            return web.json_response(response_data)
        except Exception as e:
            logger.error(f"Error in health handler: {type(e).__name__}: {e}")
            return web.json_response(
                {"status": "error", "error": str(e)},
                status=500
            )
    
    app.router.add_get("/health", health_handler)
    
    # Создаём runner для сервера
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Создаём site
    site = web.TCPSite(runner, HEALTH_SERVER_HOST, HEALTH_SERVER_PORT)
    
    try:
        # Запускаем сервер
        await site.start()
        logger.info(f"Health server started on http://{HEALTH_SERVER_HOST}:{HEALTH_SERVER_PORT}/health")
        
        # Ждём shutdown signal
        shutdown_evt = get_shutdown_event()
        while system_state.system_health.is_running and not shutdown_evt.is_set():
            await asyncio.sleep(1.0)
        
    except asyncio.CancelledError:
        logger.info("Health server cancelled")
    except Exception as e:
        logger.error(f"Error in health server: {type(e).__name__}: {e}")
    finally:
        # Graceful shutdown
        try:
            await site.stop()
            await runner.cleanup()
            logger.info("Health server stopped")
        except Exception as e:
            logger.warning(f"Error stopping health server: {type(e).__name__}: {e}")


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
            # Создаём Application и настраиваем команды
            if app is None:
                logger.info("📱 Initializing Telegram Application...")
                app = ApplicationBuilder().token(TOKEN).build()
                setup_commands(app)
                logger.info("📱 Telegram Application initialized")
            
            # Создаём shutdown event для отслеживания остановки
            if shutdown_event is None:
                shutdown_event = asyncio.Event()
            
            # Мониторим shutdown request
            async def monitor_shutdown():
                """
                Monitors global shutdown event and sets local shutdown_event when shutdown is requested.
                
                WHY: Telegram polling runs in a separate task and needs to be notified
                when shutdown is requested. This allows graceful shutdown of polling.
                """
                try:
                    # Monitor global shutdown event
                    while system_state.system_health.is_running and not shutdown_evt.is_set():
                        await asyncio.sleep(1.0)
                except asyncio.CancelledError:
                    # Task отменена - устанавливаем shutdown event для остановки polling
                    pass
                finally:
                    # Set local shutdown event to stop polling
                    shutdown_event.set()
            
            monitor_task = asyncio.create_task(monitor_shutdown(), name="TelegramShutdownMonitor")
            
            # Запускаем polling в отдельной task для изоляции
            logger.info("📱 Starting Telegram polling...")
            
            # Сбрасываем backoff при успешном запуске
            backoff_seconds = 10.0
            
            # Создаём task для polling - это позволяет перехватывать исключения
            # из внутренних tasks python-telegram-bot
            polling_task = asyncio.create_task(
                _telegram_polling_task(app, shutdown_event),
                name="TelegramPollingTask"
            )
            
            # Ждём завершения task (или исключения)
            # CRITICAL: Используем wait_for с таймаутом для предотвращения бесконечного ожидания
            # Таймаут 5 секунд - достаточно для проверки статуса, но не блокирует shutdown
            try:
                await asyncio.wait_for(polling_task, timeout=5.0)
            except asyncio.TimeoutError:
                # Таймаут - проверяем статус task и shutdown
                if shutdown_evt.is_set() or not system_state.system_health.is_running:
                    # Shutdown запрошен - отменяем task и выходим
                    if polling_task and not polling_task.done():
                        polling_task.cancel()
                    break
                # Продолжаем ожидание (task еще работает)
                try:
                    await polling_task
                except asyncio.CancelledError:
                    logger.info("📱 Telegram polling task cancelled")
                    break
            except asyncio.CancelledError:
                # Task отменена - выходим
                logger.info("📱 Telegram polling task cancelled")
                break
            except Exception as e:
                # Исключение из task - пробрасываем для обработки ниже
                raise
            
            # Если polling завершился нормально (shutdown), выходим
            logger.info("📱 Telegram polling stopped normally")
            break
            
        except Conflict as e:
            # КРИТИЧНО: Conflict означает, что другой экземпляр уже запущен
            # Это НЕ retryable - нужно выйти и дать systemd перезапустить позже
            logger.error(
                f"TELEGRAM_CONFLICT: Another instance is already polling. "
                f"This usually happens during systemd restart when previous instance hasn't fully stopped. "
                f"Exiting cleanly to allow systemd restart. error={e}"
            )
            
            # Останавливаем все Telegram ресурсы
            if polling_task and not polling_task.done():
                try:
                    polling_task.cancel()
                    await asyncio.wait_for(polling_task, timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                except Exception:
                    pass
            
            if monitor_task and not monitor_task.done():
                try:
                    monitor_task.cancel()
                    await asyncio.wait_for(monitor_task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                except Exception:
                    pass
            
            if app:
                try:
                    if app.running:
                        await app.stop()
                    await app.shutdown()
                except Exception:
                    pass
            
            # Ждём 10 секунд перед выходом (даём время предыдущему экземпляру завершиться)
            logger.info("Waiting 10 seconds before exit to allow previous instance to stop...")
            await asyncio.sleep(10.0)
            
            # Выходим - systemd перезапустит позже
            logger.info("Exiting due to Telegram Conflict. systemd will restart the service.")
            system_state.system_health.is_running = False
            return
            
        except NetworkError as e:
            # NetworkError - retryable, продолжаем работу
            logger.warning(
                f"TELEGRAM_NETWORK_FAILURE: {type(e).__name__}: {e}. "
                f"Retrying in {backoff_seconds:.1f}s. "
                f"Runtime continues normally."
            )
            
            # Обновляем system health (только для NetworkError, не для Conflict)
            system_state.record_error(f"TELEGRAM_NETWORK_FAILURE: {type(e).__name__}")
            if system_state.system_health.consecutive_errors >= 5:
                system_state.system_health.safe_mode = True
                logger.warning(
                    f"SAFE-MODE activated due to Telegram failures: "
                    f"consecutive_errors={system_state.system_health.consecutive_errors}"
                )
            
            # Увеличиваем backoff (exponential)
            backoff_seconds = min(backoff_seconds * BACKOFF_MULTIPLIER, MAX_BACKOFF)
            
            # Отменяем мониторинг shutdown
            if monitor_task and not monitor_task.done():
                try:
                    monitor_task.cancel()
                    await asyncio.wait_for(monitor_task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                except Exception:
                    pass
            monitor_task = None
            
            # Устанавливаем shutdown event для остановки polling
            if shutdown_event:
                shutdown_event.set()
            
            # Отменяем task если она еще работает
            if polling_task and not polling_task.done():
                try:
                    polling_task.cancel()
                    await asyncio.wait_for(polling_task, timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                except Exception:
                    pass
            polling_task = None
            shutdown_event = None
            
            # Ждём перед перезапуском (с проверкой shutdown)
            try:
                sleep_seconds = int(backoff_seconds)
                shutdown_evt = get_shutdown_event()
                # Используем await asyncio.sleep() с проверкой shutdown каждую секунду
                shutdown_evt = get_shutdown_event()
                remaining = sleep_seconds
                while remaining > 0:
                    if shutdown_evt.is_set() or not system_state.system_health.is_running:
                        break
                    # Спим по 1 секунде, чтобы можно было прервать при shutdown
                    await asyncio.sleep(min(1.0, remaining))
                    remaining -= 1.0
            except asyncio.CancelledError:
                break
            
            # Сбрасываем app для пересоздания при следующей попытке
            if app:
                try:
                    await app.shutdown()
                except Exception:
                    pass
                app = None
            
            # Продолжаем цикл - перезапускаем polling
            
        except asyncio.CancelledError:
            logger.info("📱 Telegram supervisor cancelled")
            break
            
        except Exception as e:
            # Другие исключения - логируем, но НЕ пробрасываем
            # Это fault isolation - Telegram не должен крашить процесс
            logger.error(
                f"TELEGRAM_UNEXPECTED_ERROR: {type(e).__name__}: {e}. "
                f"Retrying in {backoff_seconds:.1f}s. "
                f"Runtime continues normally.",
                exc_info=True
            )
            
            # Обновляем system health
            system_state.record_error(f"TELEGRAM_UNEXPECTED_ERROR: {type(e).__name__}")
            if system_state.system_health.consecutive_errors >= 5:
                system_state.system_health.safe_mode = True
                logger.warning(
                    f"SAFE-MODE activated due to Telegram errors: "
                    f"consecutive_errors={system_state.system_health.consecutive_errors}"
                )
            
            # Увеличиваем backoff
            backoff_seconds = min(backoff_seconds * BACKOFF_MULTIPLIER, MAX_BACKOFF)
            
            # Отменяем мониторинг shutdown
            if monitor_task and not monitor_task.done():
                try:
                    monitor_task.cancel()
                    await asyncio.wait_for(monitor_task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                except Exception:
                    pass
            monitor_task = None
            
            # Устанавливаем shutdown event для остановки polling
            if shutdown_event:
                shutdown_event.set()
            
            # Отменяем task если она еще работает
            if polling_task and not polling_task.done():
                try:
                    polling_task.cancel()
                    await asyncio.wait_for(polling_task, timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                except Exception:
                    pass
            polling_task = None
            shutdown_event = None
            
            # Ждём перед перезапуском
            try:
                sleep_seconds = int(backoff_seconds)
                shutdown_evt = get_shutdown_event()
                # Используем await asyncio.sleep() с проверкой shutdown каждую секунду
                shutdown_evt = get_shutdown_event()
                remaining = sleep_seconds
                while remaining > 0:
                    if shutdown_evt.is_set() or not system_state.system_health.is_running:
                        break
                    # Спим по 1 секунде, чтобы можно было прервать при shutdown
                    await asyncio.sleep(min(1.0, remaining))
                    remaining -= 1.0
            except asyncio.CancelledError:
                break
            
            # Сбрасываем app для пересоздания
            if app:
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
                app = None
            
            # Продолжаем цикл
    
    # Cleanup при выходе
    if shutdown_event:
        shutdown_event.set()
    
    if polling_task and not polling_task.done():
        try:
            polling_task.cancel()
            await asyncio.wait_for(polling_task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception:
            pass
    
    if app:
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
    # Проверка single-instance
    if not check_single_instance():
        logger.critical("Another instance is running. Exiting.")
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
            
            # Проверяем safe-mode активацию
            if system_state.system_health.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                system_state.system_health.safe_mode = True
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
    # 1. Сначала запускаем health server (неблокирующий)
    # 2. Затем запускаем остальные задачи
    # 3. Telegram supervisor запускается ПОСЛЕДНИМ с явной задержкой
    
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
        register_task(
            asyncio.create_task(health_server(), name="HealthServer"),
            "HealthServer"
        ),
    ]
    
    # Ждём инициализации event loop и старта health server
    # Это гарантирует, что система готова перед запуском Telegram
    logger.info("Waiting for event loop initialization and health server startup...")
    await asyncio.sleep(2.0)  # Даём время для инициализации
    
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
    
    try:
        # Ждем завершения всех задач или shutdown signal
        # Используем return_exceptions=True чтобы одна ошибка не крашила все задачи
        # Включаем telegram_task в gather
        all_tasks = tasks + [telegram_task] if 'telegram_task' in locals() else tasks
        results = await asyncio.gather(*all_tasks, return_exceptions=True)
        
        # Проверяем результаты на ошибки
        for task, result in zip(all_tasks, results):
            if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                logger.error(f"Task {task.get_name()} failed: {type(result).__name__}: {result}")
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
        
        # Для systemd: exit с non-zero кодом при критической ошибке
        raise
    finally:
        # ========== GRACEFUL SHUTDOWN SEQUENCE ==========
        # 
        # WHY THIS ORDER:
        # 1. Set is_running=False - stops all loops from starting new work
        # 2. Set shutdown_event - allows loops to exit naturally
        # 3. Cancel all tasks - ensures no task blocks shutdown
        # 4. Wait for completion - cleanup resources
        # 5. Send notification (non-blocking) - user feedback
        # 6. Cleanup - PID file, logs
        #
        # CRITICAL: Must complete within systemd TimeoutStopSec (default 90s)
        # No blocking operations after this point.
        
        logger.info("Initiating graceful shutdown...")
        system_state.system_health.is_running = False
        
        # Set shutdown event to allow loops to exit naturally
        shutdown_evt = get_shutdown_event()
        shutdown_evt.set()
        
        # КРИТИЧНО: Явно останавливаем Telegram polling ПЕРЕД общей отменой задач
        # Это гарантирует, что polling полностью остановлен до выхода процесса
        try:
            # Находим telegram_task в зарегистрированных задачах
            telegram_task_to_stop = None
            for task in RUNNING_TASKS:
                if task.get_name() == "TelegramSupervisor":
                    telegram_task_to_stop = task
                    break
            
            if telegram_task_to_stop and not telegram_task_to_stop.done():
                logger.info("Stopping Telegram polling task...")
                telegram_task_to_stop.cancel()
                try:
                    await asyncio.wait_for(telegram_task_to_stop, timeout=10.0)
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
        await shutdown_all_tasks(timeout=10.0)
        
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
        
        logger.info("Graceful shutdown completed")


if __name__ == "__main__":
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
