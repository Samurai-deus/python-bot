"""
Signal endpoints.
"""
import asyncio
import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import run_sync, verify_auth
from api.models import SignalResponse

logger = logging.getLogger(__name__)

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
        rows = await asyncio.wait_for(run_sync(get_recent_signals, since), timeout=5.0)
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
    except Exception as exc:
        logger.error("Failed to get latest signals: %s: %s", type(exc).__name__, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.get("/history", response_model=List[SignalResponse])
async def get_signal_history(
    symbol: str = Query(default="", description="Filter by symbol"),
    days: int = Query(default=7, ge=1, le=90),
    _: dict = Depends(verify_auth),
):
    from database import get_closed_trades

    try:
        rows = await asyncio.wait_for(run_sync(get_closed_trades, days), timeout=5.0)
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
    except Exception as exc:
        logger.error("Failed to get signal history: %s: %s", type(exc).__name__, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from exc
