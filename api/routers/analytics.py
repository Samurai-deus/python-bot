"""
Analytics endpoints.
"""
from typing import List
from fastapi import APIRouter, Depends, Query

from api.deps import run_sync, verify_auth
from api.models import (
    AnalyticsSummaryResponse,
    EquityCurveResponse,
    SymbolPnlItem,
    PnlHistoryItem,
)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/summary", response_model=AnalyticsSummaryResponse)
async def get_summary(
    days: int = Query(default=30, ge=1, le=365),
    _: dict = Depends(verify_auth),
):
    from analytics.performance_tracker import PerformanceTracker

    tracker = PerformanceTracker()
    report = await run_sync(tracker.get_full_report, days)

    if report is None:
        return AnalyticsSummaryResponse(
            period_days=days,
            total_trades=0,
            win_rate=0.0,
            net_pnl=0.0,
            sharpe_ratio=None,
            max_drawdown_pct=0.0,
            profit_factor=None,
        )

    return AnalyticsSummaryResponse(
        period_days=days,
        total_trades=report.total_trades,
        win_rate=report.win_rate,
        net_pnl=report.total_net_pnl,
        sharpe_ratio=report.sharpe_ratio,
        max_drawdown_pct=report.max_drawdown_pct,
        profit_factor=report.profit_factor,
    )


@router.get("/equity-curve", response_model=EquityCurveResponse)
async def get_equity_curve(
    days: int = Query(default=30, ge=1, le=365),
    _: dict = Depends(verify_auth),
):
    from database import get_equity_curve_points

    rows = await run_sync(get_equity_curve_points, days)
    points = [r["balance"] for r in rows]
    timestamps = [r["timestamp"] for r in rows]
    return EquityCurveResponse(points=points, timestamps=timestamps)


@router.get("/by-symbol", response_model=List[SymbolPnlItem])
async def get_by_symbol(
    days: int = Query(default=30, ge=1, le=365),
    _: dict = Depends(verify_auth),
):
    from analytics.performance_tracker import PerformanceTracker

    tracker = PerformanceTracker()
    by_sym = await run_sync(tracker.get_pnl_by_symbol, days)

    result = []
    for symbol, sym_report in by_sym.items():
        result.append(
            SymbolPnlItem(
                symbol=symbol,
                trades=sym_report.trades,
                wins=sym_report.wins,
                net_pnl=sym_report.net_pnl,
                win_rate=sym_report.win_rate,
            )
        )
    return result


@router.get("/pnl-history", response_model=List[PnlHistoryItem])
async def get_pnl_history(
    days: int = Query(default=30, ge=1, le=365),
    _: dict = Depends(verify_auth),
):
    from database import get_pnl_history

    rows = await run_sync(get_pnl_history, days)
    return [
        PnlHistoryItem(
            date=r["date"],
            realised_pnl=r["realised_pnl"],
            trades_count=r["trades_count"],
            balance_end=r["balance_end"],
        )
        for r in rows
    ]
