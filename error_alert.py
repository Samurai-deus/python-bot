# Файл: error_alert.py

from telegram_bot import send_message  # Импортируем функцию отправки сообщений

def error_alert(error):
    send_message(f"❌ Внимание! Ошибка в боте:\n{error}")

