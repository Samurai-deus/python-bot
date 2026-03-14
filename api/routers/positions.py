"""
Open positions and trade history endpoints.
"""
from typing import List
from fastapi import APIRouter, Depends, Query

from api.deps import run_sync, verify_auth
from api.models import PositionResponse, TradeHistoryItem

router = APIRouter(prefix="/api/positions", tags=["positions"])


@router.get("/open", response_model=List[PositionResponse])
async def get_open_positions(_: dict = Depends(verify_auth)):
    from database import get_open_positions

    rows = await run_sync(get_open_positions)
    return [
        PositionResponse(
            id=r["id"],
            symbol=r["symbol"],
            side=r["side"],
            qty=r["qty"],
            entry_price=r["entry_price"],
            stop_loss=r.get("stop_loss"),
            take_profit=r.get("take_profit"),
            opened_at=r["opened_at"],
        )
        for r in rows
    ]


@router.get("/history", response_model=List[TradeHistoryItem])
async def get_trade_history(
    days: int = Query(default=30, ge=1, le=365),
    _: dict = Depends(verify_auth),
):
    from database import get_closed_trades

    rows = await run_sync(get_closed_trades, days)
    return [
        TradeHistoryItem(
            symbol=r["symbol"],
            side=r["side"],
            entry_price=r["entry_price"],
            exit_price=r["exit_price"],
            quantity=r["quantity"],
            net_pnl=r["net_pnl"],
            closed_at=r["closed_at"],
        )
        for r in rows
    ]
