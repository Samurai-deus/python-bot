"""
Systemd Integration - Watchdog и exit codes

ЦЕЛЬ: Правильная интеграция с systemd для контролируемых restart.

EXIT CODES:
- 0: graceful shutdown
- 2: recoverable failure (systemd может рестартовать)
- 10: CRITICAL / deadlock detected (требует restart)
- 77: config error (не рестартовать)

WATCHDOG:
- sd_notify WATCHDOG=1 каждые N секунд
- WatchdogSec в unit file
- Если heartbeat пропущен -> systemd убивает процесс
"""
import os
import sys
import logging
from typing import Optional
from enum import IntEnum

logger = logging.getLogger(__name__)


class ExitCode(IntEnum):
    """Exit codes для systemd"""
    SUCCESS = 0  # Graceful shutdown
    RECOVERABLE = 2  # Recoverable failure (systemd может рестартовать)
    CRITICAL = 10  # CRITICAL / deadlock (требует restart)
    CONFIG_ERROR = 77  # Config error (не рестартовать)


# Try to import systemd (может быть не установлен)
try:
    import systemd.daemon
    HAS_SYSTEMD = True
except ImportError:
    HAS_SYSTEMD = False
    logger.warning("systemd.daemon not available - watchdog disabled")


class SystemdIntegration:
    """
    Интеграция с systemd для watchdog и exit codes
    """
    
    def __init__(self, watchdog_interval: float = 30.0):
        """
        Args:
            watchdog_interval: Интервал heartbeat в секундах (должен быть < WatchdogSec в unit file)
        """
        self.watchdog_interval = watchdog_interval
        self._watchdog_enabled = False
        self._last_heartbeat = 0.0
        
        if HAS_SYSTEMD:
            # Проверяем, запущены ли мы под systemd
            if os.getenv("NOTIFY_SOCKET"):
                self._watchdog_enabled = True
                logger.info(f"Systemd watchdog enabled (interval: {watchdog_interval}s)")
            else:
                logger.info("Not running under systemd - watchdog disabled")
        else:
            logger.warning("systemd.daemon not available - watchdog disabled")
    
    def notify_ready(self) -> None:
        """Уведомить systemd, что сервис готов"""
        if not HAS_SYSTEMD or not self._watchdog_enabled:
            return
        
        try:
            systemd.daemon.notify("READY=1")
            logger.info("Systemd notified: READY=1")
        except Exception as e:
            logger.error(f"Failed to notify systemd READY: {type(e).__name__}: {e}")
    
    def notify_watchdog(self) -> bool:
        """
        Отправить watchdog heartbeat
        
        Returns:
            True если heartbeat отправлен
        """
        if not HAS_SYSTEMD or not self._watchdog_enabled:
            return False
        
        try:
            systemd.daemon.notify("WATCHDOG=1")
            import time
            self._last_heartbeat = time.monotonic()
            return True
        except Exception as e:
            logger.error(f"Failed to send watchdog heartbeat: {type(e).__name__}: {e}")
            return False
    
    def notify_status(self, status: str) -> None:
        """
        Отправить статус в systemd
        
        Args:
            status: Статусное сообщение
        """
        if not HAS_SYSTEMD or not self._watchdog_enabled:
            return
        
        try:
            systemd.daemon.notify(f"STATUS={status}")
        except Exception as e:
            logger.error(f"Failed to notify systemd STATUS: {type(e).__name__}: {e}")
    
    def notify_stopping(self) -> None:
        """Уведомить systemd, что сервис останавливается"""
        if not HAS_SYSTEMD or not self._watchdog_enabled:
            return
        
        try:
            systemd.daemon.notify("STOPPING=1")
            logger.info("Systemd notified: STOPPING=1")
        except Exception as e:
            logger.error(f"Failed to notify systemd STOPPING: {type(e).__name__}: {e}")
    
    def exit_with_code(self, exit_code: ExitCode, reason: str) -> None:
        """
        Выход с правильным exit code
        
        Args:
            exit_code: Exit code
            reason: Причина выхода
        """
        logger.critical(
            f"SYSTEM_EXIT exit_code={exit_code.value} "
            f"exit_code_name={exit_code.name} reason={reason}"
        )
        
        self.notify_stopping()
        sys.exit(exit_code.value)
    
    def is_watchdog_enabled(self) -> bool:
        """Проверка, включён ли watchdog"""
        return self._watchdog_enabled


# Глобальный экземпляр
_systemd_integration: Optional[SystemdIntegration] = None


def get_systemd_integration() -> SystemdIntegration:
    """Получить глобальный экземпляр"""
    global _systemd_integration
    if _systemd_integration is None:
        _systemd_integration = SystemdIntegration()
    return _systemd_integration


def set_systemd_integration(si: SystemdIntegration) -> None:
    """Установить экземпляр (для тестов)"""
    global _systemd_integration
    _systemd_integration = si

