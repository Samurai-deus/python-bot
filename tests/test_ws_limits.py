"""
Лимиты соединений WebSocket (2.7, 10.09.2026).

Каждое соединение раз в 5 секунд ходит в базу через пул на 4 потока. Без
лимита один допущенный клиент мог открыть сколько угодно сокетов и занять пул.
"""
import asyncio

from api.routers.ws import ConnectionManager, MAX_CONNECTIONS_PER_USER


class _Socket:
    """Соединению нужна только идентичность — это ключ в реестре."""


def _run(coro):
    return asyncio.run(coro)


def test_per_user_limit():
    async def scenario():
        mgr = ConnectionManager(per_user=2, total=10)
        a, b, c = _Socket(), _Socket(), _Socket()
        assert await mgr.add(a, 1)
        assert await mgr.add(b, 1)
        assert not await mgr.add(c, 1), "третье соединение того же пользователя — сверх лимита"
        assert await mgr.add(c, 2), "у другого пользователя свой лимит"
        await mgr.disconnect(a)
        assert await mgr.add(_Socket(), 1), "после закрытия слот освобождается"
    _run(scenario())


def test_total_limit():
    async def scenario():
        mgr = ConnectionManager(per_user=10, total=3)
        for uid in range(3):
            assert await mgr.add(_Socket(), uid)
        assert not await mgr.add(_Socket(), 99)
        assert mgr.count() == 3
    _run(scenario())


def test_disconnect_of_unknown_socket_is_harmless():
    async def scenario():
        mgr = ConnectionManager()
        await mgr.disconnect(_Socket())
        assert mgr.count() == 0
    _run(scenario())


def test_default_limit_leaves_room_for_reconnects():
    assert MAX_CONNECTIONS_PER_USER >= 2
