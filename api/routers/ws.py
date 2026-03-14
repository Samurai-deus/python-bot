"""
WebSocket endpoint — real-time push every 5 seconds.
"""
import asyncio
import logging
from datetime import datetime, UTC

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


class ConnectionManager:
    def __init__(self):
        self._connections: set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.add(ws)

    def disconnect(self, ws: WebSocket):
        self._connections.discard(ws)


manager = ConnectionManager()


async def _push_loop(ws: WebSocket):
    """Background task: send system snapshot every 5 seconds."""
    while True:
        try:
            payload = await _build_snapshot()
            await ws.send_json(payload)
        except Exception as exc:
            logger.debug("WS push error: %s", exc)
            break
        await asyncio.sleep(5)


async def _build_snapshot() -> dict:
    from system_state_machine import get_state_machine
    from database import get_open_positions, get_current_balance_from_db

    loop = asyncio.get_event_loop()

    sm = get_state_machine()
    info = sm.get_state_info()

    positions_raw = await loop.run_in_executor(None, get_open_positions)
    balance = await loop.run_in_executor(None, get_current_balance_from_db)

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


@router.websocket("/api/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    task = asyncio.create_task(_push_loop(ws))
    try:
        # Keep alive: wait for client disconnect
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        manager.disconnect(ws)
