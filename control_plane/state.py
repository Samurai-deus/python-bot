"""
Общее состояние процесса бота: метрики анализа и Prometheus, адаптивное
состояние, ручная пауза, флаг хаоса, блокировки. Его делят цикл анализа,
контролёры процесса, команды бота и HTTP-панель управления.

Вынесено из runner.py (пункт 5 плана отложенного, docs/DEFERRED_PLAN.md,
шаг 6б). Словари — те же объекты, runner держит на них ссылки под прежними
именами, поэтому изменения по ключам видны всем. Флаг хаоса раньше был bool,
который обработчики переприсваивали через global: после выноса такое
присваивание не дошло бы до читателя (runtime_heartbeat_loop), поэтому он стал
полем словаря chaos.
"""
import asyncio
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# Метрики анализа рынка для healthcheck; обновляются в market_analysis_loop
analysis_metrics = {
    "analysis_count": 0,
    "analysis_total_time": 0.0,
    "analysis_max_time": 0.0,
    "last_analysis_duration": 0.0,
    "start_time": None,  # Будет установлено при первом запуске
}

# Histogram buckets for analysis duration (seconds)
ANALYSIS_DURATION_BUCKETS = [0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 13.0]

# Prometheus metrics state
prometheus_metrics = {
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
adaptive_system_state = {
    "volatility_state": "MEDIUM",  # LOW, MEDIUM, HIGH (from market_regime.volatility_level)
    "adaptive_interval": None,  # Current adaptive interval (None = not initialized)
    "recovery_cycles": 0,  # Consecutive successful cycles while trading_paused
}

# Control plane state (manual pause tracking)
control_plane_state = {
    "manual_pause_active": False,  # True if trading was paused manually (via admin/telegram)
}

# Chaos invariant tracking (REQUIREMENT 2): был ли активен chaos. Пишут обработчики
# хаоса, читает runtime_heartbeat_loop.
chaos = {"was_active": False}


class TimeoutLock:
    """
    Thread-safe lock for metrics counters. These sync functions may be called from
    the event loop thread OR from threads spawned by run_in_executor, so
    threading.Lock is required (asyncio.Lock cannot be awaited from sync code).
    acquire() raises RuntimeError after `timeout` seconds to surface potential
    deadlocks instead of hanging indefinitely.
    """

    def __init__(self, timeout: float = 5.0):
        self._lock = threading.Lock()
        self._timeout = timeout

    def __enter__(self):
        if not self._lock.acquire(timeout=self._timeout):
            logger.error("_metrics_lock acquire timeout after %.1fs — possible deadlock", self._timeout)
            raise RuntimeError("_metrics_lock acquire timeout")
        return self

    def __exit__(self, *args):
        self._lock.release()

    # Compatibility shim used by code that calls acquire()/release() directly
    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        t = timeout if timeout >= 0 else self._timeout
        return self._lock.acquire(blocking=blocking, timeout=t)

    def release(self):
        self._lock.release()


metrics_lock = TimeoutLock(timeout=1.0)

# Lock to prevent race conditions in HTTP handlers (especially admin commands):
# concurrent HTTP requests cannot race-clear safe_mode or resume trading while
# safe_mode == true. Created lazily, when an event loop is running.
_admin_command_lock: Optional[asyncio.Lock] = None


def get_admin_lock() -> asyncio.Lock:
    """Returns the admin command lock, initializing it on first call.

    Safe: all callers are in the same asyncio event loop (cooperative).
    """
    global _admin_command_lock
    if _admin_command_lock is None:
        _admin_command_lock = asyncio.Lock()
    return _admin_command_lock


def reset_admin_lock() -> None:
    """Сбросить блокировку — для тестов: asyncio.Lock привязывается к циклу событий."""
    global _admin_command_lock
    _admin_command_lock = None


# ---------------------------------------------------------------------------
# Функции метрик (перенесены из runner.py на шаге 6в; runner держит прежние имена)
# ---------------------------------------------------------------------------

def get_analysis_metrics():
    """Возвращает текущие метрики анализа для health endpoint"""
    with metrics_lock:
        return analysis_metrics.copy()


def update_analysis_metrics(metrics_update: dict):
    """Обновляет глобальные метрики анализа"""
    with metrics_lock:
        analysis_metrics.update(metrics_update)


def get_prometheus_metrics():
    """Возвращает текущие Prometheus метрики"""
    with metrics_lock:
        return prometheus_metrics.copy()


def record_analysis_duration(duration: float):
    """
    Записывает длительность анализа в histogram buckets.

    NON-BLOCKING: Просто обновляет счетчики в памяти.

    Prometheus histogram buckets are cumulative:
    - Each bucket counts all observations <= bucket value
    - Values < smallest bucket are still counted in smallest bucket
    """
    with metrics_lock:
        prometheus_metrics["analysis_duration_sum"] += duration
        prometheus_metrics["analysis_duration_count"] += 1
        for bucket in ANALYSIS_DURATION_BUCKETS:
            if duration <= bucket:
                prometheus_metrics["analysis_duration_buckets"][bucket] += 1


def increment_scheduler_stalls():
    """Увеличивает счетчик scheduler stalls (NON-BLOCKING)"""
    with metrics_lock:
        prometheus_metrics["scheduler_stalls_total"] += 1


def increment_analysis_cycles():
    """Увеличивает счетчик завершенных циклов анализа (NON-BLOCKING)"""
    with metrics_lock:
        prometheus_metrics["analysis_cycles_total"] += 1


def get_adaptive_system_state():
    """Возвращает текущее состояние адаптивной системы"""
    return adaptive_system_state.copy()


def update_volatility_state(volatility_level: str):
    """Обновляет состояние волатильности (NON-BLOCKING)"""
    # Нормализуем уровень волатильности: LOW, MEDIUM, HIGH
    if volatility_level in ["LOW", "NORMAL", "MEDIUM", "HIGH", "EXTREME"]:
        # Маппинг: LOW -> LOW, NORMAL/MEDIUM -> MEDIUM, HIGH/EXTREME -> HIGH
        if volatility_level == "LOW":
            new_state = "LOW"
        elif volatility_level in ["NORMAL", "MEDIUM"]:
            new_state = "MEDIUM"
        else:  # HIGH, EXTREME
            new_state = "HIGH"
        with metrics_lock:
            adaptive_system_state["volatility_state"] = new_state
