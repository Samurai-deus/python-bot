"""
WebSocket endpoint — real-time push every 5 seconds.
"""
import asyncio
import logging
from datetime import datetime, UTC

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from api.deps import verify_ws_token

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


class ConnectionManager:
    def __init__(self):
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            self._connections.discard(ws)


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


async def _build_snapshot() -> dict:
    from system_state_machine import get_state_machine
    from database import get_open_positions, get_current_balance_from_db

    loop = asyncio.get_running_loop()

    sm = get_state_machine()
    info = sm.get_state_info()

    positions_raw = await asyncio.wait_for(loop.run_in_executor(None, get_open_positions), timeout=5.0)
    balance = await asyncio.wait_for(loop.run_in_executor(None, get_current_balance_from_db), timeout=5.0)

    positions = [
        {
            "id": p["id"],
            "symbol": p["symbol"],
            "side": p["side"],
            "qty": p["qty"],
            "entry_price": p["entry_price"],
            "stop_loss": p.get("stop_loss"),
            "take_profit": p.get("take_profit"),
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


@router.websocket("/api/ws")
async def websocket_endpoint(ws: WebSocket, token: str = Query(default="")):
    if not verify_ws_token(token):
        await ws.close(code=4001, reason="Unauthorized")
        return
    await manager.connect(ws)
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
