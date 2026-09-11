"""
Прокси для Telegram: на прод-хосте api.telegram.org напрямую недоступен
(соединение отваливается по таймауту), поэтому ВСЕ HTTP-клиенты бота обязаны
идти через TELEGRAM_PROXY_URL — и отправка, и приём обновлений.

Сначала прокси был добавлен только в клиент отправки. Бот писал бы владельцу,
но не видел бы ни одной команды: PTB создаёт отдельный клиент для getUpdates,
и runner собирал его сам, без прокси.
"""
import io
import pathlib
import tokenize

import pytest

import telegram_bot


class _Recorder:
    calls = []

    def __init__(self, **kwargs):
        _Recorder.calls.append(kwargs)


@pytest.fixture
def recorder(monkeypatch):
    _Recorder.calls = []
    monkeypatch.setattr(telegram_bot, "HTTPXRequest", _Recorder)
    return _Recorder


def test_proxy_is_applied_when_configured(recorder, monkeypatch):
    monkeypatch.setenv("TELEGRAM_PROXY_URL", "http://host.docker.internal:12334")
    telegram_bot.build_request()
    assert recorder.calls[-1]["proxy"] == "http://host.docker.internal:12334"


def test_direct_connection_when_not_configured(recorder, monkeypatch):
    monkeypatch.delenv("TELEGRAM_PROXY_URL", raising=False)
    telegram_bot.build_request()
    assert "proxy" not in recorder.calls[-1]


def test_sending_client_uses_the_shared_builder(recorder, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:TEST")
    monkeypatch.setenv("TELEGRAM_PROXY_URL", "http://proxy.test:1")
    monkeypatch.setattr(telegram_bot, "_bot", None)
    monkeypatch.setattr(telegram_bot, "Bot", lambda token, request: ("bot", token, request))
    telegram_bot.get_bot()
    assert recorder.calls and recorder.calls[-1]["proxy"] == "http://proxy.test:1"


def test_nobody_else_constructs_telegram_http_clients():
    """
    Гейт: HTTPXRequest создаётся только в telegram_bot.py. Любое другое место —
    это клиент, который пойдёт в Telegram мимо прокси.
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    allowed = {(root / "telegram_bot.py").resolve()}
    skip = {"venv", ".venv", "archive", "node_modules", ".git", "miniapp", "tests"}

    offenders = []
    for path in root.rglob("*.py"):
        if any(part in skip for part in path.parts) or path.resolve() in allowed:
            continue
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline))
        except (UnicodeDecodeError, tokenize.TokenError, SyntaxError, IndentationError):
            continue
        code = [t for t in tokens if t.type not in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE)]
        for i, tok in enumerate(code[:-1]):
            if tok.type == tokenize.NAME and tok.string == "HTTPXRequest" and code[i + 1].string == "(":
                offenders.append(f"{path.relative_to(root)}:{tok.start[0]}")

    assert not offenders, "HTTPXRequest создаётся в обход telegram_bot.build_request: " + ", ".join(offenders)


def test_runner_passes_proxied_client_for_get_updates_too():
    """
    Гейт выше ловит создание HTTPXRequest в обход, но не его ОТСУТСТВИЕ: если
    убрать .get_updates_request(...), PTB молча создаст клиент для getUpdates
    сам — без прокси, и бот перестанет получать команды. Проверяем, что runner
    передаёт оба клиента из build_request.
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    source = (root / "loops" / "telegram_supervisor.py").read_text(encoding="utf-8")
    skip = (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT)
    code = "".join(t.string for t in tokenize.generate_tokens(io.StringIO(source).readline) if t.type not in skip)
    assert ".request(build_request())" in code
    assert ".get_updates_request(build_request())" in code
