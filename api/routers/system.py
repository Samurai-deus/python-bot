"""
System health and balance endpoints.
"""
from datetime import datetime, UTC
from fastapi import APIRouter, Depends, HTTPException

from api.deps import run_sync, verify_auth
from api.models import SystemHealthResponse, BalanceResponse

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health", response_model=SystemHealthResponse)
async def get_health(_: dict = Depends(verify_auth)):
    from system_state_machine import get_state_machine
    from database import get_current_balance_from_db

    sm = get_state_machine()
    info = sm.get_state_info()
    balance = await run_sync(get_current_balance_from_db)

    return SystemHealthResponse(
        state=info["state"],
        duration_in_state=info["duration_in_state"] or 0.0,
        consecutive_errors=info["consecutive_errors"],
        trading_paused=sm.trading_paused,
        balance_usdt=balance,
        timestamp=datetime.now(UTC).isoformat(),
    )


@router.get("/balance", response_model=BalanceResponse)
async def get_balance(_: dict = Depends(verify_auth)):
    from exchange.bybit_client import get_bybit_client

    client = get_bybit_client()
    try:
        b = await run_sync(client.get_wallet_balance, "USDT")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Bybit unavailable: {exc}") from exc

    return BalanceResponse(
        equity=b.total_equity,
        available=b.available_balance,
        wallet_balance=b.wallet_balance,
        coin=b.coin,
    )
