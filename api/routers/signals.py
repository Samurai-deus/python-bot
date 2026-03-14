"""
Signal endpoints.
"""
from typing import List
from fastapi import APIRouter, Depends, Query

from api.deps import run_sync, verify_auth
from api.models import SignalResponse

router = APIRouter(prefix="/api/signals", tags=["signals"])


@router.get("/latest", response_model=List[SignalResponse])
async def get_latest_signals(
    limit: int = Query(default=20, ge=1, le=100),
    _: dict = Depends(verify_auth),
):
    try:
        from core.signal_snapshot_store import SignalSnapshotStore

        store = SignalSnapshotStore()
        snap = store.load_latest()
        if snap is None:
            return []
        # Single snapshot — wrap in list
        return [
            SignalResponse(
                symbol=snap.symbol,
                decision=str(snap.decision),
                confidence=getattr(snap, "confidence", None),
                timestamp=snap.timestamp.isoformat() if hasattr(snap.timestamp, "isoformat") else str(snap.timestamp),
            )
        ]
    except Exception:
        return []


@router.get("/history", response_model=List[SignalResponse])
async def get_signal_history(
    symbol: str = Query(default="", description="Filter by symbol"),
    days: int = Query(default=7, ge=1, le=90),
    _: dict = Depends(verify_auth),
):
    from database import get_closed_trades

    try:
        rows = await run_sync(get_closed_trades, days)
        result = []
        for r in rows:
            if symbol and r["symbol"] != symbol:
                continue
            result.append(
                SignalResponse(
                    symbol=r["symbol"],
                    decision=r.get("side", "UNKNOWN"),
                    confidence=None,
                    timestamp=r["closed_at"],
                )
            )
        return result
    except Exception:
        return []
