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
        from datetime import datetime, UTC, timedelta
        from journal import get_recent_signals

        since = datetime.now(UTC) - timedelta(days=30)
        rows = await run_sync(get_recent_signals, since)
        rows.sort(key=lambda r: r["timestamp"], reverse=True)
        result = []
        for r in rows[:limit]:
            direction = r.get("direction", "")
            if direction == "LONG":
                decision_label = "BUY"
            elif direction == "SHORT":
                decision_label = "SELL"
            else:
                decision_label = r.get("decision") or r.get("risk") or "UNKNOWN"
            result.append(
                SignalResponse(
                    symbol=r["symbol"],
                    decision=decision_label,
                    confidence=r.get("confidence"),
                    timestamp=r["timestamp"].isoformat(),
                )
            )
        return result
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
