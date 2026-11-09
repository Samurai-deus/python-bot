"""
Пул HTTP-клиента Telegram не остаётся забитым после сбоя прокси.

09.11.2026: основной бот три недели (с 19.10) не отправил ни одного сообщения — «Pool timeout: All connections
in the connection pool are occupied», хотя прокси давно ожил. Причина — httpcore 1.0.9 (последняя версия): при
таймауте TLS внутри туннеля через HTTP-прокси соединение навсегда остаётся в пуле «ACTIVE, Request Count: 1».
19.10 sing-box отвечал на CONNECT, но не мог достучаться до Telegram; каждая неудачная отправка съедала место,
и Bot главного цикла, живущий весь срок процесса, остался с пулом из мёртвых туннелей.

Тест воспроизводит это на настоящих httpx/httpcore через локальный «прокси»: сначала он отвечает 200 на CONNECT
и молчит (TLS не доходит), потом отвечает 502 сразу. Исправная отправка во второй фазе получает ответ прокси
(502), а не «Pool timeout».
"""
import asyncio
import weakref

import pytest
from telegram.error import NetworkError, TimedOut
from telegram.request import HTTPXRequest

import telegram_bot

POOL = 2
TIMEOUT = 0.3


class _Proxy:
    def __init__(self) -> None:
        self.mode = "hang_after_connect"
        self.release = asyncio.Event()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            if self.mode == "hang_after_connect":
                writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                await writer.drain()
                await self.release.wait()
            else:
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()


@pytest.fixture
def telegram_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:TEST")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.delenv("TELEGRAM_PROXY_URL", raising=False)
    monkeypatch.setattr(telegram_bot, "_loop_bots", weakref.WeakKeyDictionary())
    # Каждый HTTP-клиент грузит SSL-контекст заново (на Windows ≈ 2 с; Bot создаёт два клиента) — тест про пул,
    # не про сертификаты: один контекст на всех. TLS здесь всё равно не завершается — «прокси» молчит.
    import httpx._transports.default as transports
    shared = transports.create_ssl_context()
    monkeypatch.setattr(transports, "create_ssl_context", lambda *a, **k: shared)


def test_send_recovers_after_tunnel_timeouts(monkeypatch, telegram_env):
    async def scenario() -> Exception:
        proxy = _Proxy()
        server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        def small_request(connection_pool_size: int = 10) -> HTTPXRequest:
            return HTTPXRequest(connection_pool_size=POOL, pool_timeout=TIMEOUT, read_timeout=TIMEOUT,
                                write_timeout=TIMEOUT, connect_timeout=TIMEOUT, proxy=f"http://127.0.0.1:{port}")

        monkeypatch.setattr(telegram_bot, "build_request", small_request)
        try:
            for _ in range(POOL + 1):              # больше неудач, чем мест в пуле
                with pytest.raises(TimedOut):
                    await telegram_bot._send("x", retry_count=0)
            proxy.mode = "fast_502"                  # прокси ожил (отвечает сразу)
            proxy.release.set()
            with pytest.raises(NetworkError) as err:
                await telegram_bot._send("x", retry_count=0)
            return err.value
        finally:
            proxy.release.set()
            server.close()
            await server.wait_closed()

    err = asyncio.run(scenario())
    assert not isinstance(err, TimedOut), f"пул остался забит после сбоя прокси: {err}"
    assert "502" in str(err)


def test_sync_send_closes_its_loop_client(telegram_env):
    """Отправка из потока (spike_alert, error_alert…) создаёт Bot в своём цикле — закрывается вместе с циклом."""
    loop = asyncio.new_event_loop()

    async def make():
        return telegram_bot.get_bot()

    bot = loop.run_until_complete(make())
    client = bot.request._client
    assert not client.is_closed
    telegram_bot._cleanup_loop(loop)
    assert client.is_closed
    assert loop.is_closed()
