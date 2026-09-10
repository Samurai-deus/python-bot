"""
Токен бота не должен попадать в логи (10.09.2026).

Токен — часть URL каждого запроса к Telegram, а httpx на уровне INFO пишет URL.
Так утёк прежний токен, и так же после выкладки в логах контейнера оказался новый.

Тестовый «токен» намеренно не той формы, что у настоящего (после двоеточия не
«A» и не 34 символа): иначе его принял бы за секрет gitleaks в CI.
"""
import io
import logging

import pytest

from utils.log_redaction import SecretRedactingFilter, install_log_redaction, redact

FAKE_TOKEN = "123456789:XYZtestonlynotarealtoken_0000000000"
SECRET_PART = FAKE_TOKEN.split(":", 1)[1]
URL_LINE = f'HTTP Request: POST https://api.telegram.org/bot{FAKE_TOKEN}/getUpdates "HTTP/1.1 200 OK"'


def test_token_inside_url_is_redacted():
    out = redact(URL_LINE)
    assert SECRET_PART not in out
    assert "bot123456789:***" in out, "id бота оставляем — он не секрет, а по нему видно, какой бот"


def test_bare_token_is_redacted():
    out = redact(f"TELEGRAM_BOT_TOKEN={FAKE_TOKEN}")
    assert SECRET_PART not in out


@pytest.mark.parametrize("text", [
    "BTCUSDT 78110.40 at 12:30:45",
    "risk:reward 1:2.5",
    "timestamp=2026-09-10T08:37:39.811567+00:00 level=INFO",
    "order_id=mbot_BTCUSDT_1789023367",
])
def test_ordinary_text_is_untouched(text):
    assert redact(text) == text


@pytest.fixture
def captured():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger = logging.getLogger("test_log_redaction")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    install_log_redaction(logger)
    yield logger, stream
    logger.handlers = []


def test_filter_cleans_formatted_arguments(captured):
    logger, stream = captured
    logger.info("HTTP Request: POST %s", f"https://api.telegram.org/bot{FAKE_TOKEN}/getUpdates")
    assert SECRET_PART not in stream.getvalue()
    assert "bot123456789:***" in stream.getvalue()


def test_filter_cleans_exception_traceback(captured):
    logger, stream = captured
    try:
        raise ConnectionError(f"failed to reach https://api.telegram.org/bot{FAKE_TOKEN}/getMe")
    except ConnectionError:
        logger.exception("Telegram недоступен")
    output = stream.getvalue()
    assert "ConnectionError" in output, "трассировка должна остаться — пропасть должен только токен"
    assert SECRET_PART not in output


def test_filter_is_installed_once(captured):
    logger, _ = captured
    install_log_redaction(logger)
    filters = [f for f in logger.handlers[0].filters if isinstance(f, SecretRedactingFilter)]
    assert len(filters) == 1


def test_httpx_request_logging_is_quieted():
    install_log_redaction(logging.getLogger("test_log_redaction_quiet"))
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_runner_logging_setup_installs_the_filter():
    """Логирование бота — через runner.setup_structured_logging; фильтр обязан быть там."""
    import runner
    root = runner.setup_structured_logging()
    assert root.handlers, "у корневого логгера нет обработчиков"
    for handler in root.handlers:
        assert any(isinstance(f, SecretRedactingFilter) for f in handler.filters), handler
