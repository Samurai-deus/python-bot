"""
Токены — вон из логов.

Токен бота — часть URL каждого запроса к Telegram: https://api.telegram.org/bot<токен>/getUpdates.
httpx на уровне INFO пишет URL каждого запроса. Так утёк прежний токен: строки
лога с ним попали в tests/rso_report_*.json, а те — в публичный репозиторий.
После выкладки 10.09.2026 в логах контейнера оказался и новый токен — той же
строкой «HTTP Request: POST https://api.telegram.org/bot…/deleteWebhook».

Две меры, обе нужны:
  • httpx и httpcore — только WARNING и выше: URL каждого запроса в логе не нужен;
  • фильтр на всех обработчиках корневого логгера вырезает токен из любой строки —
    и из сообщения, и из трассировки исключения. Это страховка на случай, когда
    URL с токеном окажется в тексте ошибки или в чужом логгере.
"""
import logging
import re

# bot<id>:<secret> внутри URL и «голый» <id>:<secret>. Id бота — 5–12 цифр,
# секрет — от 20 символов из алфавита base64url.
_TOKEN_IN_URL = re.compile(r"(bot\d{5,12}):[A-Za-z0-9_-]{20,}")
_BARE_TOKEN = re.compile(r"(?<![\w/])(\d{5,12}):[A-Za-z0-9_-]{30,}")

_NOISY_LOGGERS = ("httpx", "httpcore")


def redact(text: str) -> str:
    """Заменяет секретную часть токена на *** (id бота оставляем — он не секрет)."""
    if not text:
        return text
    text = _TOKEN_IN_URL.sub(r"\1:***", text)
    return _BARE_TOKEN.sub(r"\1:***", text)


class SecretRedactingFilter(logging.Filter):
    """Фильтр обработчика: чистит сообщение и трассировку до форматирования."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        cleaned = redact(message)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()

        # Трассировку форматтер строит ПОСЛЕ фильтров, из exc_info. Строим её здесь
        # сами и чистим: форматтер возьмёт готовый exc_text и exc_info не тронет.
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


def install_log_redaction(logger: logging.Logger = None) -> None:
    """Приглушить httpx/httpcore и повесить фильтр на все обработчики логгера."""
    target = logger or logging.getLogger()
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    for handler in target.handlers:
        if not any(isinstance(f, SecretRedactingFilter) for f in handler.filters):
            handler.addFilter(SecretRedactingFilter())
