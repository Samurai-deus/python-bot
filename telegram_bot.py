import os
import warnings
import asyncio

# Подавляем предупреждение от apscheduler (используется python-telegram-bot)
warnings.filterwarnings("ignore", category=UserWarning, module="apscheduler")

from telegram import Bot
from telegram.ext import ApplicationBuilder  # Только для создания Application (polling)
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
    from telegram.error import TimedOut, NetworkError
    
    for attempt in range(retry_count + 1):
        try:
            # КРИТИЧНО: Обёртываем сетевые вызовы в wait_for для предотвращения блокировки при network blackhole
            # Таймаут 30s достаточен для нормальной работы, но ограничивает время при недоступности сети
            try:
                # Пытаемся отправить с parse_mode, если указан
                if parse_mode:
                    try:
                        result = await asyncio.wait_for(
                            bot.send_message(chat_id=CHAT_ID, text=text, parse_mode=parse_mode),
                            timeout=30.0
                        )
                        print(f"✅ Сообщение успешно отправлено в Telegram с {parse_mode} (message_id: {result.message_id})")
                        return result
                    except asyncio.TimeoutError:
                        # Timeout при network blackhole - пробрасываем как NetworkError для retry логики
                        raise NetworkError("Request timeout - network may be unreachable")
                    except Exception as parse_error:
                        # Если ошибка парсинга, пробуем без parse_mode
                        if "parse" in str(parse_error).lower() or "markdown" in str(parse_error).lower():
                            print(f"⚠️ Ошибка парсинга {parse_mode}, пробуем без parse_mode: {parse_error}")
                            result = await asyncio.wait_for(
                                bot.send_message(chat_id=CHAT_ID, text=text),
                                timeout=30.0
                            )
                            print(f"✅ Сообщение успешно отправлено в Telegram без parse_mode (message_id: {result.message_id})")
                            return result
                        else:
                            raise
                else:
                    result = await asyncio.wait_for(
                        bot.send_message(chat_id=CHAT_ID, text=text),
                        timeout=30.0
                    )
                    print(f"✅ Сообщение успешно отправлено в Telegram (message_id: {result.message_id})")
                    return result
            except asyncio.TimeoutError:
                # Timeout при network blackhole - пробрасываем как NetworkError для retry логики
                raise NetworkError("Request timeout - network may be unreachable")
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
        # КРИТИЧНО: Обёртываем сетевой вызов в wait_for для предотвращения блокировки при network blackhole
        result = await asyncio.wait_for(
            bot.send_message(
                chat_id=CHAT_ID,
                text=f"📈 {symbol} график\n{img_url}"
            ),
            timeout=30.0
        )
        print(f"✅ График успешно отправлен в Telegram для {symbol}")
        return result
    except asyncio.TimeoutError:
        print(f"❌ Timeout при отправке графика для {symbol} - network may be unreachable")
        raise
    except Exception as e:
        print(f"❌ Ошибка при отправке графика для {symbol}: {type(e).__name__}: {e}")
        raise


# ===============================
# ASYNC API (используется из async контекста)
# ===============================

async def send_message_async(text, parse_mode="Markdown"):
    """
    Async версия отправки сообщения.
    Используется из async контекста (runner.py, supervisors).
    
    Args:
        text: Текст сообщения
        parse_mode: Режим парсинга (Markdown, HTML или None)
    """
    try:
        return await _send(text, parse_mode=parse_mode)
    except Exception:
        # Если не получилось с Markdown, пробуем без него
        try:
            return await _send(text, parse_mode=None)
        except Exception:
            # Игнорируем ошибки - не блокируем основной процесс
            pass

async def send_chart_async(symbol):
    """
    Async версия отправки графика.
    Используется из async контекста (runner.py, supervisors).
    
    Args:
        symbol: Символ для графика
    """
    try:
        return await _send_chart(symbol)
    except Exception:
        # Игнорируем ошибки - не блокируем основной процесс
        pass


# ===============================
# SYNC API (для обратной совместимости)
# ===============================
# Эти функции используются из синхронного кода через asyncio.to_thread()

def _send_message_sync(text):
    """
    Синхронная обертка для send_message_async.
    Используется через asyncio.to_thread() из async контекста.
    """
    print(f"📤 Попытка отправить сообщение в Telegram (длина: {len(text)} символов)")
    
    # Создаем новый event loop в текущем потоке (вызывается из asyncio.to_thread)
    # Это безопасно, так как вызывается из отдельного потока
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Пытаемся отправить с Markdown, если не получится - без него
            try:
                result = loop.run_until_complete(send_message_async(text, parse_mode="Markdown"))
                print(f"✅ Сообщение отправлено успешно")
                return result
            except Exception:
                # Если не получилось с Markdown, пробуем без него
                result = loop.run_until_complete(send_message_async(text, parse_mode=None))
                print(f"✅ Сообщение отправлено успешно")
                return result
        finally:
            # Cleanup
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            try:
                if not loop.is_closed():
                    loop.close()
            except Exception:
                pass
    except Exception as e:
        error_type = type(e).__name__
        if "Event loop is closed" not in str(e):
            print(f"❌ Ошибка при отправке сообщения: {error_type}: {e}")
            import traceback
            print(f"Трассировка:\n{traceback.format_exc()}")

def _send_chart_sync(symbol):
    """
    Синхронная обертка для send_chart_async.
    Используется через asyncio.to_thread() из async контекста.
    """
    print(f"📤 Попытка отправить график для {symbol}")
    
    # Создаем новый event loop в текущем потоке (вызывается из asyncio.to_thread)
    # Это безопасно, так как вызывается из отдельного потока
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(send_chart_async(symbol))
            print(f"✅ График отправлен успешно для {symbol}")
            return result
        finally:
            # Cleanup
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            try:
                if not loop.is_closed():
                    loop.close()
            except Exception:
                pass
    except Exception as e:
        error_type = type(e).__name__
        if "Event loop is closed" not in str(e):
            print(f"❌ Ошибка при отправке графика для {symbol}: {error_type}: {e}")
            import traceback
            print(f"Трассировка:\n{traceback.format_exc()}")

def send_message(text):
    """
    Синхронная функция для отправки сообщения.
    
    ВАЖНО: Эта функция должна вызываться ТОЛЬКО через asyncio.to_thread()
    из async контекста. Прямой вызов из синхронного кода создаст новый event loop,
    что может привести к RuntimeError.
    
    Для использования из async контекста используйте send_message_async().
    """
    return _send_message_sync(text)

def send_chart(symbol):
    """
    Синхронная функция для отправки графика.
    
    ВАЖНО: Эта функция должна вызываться ТОЛЬКО через asyncio.to_thread()
    из async контекста. Прямой вызов из синхронного кода создаст новый event loop,
    что может привести к RuntimeError.
    
    Для использования из async контекста используйте send_chart_async().
    """
    return _send_chart_sync(symbol)

