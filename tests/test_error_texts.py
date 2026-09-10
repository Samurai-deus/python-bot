"""
Тексты исключений не уходят в ответы бота (2.10, 10.09.2026).

Исключения httpx несут URL запроса, а в URL к Telegram входит токен бота:
ответ «{type(e).__name__}: {e}» мог отправить токен в чат. Подробности —
только в лог, где их вычищает фильтр utils/log_redaction.py.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Вызов, отправляющий текст пользователю, с подстановкой исключения в f-строке.
LEAK = re.compile(
    r"(reply_func|reply_text|send_message|send_message_async|edit_message_text|answer)\(\s*f[\"'][^\n]*\{(e|exc|err|error|ex)(!s|!r)?\}"
)


def test_bot_replies_do_not_carry_exception_text():
    text = (ROOT / "telegram_commands.py").read_text(encoding="utf-8")
    leaks = [ln.strip() for ln in text.splitlines() if LEAK.search(ln)]
    assert leaks == [], f"текст исключения уходит пользователю: {leaks}"


def test_the_pattern_catches_the_old_form():
    """Обратная сторона: шаблон узнаёт то, что было в коде до исправления."""
    assert LEAK.search('await reply_func(f"❌ Ошибка получения статуса: {e}")')
    assert LEAK.search('await reply_func(f"❌ **Ошибка**\\n\\n{type(e).__name__}: {e}")')
    assert not LEAK.search('logger.error("Ошибка: %s", e)')
