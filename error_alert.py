"""
Модуль для отправки уведомлений об ошибках в Telegram
"""
import traceback
from datetime import datetime, UTC
from telegram_bot import send_message


def error_alert(error, include_traceback=False):
    """
    Отправляет уведомление об ошибке в Telegram.
    
    Args:
        error: Текст ошибки или объект исключения
        include_traceback: Включать ли трассировку стека
    """
    try:
        # Формируем сообщение
        if isinstance(error, Exception):
            error_text = str(error)
            if include_traceback:
                error_text += f"\n\nТрассировка:\n{traceback.format_exc()}"
        else:
            error_text = str(error)
        
        # Добавляем временную метку
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        message = f"❌ **Ошибка в боте**\n\n{error_text}\n\n⏰ {timestamp}"
        
        # Отправляем сообщение
        send_message(message)
        
    except Exception as e:
        # Если не удалось отправить уведомление, логируем в консоль
        print(f"⚠️ Не удалось отправить уведомление об ошибке: {e}")
        print(f"Оригинальная ошибка: {error}")


def critical_error(error):
    """
    Отправляет уведомление о критической ошибке.
    
    Args:
        error: Текст ошибки или объект исключения
    """
    try:
        if isinstance(error, Exception):
            error_text = f"{type(error).__name__}: {str(error)}"
            error_text += f"\n\nТрассировка:\n{traceback.format_exc()}"
        else:
            error_text = str(error)
        
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        message = f"🚨 **КРИТИЧЕСКАЯ ОШИБКА**\n\n{error_text}\n\n⏰ {timestamp}"
        
        send_message(message)
        
    except Exception as e:
        print(f"⚠️ Не удалось отправить уведомление о критической ошибке: {e}")
        print(f"Оригинальная ошибка: {error}")

