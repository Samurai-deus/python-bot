import os
import warnings
import asyncio
import threading

# Подавляем предупреждение от apscheduler (используется python-telegram-bot)
warnings.filterwarnings("ignore", category=UserWarning, module="apscheduler")

from telegram import Bot
from telegram.ext import ApplicationBuilder  # Только для создания Application (polling)
# CommandHandler, MessageHandler, filters - используются в telegram_commands.py
from telegram.request import HTTPXRequest

TOKEN = "8358679673:AAFhBTR9gumcN98cfSLOwV9OvPolAiTOqw8"
CHAT_ID = "8163327295"

# Создаем Bot с увеличенным connection pool и таймаутом
# Это решает проблему "Pool timeout: All connections in the connection pool are occupied"
request = HTTPXRequest(
    connection_pool_size=20,  # Увеличиваем размер пула
    pool_timeout=30.0,  # Увеличиваем таймаут ожидания соединения
    read_timeout=30.0,
    write_timeout=30.0,
    connect_timeout=30.0
)
bot = Bot(token=TOKEN, request=request)

async def _send(text, parse_mode=None, retry_count=2):
    """
    Отправляет сообщение в Telegram с повторными попытками.
    
    Args:
        text: Текст сообщения
        parse_mode: Режим парсинга (Markdown, HTML или None)
        retry_count: Количество повторных попыток при ошибках
    """
    import asyncio
    from telegram.error import TimedOut, NetworkError
    
    for attempt in range(retry_count + 1):
        try:
            # Пытаемся отправить с parse_mode, если указан
            if parse_mode:
                try:
                    result = await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode=parse_mode)
                    print(f"✅ Сообщение успешно отправлено в Telegram с {parse_mode} (message_id: {result.message_id})")
                    return result
                except Exception as parse_error:
                    # Если ошибка парсинга, пробуем без parse_mode
                    if "parse" in str(parse_error).lower() or "markdown" in str(parse_error).lower():
                        print(f"⚠️ Ошибка парсинга {parse_mode}, пробуем без parse_mode: {parse_error}")
                        result = await bot.send_message(chat_id=CHAT_ID, text=text)
                        print(f"✅ Сообщение успешно отправлено в Telegram без parse_mode (message_id: {result.message_id})")
                        return result
                    else:
                        raise
            else:
                result = await bot.send_message(chat_id=CHAT_ID, text=text)
                print(f"✅ Сообщение успешно отправлено в Telegram (message_id: {result.message_id})")
                return result
        except (TimedOut, NetworkError) as e:
            if attempt < retry_count:
                wait_time = (attempt + 1) * 2  # Увеличиваем задержку с каждой попыткой
                print(f"⚠️ Ошибка соединения (попытка {attempt + 1}/{retry_count + 1}): {e}. Повтор через {wait_time} сек...")
                await asyncio.sleep(wait_time)
                continue
            else:
                print(f"❌ Ошибка при отправке сообщения в Telegram после {retry_count + 1} попыток: {type(e).__name__}: {e}")
                raise
        except Exception as e:
            # Для других ошибок не повторяем
            print(f"❌ Ошибка при отправке сообщения в Telegram: {type(e).__name__}: {e}")
            raise

async def _send_chart(symbol):
    img_url = (
        "https://www.tradingview.com/x/"
        f"?symbol=BYBIT:{symbol}"
    )
    try:
        result = await bot.send_message(
            chat_id=CHAT_ID,
            text=f"📈 {symbol} график\n{img_url}"
        )
        print(f"✅ График успешно отправлен в Telegram для {symbol}")
        return result
    except Exception as e:
        print(f"❌ Ошибка при отправке графика для {symbol}: {type(e).__name__}: {e}")
        raise

def send_message(text):
    """Отправляет сообщение синхронно, создавая новый event loop в отдельном потоке"""
    print(f"📤 Попытка отправить сообщение в Telegram (длина: {len(text)} символов)")
    
    _send_result = {"success": False, "error": None}
    
    def _run_in_thread():
        loop = None
        try:
            # Создаем новый event loop в этом потоке
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                # Пытаемся отправить с Markdown, если не получится - без него
                try:
                    result = loop.run_until_complete(_send(text, parse_mode="Markdown"))
                except Exception:
                    # Если не получилось с Markdown, пробуем без него
                    result = loop.run_until_complete(_send(text, parse_mode=None))
                _send_result["success"] = True
                _send_result["result"] = result
                print(f"✅ Сообщение отправлено успешно")
            except Exception as e:
                # Сохраняем ошибку для проверки
                _send_result["error"] = str(e)
                error_type = type(e).__name__
                # Игнорируем только ошибки закрытого loop
                if "Event loop is closed" not in str(e):
                    print(f"❌ Ошибка при отправке сообщения: {error_type}: {e}")
                    import traceback
                    print(f"Трассировка:\n{traceback.format_exc()}")
            finally:
                if loop and not loop.is_closed():
                    # Закрываем все pending tasks
                    try:
                        pending = asyncio.all_tasks(loop)
                        for task in pending:
                            task.cancel()
                        # Ждем завершения задач
                        if pending:
                            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    except Exception:
                        pass
                    try:
                        loop.close()
                    except Exception:
                        pass
        except Exception as e:
            # Сохраняем ошибку
            _send_result["error"] = str(e)
            error_type = type(e).__name__
            # Игнорируем только ошибки закрытого loop
            if "Event loop is closed" not in str(e):
                print(f"❌ Ошибка в потоке отправки сообщения: {error_type}: {e}")
                import traceback
                print(f"Трассировка:\n{traceback.format_exc()}")
    
    # Запускаем в отдельном потоке, чтобы избежать конфликтов с event loop
    try:
        thread = threading.Thread(target=_run_in_thread, daemon=True)
        thread.start()
        thread.join(timeout=10)  # Ждем максимум 10 секунд
        
        # Проверяем результат
        if not _send_result["success"]:
            if _send_result["error"]:
                print(f"⚠️ Сообщение не было отправлено: {_send_result['error']}")
            else:
                print(f"⚠️ Сообщение не было отправлено: таймаут или неизвестная ошибка")
        else:
            print(f"✅ Сообщение успешно отправлено")
            
    except Exception as e:
        print(f"❌ Критическая ошибка при запуске потока отправки: {type(e).__name__}: {e}")
        import traceback
        print(f"Трассировка:\n{traceback.format_exc()}")

def send_chart(symbol):
    """Отправляет график синхронно, создавая новый event loop в отдельном потоке"""
    print(f"📤 Попытка отправить график для {symbol}")
    
    _send_result = {"success": False, "error": None}
    
    def _run_in_thread():
        loop = None
        try:
            # Создаем новый event loop в этом потоке
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(_send_chart(symbol))
                _send_result["success"] = True
                _send_result["result"] = result
                print(f"✅ График отправлен успешно для {symbol}")
            except Exception as e:
                # Сохраняем ошибку для проверки
                _send_result["error"] = str(e)
                error_type = type(e).__name__
                # Игнорируем только ошибки закрытого loop
                if "Event loop is closed" not in str(e):
                    print(f"❌ Ошибка при отправке графика для {symbol}: {error_type}: {e}")
                    import traceback
                    print(f"Трассировка:\n{traceback.format_exc()}")
            finally:
                if loop and not loop.is_closed():
                    # Закрываем все pending tasks
                    try:
                        pending = asyncio.all_tasks(loop)
                        for task in pending:
                            task.cancel()
                        # Ждем завершения задач
                        if pending:
                            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    except Exception:
                        pass
                    try:
                        loop.close()
                    except Exception:
                        pass
        except Exception as e:
            # Сохраняем ошибку
            _send_result["error"] = str(e)
            error_type = type(e).__name__
            # Игнорируем только ошибки закрытого loop
            if "Event loop is closed" not in str(e):
                print(f"❌ Ошибка в потоке отправки графика для {symbol}: {error_type}: {e}")
                import traceback
                print(f"Трассировка:\n{traceback.format_exc()}")
    
    # Запускаем в отдельном потоке, чтобы избежать конфликтов с event loop
    try:
        thread = threading.Thread(target=_run_in_thread, daemon=True)
        thread.start()
        thread.join(timeout=10)  # Ждем максимум 10 секунд
        
        # Проверяем результат
        if not _send_result["success"]:
            if _send_result["error"]:
                print(f"⚠️ График не был отправлен для {symbol}: {_send_result['error']}")
            else:
                print(f"⚠️ График не был отправлен для {symbol}: таймаут или неизвестная ошибка")
        else:
            print(f"✅ График успешно отправлен для {symbol}")
            
    except Exception as e:
        print(f"❌ Критическая ошибка при запуске потока отправки графика для {symbol}: {type(e).__name__}: {e}")
        import traceback
        print(f"Трассировка:\n{traceback.format_exc()}")


# ===============================
# TELEGRAM COMMANDS HANDLER
# ===============================
#
# ВАЖНО: Эта секция НЕ выполняется при импорте модуля.
# Функция start_telegram_commands() должна вызываться ТОЛЬКО из runner.py
# через start_telegram_commands_sync().
#
# telegram_bot.py - это stateless helper для отправки сообщений.
# Polling запускается ТОЛЬКО в одном месте: runner.py:379
#

_app_instance = None
_polling_running = False
_polling_lock = threading.Lock()
_polling_lock_file = None  # Путь к lock-файлу

def start_telegram_commands():
    """
    Запускает обработчик Telegram команд в отдельном потоке.
    
    ⚠️ ВАЖНО: Эта функция должна вызываться ТОЛЬКО из runner.py!
    НЕ вызывайте её напрямую или из других мест.
    
    Защита от двойного запуска:
    1. Флаг _polling_running с thread lock
    2. Проверка в runner.py перед вызовом
    3. Логирование всех попыток
    
    Returns:
        None (функция запускает polling в отдельном потоке)
    """
    global _polling_running, _polling_lock_file
    import os
    from pathlib import Path
    
    # Дополнительная защита: lock-файл
    lock_file_path = Path(__file__).parent / ".telegram_polling.lock"
    _polling_lock_file = lock_file_path
    
    # Проверяем, не запущен ли уже polling (уровень 1: флаг)
    with _polling_lock:
        if _polling_running:
            print("⚠️ Telegram polling уже запущен (флаг), пропускаем повторный запуск")
            import logging
            logging.warning("Попытка запустить Telegram polling второй раз - игнорируется (флаг)")
            return
        
        # Проверяем lock-файл (уровень 2: файловая система)
        if lock_file_path.exists():
            # Проверяем, не устарел ли lock-файл (старше 5 минут = старый процесс)
            import time
            lock_age = time.time() - lock_file_path.stat().st_mtime
            if lock_age < 300:  # 5 минут
                print(f"⚠️ Telegram polling lock-файл существует (возраст: {lock_age:.0f} сек), пропускаем")
                import logging
                logging.warning(f"Lock-файл существует, пропускаем запуск (возраст: {lock_age:.0f} сек)")
                return
            else:
                # Старый lock-файл - удаляем
                print(f"⚠️ Удаляем устаревший lock-файл (возраст: {lock_age:.0f} сек)")
                try:
                    lock_file_path.unlink()
                except Exception:
                    pass
        
        # Создаём lock-файл
        try:
            lock_file_path.touch()
        except Exception as e:
            print(f"⚠️ Не удалось создать lock-файл: {e}")
            import logging
            logging.warning(f"Не удалось создать lock-файл: {e}")
        
        _polling_running = True
        print("🔒 Telegram polling блокирован для запуска")
    
    def run_in_thread():
        global _polling_running, _app_instance
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            _app_instance = ApplicationBuilder().token(TOKEN).build()
            
            # Импортируем и настраиваем команды
            from telegram_commands import setup_commands
            setup_commands(_app_instance)
            
            print("🤖 Telegram команды запущены")
            import logging
            logging.info("Telegram polling успешно запущен")
            
            loop.run_until_complete(_app_instance.run_polling(
                close_loop=False,
                stop_signals=None
            ))
        except (KeyboardInterrupt, SystemExit):
            print("🤖 Telegram команды остановлены")
            import logging
            logging.info("Telegram polling остановлен (KeyboardInterrupt/SystemExit)")
        except Exception as e:
            print(f"❌ Ошибка в Telegram командах: {e}")
            import logging
            logging.error(f"Ошибка в Telegram polling: {type(e).__name__}: {e}", exc_info=True)
            import traceback
            traceback.print_exc()
        finally:
            # Сбрасываем флаг при завершении
            with _polling_lock:
                _polling_running = False
                print("🔓 Telegram polling разблокирован")
                
                # Удаляем lock-файл
                if _polling_lock_file and _polling_lock_file.exists():
                    try:
                        _polling_lock_file.unlink()
                    except Exception:
                        pass
            
            try:
                if _app_instance:
                    loop.run_until_complete(_app_instance.shutdown())
            except Exception:
                pass
            
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            
            if not loop.is_closed():
                loop.close()
    
    thread = threading.Thread(target=run_in_thread, daemon=True, name="Telegram-Commands")
    thread.start()
    print("🤖 Telegram команды запущены в отдельном потоке")


def is_telegram_polling_running() -> bool:
    """
    Проверяет, запущен ли Telegram polling.
    
    Проверяет:
    1. Флаг _polling_running
    2. Существование lock-файла
    
    Returns:
        bool: True если polling запущен, False если нет
    """
    from pathlib import Path
    
    # Проверка 1: флаг
    with _polling_lock:
        if _polling_running:
            return True
    
    # Проверка 2: lock-файл
    lock_file_path = Path(__file__).parent / ".telegram_polling.lock"
    if lock_file_path.exists():
        import time
        lock_age = time.time() - lock_file_path.stat().st_mtime
        if lock_age < 300:  # Моложе 5 минут = активный
            return True
        else:
            # Устаревший lock-файл - удаляем
            try:
                lock_file_path.unlink()
            except Exception:
                pass
    
    return False

