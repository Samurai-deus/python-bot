"""
send_owner_blocking (telegram_bot): отправка владельцу из исполнителей без своего цикла событий — свой Bot
на каждый вызов (21.09.2026: общий Bot во втором asyncio.run падал «Event loop is closed»), результат bool
(21.09 сводка И13 не ушла, а отметка «отправлено» встала). Сеть подменена поддельным Bot.
"""
import telegram_bot


class FakeBot:
    made = []
    fail = 0

    def __init__(self, token, request):
        FakeBot.made.append(self)
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send_message(self, chat_id, text):
        if FakeBot.fail > 0:
            FakeBot.fail -= 1
            raise RuntimeError("Event loop is closed")
        self.sent.append((chat_id, text))


def setup(monkeypatch, fail=0):
    FakeBot.made, FakeBot.fail = [], fail
    monkeypatch.setattr(telegram_bot, "Bot", FakeBot)
    monkeypatch.setattr(telegram_bot._time, "sleep", lambda s: None)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")


def test_each_call_uses_its_own_bot_and_reports_success(monkeypatch):
    setup(monkeypatch)
    assert telegram_bot.send_owner_blocking("a") and telegram_bot.send_owner_blocking("b")
    assert len(FakeBot.made) == 2, "свой Bot на вызов — клиент не переживает закрытый цикл"
    assert [m.sent for m in FakeBot.made] == [[("42", "a")], [("42", "b")]]


def test_retries_then_reports_failure(monkeypatch):
    setup(monkeypatch, fail=1)
    assert telegram_bot.send_owner_blocking("x"), "вторая попытка прошла"
    setup(monkeypatch, fail=10)
    assert telegram_bot.send_owner_blocking("x", attempts=3) is False and len(FakeBot.made) == 3
