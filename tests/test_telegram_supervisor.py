"""
Супервизор Telegram, вынесенный из runner.py в loops/telegram_supervisor.py
(пункт 5 плана отложенного, шаг 5). Приложение PTB подменено: проверяется
изоляция сбоев, а не сама библиотека.
"""
import asyncio
import pathlib
from types import SimpleNamespace

import pytest

from loops import telegram_supervisor as supervisor

ROOT = pathlib.Path(__file__).resolve().parent.parent


def run(coro, timeout=5.0):
    return asyncio.run(asyncio.wait_for(coro, timeout))


def make_state():
    errors = []
    state = SimpleNamespace(system_health=SimpleNamespace(is_running=True), record_error=errors.append)
    return state, errors


class FakeUpdater:
    def __init__(self, log):
        self.log = log
        self.running = False

    async def start_polling(self, **kwargs):
        self.running = True
        self.log.append("polling")
        await asyncio.Event().wait()  # долгоживущий polling

    async def stop(self):
        self.running = False
        self.log.append("updater.stop")


class FakeApp:
    def __init__(self, log, on_initialize=None):
        self.log = log
        self.updater = FakeUpdater(log)
        self.running = False
        self._on_initialize = on_initialize

    async def initialize(self):
        self.log.append("initialize")
        if self._on_initialize:
            self._on_initialize()

    async def start(self):
        self.running = True
        self.log.append("start")

    async def stop(self):
        self.running = False
        self.log.append("stop")

    async def shutdown(self):
        self.log.append("shutdown")


class FakeBuilder:
    """ApplicationBuilder: запоминает, какие клиенты HTTP ему дали."""

    def __init__(self, app, requests):
        self._app = app
        self._requests = requests

    def token(self, value):
        return self

    def request(self, value):
        self._requests["request"] = value
        return self

    def get_updates_request(self, value):
        self._requests["get_updates_request"] = value
        return self

    def build(self):
        return self._app() if callable(self._app) else self._app


class FakeRequest:
    """HTTP-клиент из build_request: помнит хук ответа и закрытие."""

    def __init__(self, on_response=None):
        self.on_response = on_response
        self.closed = False

    async def shutdown(self):
        self.closed = True


@pytest.fixture
def ptb(monkeypatch):
    import telegram.ext
    import telegram_bot
    import telegram_commands
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test")
    monkeypatch.setattr(telegram_commands, "setup_commands", lambda app: None)
    holder = {"requests": {}, "clients": []}

    def build_request(on_response=None):
        client = FakeRequest(on_response)
        holder["clients"].append(client)
        return client

    monkeypatch.setattr(telegram_bot, "build_request", build_request)

    def install(app):
        monkeypatch.setattr(telegram.ext, "ApplicationBuilder", lambda: FakeBuilder(app, holder["requests"]))

    holder["install"] = install
    return holder


def test_polling_starts_through_the_proxy_and_stops_cleanly(ptb):
    async def scenario():
        shutdown = asyncio.Event()
        log = []
        ptb["install"](FakeApp(log))
        state, errors = make_state()
        task = asyncio.create_task(supervisor.telegram_supervisor(lambda: state, shutdown))
        for _ in range(50):
            if "polling" in log:
                break
            await asyncio.sleep(0.01)
        shutdown.set()
        await task
        return log, errors

    log, errors = run(scenario())
    assert log[:3] == ["initialize", "start", "polling"]
    assert "shutdown" in log and errors == []
    send, updates = ptb["clients"]
    assert ptb["requests"] == {"request": send, "get_updates_request": updates}, "оба клиента — из build_request"
    assert send.on_response is None and updates.on_response is not None, "ответы getUpdates отмечаются"
    assert send.closed and updates.closed, "HTTP-клиенты закрываются при остановке"


@pytest.mark.timeout(15)  # исправный проходит за доли секунды; сломанный перезапуск зависает — пусть падает
def test_silent_polling_is_rebuilt_with_fresh_clients(ptb, monkeypatch):
    """
    PTB повторяет TimedOut опроса бесконечно и молча: забитый мёртвыми туннелями пул getUpdates супервизор видит
    только по отсутствию ответов. Тогда приложение пересоздаётся с новыми клиентами, старые закрываются; опрос,
    который получает ответы, не трогается.
    """
    monkeypatch.setattr(supervisor, "POLL_STALE_SECONDS", 0.05)
    monkeypatch.setattr(supervisor, "WAIT_STEP_SECONDS", 0.01)
    monkeypatch.setattr(supervisor, "INITIAL_BACKOFF_SECONDS", 0.01)

    async def scenario():
        shutdown = asyncio.Event()
        apps = []

        def new_app():
            apps.append(FakeApp([]))
            return apps[-1]

        ptb["install"](new_app)
        state, errors = make_state()
        task = asyncio.create_task(supervisor.telegram_supervisor(lambda: state, shutdown))
        for _ in range(200):
            if len(apps) == 2 and "polling" in apps[1].log:
                break
            await asyncio.sleep(0.01)
        for _ in range(20):                     # второй опрос получает ответы: 0,2 с — вчетверо дольше порога
            ptb["clients"][-1].on_response()
            await asyncio.sleep(0.01)
        shutdown.set()
        await task
        return apps, errors

    apps, errors = run(scenario())
    assert len(apps) == 2, "молчащий опрос пересоздан один раз, отвечающий — не тронут"
    assert "shutdown" in apps[0].log
    first_send, first_updates = ptb["clients"][:2]
    assert first_send.closed and first_updates.closed, "клиенты с забитым пулом закрыты, а не переиспользованы"
    assert len(ptb["clients"]) == 4
    assert errors == [], "сетевой сбой, а не ошибка процесса"


def test_network_failure_is_retried_without_recording_an_error(ptb):
    from telegram.error import NetworkError

    async def scenario():
        shutdown = asyncio.Event()

        def fail():
            shutdown.set()
            raise NetworkError("no route")

        ptb["install"](FakeApp([], on_initialize=fail))
        state, errors = make_state()
        await supervisor.telegram_supervisor(lambda: state, shutdown)
        return errors

    assert run(scenario()) == []


def test_unexpected_failure_is_recorded_and_retried(ptb):
    async def scenario():
        shutdown = asyncio.Event()

        def fail():
            shutdown.set()
            raise ValueError("boom")

        ptb["install"](FakeApp([], on_initialize=fail))
        state, errors = make_state()
        await supervisor.telegram_supervisor(lambda: state, shutdown)
        return errors

    assert run(scenario()) == ["TELEGRAM_SUPERVISOR: ValueError"]


def test_runner_registers_the_supervisor_and_dead_polling_task_is_gone():
    text = (ROOT / "runner.py").read_text(encoding="utf-8")
    assert ('telegram_supervisor.telegram_supervisor(_state, get_shutdown_event()), '
            'name="TelegramSupervisor"') in text
    assert "async def telegram_supervisor(" not in text
    assert "_telegram_polling_task" not in text, "мёртвая копия polling вернулась"
