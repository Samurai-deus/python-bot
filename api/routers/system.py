"""
System health endpoint.

Баланс кошелька (/api/system/balance) удалён 29.09.2026 (аудит, пакет 8): ради него API держал ключи общего
демо-счёта бота и И14, а мини-апп его не вызывал. Баланс бота — в /api/system/health (capital, без ключей
при общем счёте).
"""
import logging
from datetime import datetime, UTC
from fastapi import APIRouter, Depends

from api.deps import run_sync, verify_auth
from api.models import SystemHealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health", response_model=SystemHealthResponse)
async def get_health(_: dict = Depends(verify_auth)):
    from system_state_machine import get_state_machine
    from capital import get_current_balance
    from trading_mode import get_trading_mode

    sm = get_state_machine()
    info = sm.get_state_info()
    # Баланс — из capital: он знает режим (кошелёк в TESTNET/LIVE, бумажный счёт
    # иначе) и стартовый баланс из config. Раньше здесь вызывалась функция базы
    # без аргумента, и её значение по умолчанию — 10 000 — показывалось в Mini App
    # при бумажном счёте в 100 $.
    balance = await run_sync(get_current_balance)

    return SystemHealthResponse(
        state=info["state"],
        duration_in_state=info["duration_in_state"] or 0.0,
        consecutive_errors=info["consecutive_errors"],
        trading_paused=sm.trading_paused,
        balance_usdt=balance,
        timestamp=datetime.now(UTC).isoformat(),
        trading_mode=get_trading_mode().value,
    )
