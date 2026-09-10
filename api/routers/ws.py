"""
WebSocket endpoint — real-time push every 5 seconds.
"""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, UTC

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.deps import ws_user

logger = logging.getLogger(__name__)

# Bounded executor: prevents thread leak when DB calls are slow.
# wait_for(run_in_executor(None, fn)) cancels the Future but the thread
# continues running — with the default unbounded pool this leaks threads.
# A bounded pool (4 threads) caps the damage: slow calls queue up and
# complete naturally instead of spawning infinite zombie threads.
_ws_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ws-db")

router = APIRouter(tags=["websocket"])


# Лимиты соединений (2.7). Каждое соединение раз в 5 секунд ходит в базу через
# пул на 4 потока; без лимита один допущенный клиент мог открыть сколько угодно
# сокетов и занять пул целиком. Mini App держит одно соединение, три — запас на
# переподключение и второе устройство.
MAX_CONNECTIONS_PER_USER = 3
MAX_CONNECTIONS_TOTAL = 50


class ConnectionManager:
    def __init__(self, per_user: int = MAX_CONNECTIONS_PER_USER, total: int = MAX_CONNECTIONS_TOTAL):
        self._connections: dict = {}  # WebSocket -> user_id
        self._lock = asyncio.Lock()
        self._per_user = per_user
        self._total = total

    async def add(self, ws: WebSocket, user_id=None) -> bool:
        """Зарегистрировать соединение; False — лимит исчерпан, соединение закрыть."""
        async with self._lock:
            if len(self._connections) >= self._total:
                return False
            if sum(1 for uid in self._connections.values() if uid == user_id) >= self._per_user:
                return False
            self._connections[ws] = user_id
            return True

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            self._connections.pop(ws, None)

    def count(self, user_id=None) -> int:
        if user_id is None:
            return len(self._connections)
        return sum(1 for uid in self._connections.values() if uid == user_id)


manager = ConnectionManager()


async def _push_loop(ws: WebSocket):
    """Background task: send system snapshot every 5 seconds."""
    from fastapi import WebSocketDisconnect as _WSD
    try:
        while True:
            try:
                payload = await _build_snapshot()
                await ws.send_json(payload)
            except (_WSD, RuntimeError) as exc:
                # Client disconnected — stop the loop
                logger.debug("WS client disconnected: %s", exc)
                break
            except Exception as exc:
                # Serialisation or transient error — log and keep pushing
                logger.warning("WS push error (non-fatal): %s", exc)
            await asyncio.sleep(5)
    except asyncio.CancelledError:
        logger.debug("WS push task cancelled")
        raise


async def _safe_run_sync(fn, default=None, timeout: float = 5.0):
    """Run sync fn in bounded executor with timeout.

    Uses _ws_executor (4 threads max) instead of the default unbounded pool.
    On timeout the Future is cancelled but the thread finishes naturally
    within the bounded pool — no infinite thread leak.
    """
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_ws_executor, fn), timeout=timeout
        )
    except asyncio.TimeoutError:
        logger.warning("_safe_run_sync timeout for %s — returning default", fn.__name__)
        return default
    except Exception as exc:
        logger.warning("_safe_run_sync error for %s: %s", fn.__name__, exc)
        return default


async def _build_snapshot() -> dict:
    from system_state_machine import get_state_machine
    from database import get_open_positions
    from capital import get_current_balance

    sm = get_state_machine()
    info = sm.get_state_info()

    positions_raw = await _safe_run_sync(get_open_positions, default=[])
    # Баланс — из capital, как в /api/system/health; раньше здесь вызывалась
    # функция базы без аргумента со скрытым значением по умолчанию 10 000.
    balance = await _safe_run_sync(get_current_balance, default=None)

    # Подтягиваем последние цены из кэша (обновляется signal_generator каждые ~5 мин)
    try:
        import price_cache as _pc
        _cached_prices = _pc.snapshot()
    except Exception:
        _cached_prices = {}

    positions = [
        {
            "id": p["id"],
            "symbol": p["symbol"],
            "side": p["side"],
            "qty": p["qty"],
            "entry_price": p["entry_price"],
            "stop_loss": p.get("stop_loss"),
            "take_profit": p.get("take_profit"),
            "current_price": _cached_prices.get(p["symbol"]),
            "opened_at": p["opened_at"],
        }
        for p in positions_raw
    ]

    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "system_state": info["state"],
        "trading_paused": sm.trading_paused,
        "balance_usdt": balance,
        "positions": positions,
    }


_PING_INTERVAL = 30  # seconds — send server-side ping if client is silent
# Сколько ждать сообщения с токеном после открытия сокета. Фронт шлёт его сразу
# в onopen, так что 5 секунд — с запасом на мобильную сеть. Было 10: каждое
# неаутентифицированное соединение держало слот вдвое дольше.
_AUTH_TIMEOUT = 5


@router.websocket("/api/ws")
async def websocket_endpoint(ws: WebSocket):
    # Accept first so we can send a close frame on auth failure
    await ws.accept()

    # Токен принимается ТОЛЬКО первым сообщением. Прежний запасной путь через
    # ?token= в URL убран: адрес с query-строкой попадает в access-log nginx,
    # то есть initData — ключ к данным бота на сутки — лежал бы в логах. Фронт
    # этим путём не пользуется (miniapp/src/hooks/useWebSocket.ts шлёт auth-сообщение).
    token = ""
    try:
        msg = await asyncio.wait_for(ws.receive_json(), timeout=_AUTH_TIMEOUT)
        if isinstance(msg, dict) and msg.get("type") == "auth":
            token = str(msg.get("token", ""))
        else:
            logger.warning("WS: unexpected first message type '%s'", msg.get("type") if isinstance(msg, dict) else type(msg))
    except asyncio.TimeoutError:
        logger.warning("WS: auth timeout — closing connection")
        await ws.close(code=4001, reason="Auth timeout")
        return
    except Exception as exc:
        logger.warning("WS: error waiting for auth message: %s", exc)
        await ws.close(code=4001, reason="Auth error")
        return

    user = ws_user(token)
    if user is None:
        await ws.close(code=4001, reason="Unauthorized")
        return

    if not await manager.add(ws, user.get("user_id")):
        logger.warning("WS: лимит соединений исчерпан (user_id=%s, всего %s)", user.get("user_id"), manager.count())
        await ws.close(code=4008, reason="Too many connections")
        return
    task = asyncio.create_task(_push_loop(ws))
    try:
        # Keep-alive loop: detect stale connections via receive timeout.
        # If no message from client in _PING_INTERVAL seconds, send a ping frame
        # and wait for any response. Stale connections are closed on next timeout.
        while True:
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=_PING_INTERVAL)
            except asyncio.TimeoutError:
                try:
                    await ws.send_json({"type": "ping"})
                except Exception:
                    break  # client unreachable — exit cleanly
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await manager.disconnect(ws)
