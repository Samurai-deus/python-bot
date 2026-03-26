import logging
import os
import time as _time
import warnings
import asyncio

from dotenv import load_dotenv

load_dotenv()

# Подавляем предупреждение от apscheduler (используется python-telegram-bot)
warnings.filterwarnings("ignore", category=UserWarning, module="apscheduler")

from telegram import Bot
from telegram.ext import ApplicationBuilder  # Только для создания Application (polling)
from telegram.request import HTTPXRequest

logger = logging.getLogger(__name__)

_token = os.environ.get("TELEGRAM_BOT_TOKEN")
_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
if not _token:
    raise ValueError("TELEGRAM_BOT_TOKEN is not set. Add it to your .env file.")
if not _chat_id:
    raise ValueError("TELEGRAM_CHAT_ID is not set. Add it to your .env file.")

TOKEN = _token
CHAT_ID = _chat_id

# ---------------------------------------------------------------------------
# Rate limiter: не более ~3 сообщений/сек в один чат (Telegram 429 prevention)
# ---------------------------------------------------------------------------
_rate_lock = asyncio.Lock()
_last_send_time: float = 0.0
_MIN_SEND_INTERVAL: float = 0.35  # секунды между отправками


async def _apply_rate_limit() -> None:
    """Ожидает минимальный интервал между отправками, чтобы не словить Telegram 429."""
    global _last_send_time
    async with _rate_lock:
        elapsed = _time.monotonic() - _last_send_time
        if elapsed < _MIN_SEND_INTERVAL:
            await asyncio.sleep(_MIN_SEND_INTERVAL - elapsed)
        _last_send_time = _time.monotonic()


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
            # Таймауты управляются HTTPXRequest (read/write/connect/pool = 30s каждый).
            # asyncio.wait_for здесь не нужен: он отменяет Task через cancel(),
            # что может повредить пул соединений httpx.
            if parse_mode:
                try:
                    result = await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode=parse_mode)
                    logger.debug("Сообщение отправлено в Telegram с %s (message_id: %d)", parse_mode, result.message_id)
                    return result
                except Exception as parse_error:
                    # Если ошибка парсинга, пробуем без parse_mode
                    if "parse" in str(parse_error).lower() or "markdown" in str(parse_error).lower():
                        logger.warning("Ошибка парсинга %s, пробуем без parse_mode: %s", parse_mode, parse_error)
                        result = await bot.send_message(chat_id=CHAT_ID, text=text)
                        logger.debug("Сообщение отправлено в Telegram без parse_mode (message_id: %d)", result.message_id)
                        return result
                    raise
            else:
                result = await bot.send_message(chat_id=CHAT_ID, text=text)
                logger.debug("Сообщение отправлено в Telegram (message_id: %d)", result.message_id)
                return result
        except (TimedOut, NetworkError) as e:
            if attempt < retry_count:
                wait_time = (attempt + 1) * 2  # Увеличиваем задержку с каждой попыткой
                logger.warning(
                    "Ошибка соединения (попытка %d/%d): %s. Повтор через %d сек...",
                    attempt + 1, retry_count + 1, e, wait_time,
                )
                await asyncio.sleep(wait_time)
                continue
            else:
                logger.error(
                    "Ошибка при отправке сообщения в Telegram после %d попыток: %s: %s",
                    retry_count + 1, type(e).__name__, e,
                )
                raise
        except Exception as e:
            # Для других ошибок не повторяем
            logger.error(
                "Ошибка при отправке сообщения в Telegram: %s: %s", type(e).__name__, e,
            )
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
        logger.debug("График отправлен в Telegram для %s", symbol)
        return result
    except Exception as e:
        logger.error("Ошибка при отправке графика для %s: %s: %s", symbol, type(e).__name__, e)
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
    await _apply_rate_limit()
    try:
        return await _send(text, parse_mode=parse_mode)
    except Exception as e:
        # Если не получилось с Markdown, пробуем без него
        logger.debug("Ошибка отправки с parse_mode=%s, повтор без форматирования: %s", parse_mode, e)
        try:
            return await _send(text, parse_mode=None)
        except Exception as e2:
            # Игнорируем ошибки - не блокируем основной процесс
            logger.warning("Не удалось отправить сообщение в Telegram: %s: %s", type(e2).__name__, e2)

async def send_chart_async(symbol):
    """
    Async версия отправки графика.
    Используется из async контекста (runner.py, supervisors).

    Args:
        symbol: Символ для графика
    """
    await _apply_rate_limit()
    try:
        return await _send_chart(symbol)
    except Exception as e:
        # Игнорируем ошибки - не блокируем основной процесс
        logger.warning("Не удалось отправить график для %s: %s: %s", symbol, type(e).__name__, e)


# ===============================
# SYNC API (для обратной совместимости)
# ===============================
# Эти функции используются из синхронного кода через asyncio.to_thread()

def _cleanup_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Отменяет незавершённые задачи и закрывает event loop."""
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


def _send_message_sync(text):
    """
    Синхронная обертка для send_message_async.
    Используется через asyncio.to_thread() из async контекста.
    """
    logger.debug("Попытка отправить сообщение в Telegram (длина: %d символов)", len(text))

    # Создаем новый event loop в текущем потоке (вызывается из asyncio.to_thread)
    # Это безопасно, так как вызывается из отдельного потока
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Пытаемся отправить с Markdown, если не получится - без него
            try:
                result = loop.run_until_complete(send_message_async(text, parse_mode="Markdown"))
                return result
            except Exception:
                # Если не получилось с Markdown, пробуем без него
                result = loop.run_until_complete(send_message_async(text, parse_mode=None))
                return result
        finally:
            _cleanup_loop(loop)
    except Exception as e:
        if "Event loop is closed" not in str(e):
            logger.error("Ошибка при отправке сообщения: %s: %s", type(e).__name__, e, exc_info=True)


def _send_chart_sync(symbol):
    """
    Синхронная обертка для send_chart_async.
    Используется через asyncio.to_thread() из async контекста.
    """
    logger.debug("Попытка отправить график для %s", symbol)

    # Создаем новый event loop в текущем потоке (вызывается из asyncio.to_thread)
    # Это безопасно, так как вызывается из отдельного потока
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(send_chart_async(symbol))
            return result
        finally:
            _cleanup_loop(loop)
    except Exception as e:
        if "Event loop is closed" not in str(e):
            logger.error("Ошибка при отправке графика для %s: %s: %s", symbol, type(e).__name__, e, exc_info=True)

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

