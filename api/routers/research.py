"""Исследовательская программа для мини-аппа: один снимок всего (см. api.research_data)."""
import asyncio

from fastapi import APIRouter, Depends

from api.deps import run_sync, verify_auth

router = APIRouter(prefix="/api/research", tags=["research"])


@router.get("/overview")
async def get_overview(_: dict = Depends(verify_auth)):
    from api.research_data import overview
    return await asyncio.wait_for(run_sync(overview), timeout=10.0)
