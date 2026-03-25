"""
Модуль для мониторинга работоспособности бота и отправки heartbeat сообщений.
"""
import logging
import time
import os
from datetime import datetime, UTC
from telegram_bot import send_message

logger = logging.getLogger(__name__)

# Пытаемся импортировать psutil, но не критично если его нет
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

HEARTBEAT_INTERVAL = 3600  # Отправка heartbeat каждые 60 минут (1 час)
LAST_HEARTBEAT_FILE = "last_heartbeat.txt"


def send_heartbeat():
    """
    Отправляет heartbeat сообщение о том, что бот работает.
    """
    try:
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        
        # Получаем информацию о системе (если доступно)
        system_info = ""
        if PSUTIL_AVAILABLE:
            try:
                cpu_percent = psutil.cpu_percent(interval=0)
                memory = psutil.virtual_memory()
                memory_percent = memory.percent
                disk = psutil.disk_usage('/')
                disk_percent = disk.percent
                system_info = (
                    f"\n📊 Система:\n"
                    f"CPU: {cpu_percent}%\n"
                    f"RAM: {memory_percent}%\n"
                    f"Disk: {disk_percent}%"
                )
            except Exception:
                system_info = ""
        
        message = (
            f"💓 **Heartbeat**\n\n"
            f"✅ Бот работает нормально"
            f"{system_info}\n\n"
            f"⏰ {timestamp}"
        )
        
        send_message(message)
        
        # Сохраняем время последнего heartbeat
        try:
            with open(LAST_HEARTBEAT_FILE, "w", encoding="utf-8") as f:
                f.write(str(time.time()))
        except Exception:
            pass
            
    except Exception as e:
        logger.error("Heartbeat send error: %s", e)


def check_last_heartbeat(max_interval_seconds=7200):
    """
    Проверяет, когда был последний heartbeat.
    Если прошло больше max_interval_seconds, отправляет предупреждение.
    
    Args:
        max_interval_seconds: Максимальный интервал без heartbeat (по умолчанию 2 часа)
    
    Returns:
        bool: True если все в порядке, False если нужно отправить предупреждение
    """
    try:
        if not os.path.exists(LAST_HEARTBEAT_FILE):
            return False  # Файл не существует - первый запуск
        
        with open(LAST_HEARTBEAT_FILE, "r", encoding="utf-8") as f:
            last_heartbeat_time = float(f.read().strip())
        
        time_since_heartbeat = time.time() - last_heartbeat_time
        
        if time_since_heartbeat > max_interval_seconds:
            # Отправляем предупреждение
            timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
            hours_since = time_since_heartbeat / 3600
            
            message = (
                f"⚠️ **Предупреждение**\n\n"
                f"Последний heartbeat был {hours_since:.1f} часов назад\n"
                f"Возможно, бот перестал работать\n\n"
                f"⏰ {timestamp}"
            )
            
            try:
                send_message(message)
            except Exception:
                pass
            
            return False
        
        return True
        
    except Exception as e:
        logger.error("Heartbeat check error: %s", e)
        return False


async def send_heartbeat_async():
    """
    Async version of send_heartbeat. Uses send_message_async directly.
    Fix: avoids creating new event loop in thread (pool exhaustion bug).
    """
    from telegram_bot import send_message_async
    import time
    try:
        timestamp = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')

        system_info = ''
        if PSUTIL_AVAILABLE:
            try:
                cpu_percent = psutil.cpu_percent()
                memory = psutil.virtual_memory()
                disk = psutil.disk_usage('/')
                system_info = (
                    '\n📊 Система:\n'
                    + f'CPU: {cpu_percent}%\n'
                    + f'RAM: {memory.percent}%\n'
                    + f'Disk: {disk.percent}%'
                )
            except Exception:
                system_info = ''

        message = (
            '💓 **Heartbeat**\n\n'
            + '✅ Бот работает нормально'
            + system_info
            + '\n\n⏰ ' + timestamp
        )

        await send_message_async(message)

        try:
            with open(LAST_HEARTBEAT_FILE, 'w', encoding='utf-8') as fh:
                fh.write(str(time.time()))
        except Exception:
            pass
    except Exception as e:
        logger.error("Heartbeat async error: %s", e)
