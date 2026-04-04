"""
Analytics endpoints.
"""
import asyncio
import logging
from typing import List
from fastapi import APIRouter, Depends, Query

from api.deps import run_sync, verify_auth
from api.models import (
    AnalyticsSummaryResponse,
    EquityCurveResponse,
    SymbolPnlItem,
    PnlHistoryItem,
    MonthlyTargetResponse,
    AccuracyReportResponse,
    ConfidenceBucketResponse,
    SymbolAccuracyResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/summary", response_model=AnalyticsSummaryResponse)
async def get_summary(
    days: int = Query(default=30, ge=1, le=365),
    _: dict = Depends(verify_auth),
):
    from analytics.performance_tracker import PerformanceTracker

    tracker = PerformanceTracker()
    report = await asyncio.wait_for(run_sync(tracker.get_full_report, days), timeout=5.0)

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

    rows = await asyncio.wait_for(run_sync(get_equity_curve_points, days), timeout=5.0)
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
    by_sym = await asyncio.wait_for(run_sync(tracker.get_pnl_by_symbol, days), timeout=5.0)

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

    rows = await asyncio.wait_for(run_sync(get_pnl_history, days), timeout=5.0)
    return [
        PnlHistoryItem(
            date=r["date"],
            realised_pnl=r["realised_pnl"],
            trades_count=r["trades_count"],
            balance_end=r["balance_end"],
        )
        for r in rows
    ]


@router.get("/monthly-target", response_model=MonthlyTargetResponse)
async def get_monthly_target(
    _: dict = Depends(verify_auth),
):
    from database import get_monthly_target_data

    data = await asyncio.wait_for(run_sync(get_monthly_target_data), timeout=5.0)
    return MonthlyTargetResponse(**data)


@router.get("/accuracy", response_model=AccuracyReportResponse)
async def get_accuracy_report(
    days: int = Query(default=30, ge=1, le=90),
    _: dict = Depends(verify_auth),
):
    """
    Отчёт точности предсказаний бота на основе исходов сигналов.

    Требует накопленных данных в signal_outcomes (заполняется outcome_tracker_loop).
    Возвращает 404 если данных ещё недостаточно (< 3 размеченных сигнала).
    """
    from fastapi import HTTPException
    from brains.accuracy_analyzer import analyze_accuracy

    try:
        report = await asyncio.wait_for(
            run_sync(analyze_accuracy, days), timeout=5.0
        )
    except Exception as exc:
        logger.error("Failed to get accuracy report: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    if report is None:
        raise HTTPException(
            status_code=404,
            detail="Not enough outcome data yet. OutcomeTracker needs more time to accumulate results.",
        )

    return AccuracyReportResponse(
        period_days=report.period_days,
        total_outcomes=report.total_outcomes,
        total_wins=report.total_wins,
        total_losses=report.total_losses,
        total_neutrals=report.total_neutrals,
        overall_win_rate=report.overall_win_rate,
        long_win_rate=report.long_win_rate,
        long_total=report.long_total,
        short_win_rate=report.short_win_rate,
        short_total=report.short_total,
        by_confidence=[
            ConfidenceBucketResponse(
                label=b.label,
                total=b.total,
                wins=b.wins,
                losses=b.losses,
                win_rate=b.win_rate,
                expected_rate=b.expected_rate,
                calibration_error=b.calibration_error,
            )
            for b in report.by_confidence
        ],
        by_state_15m=report.by_state_15m,
        mean_calibration_error=report.mean_calibration_error,
        top_symbols=[
            SymbolAccuracyResponse(
                symbol=s.symbol,
                total=s.total,
                wins=s.wins,
                losses=s.losses,
                neutrals=s.neutrals,
                win_rate=s.win_rate,
                avg_favorable_pct=s.avg_favorable_pct,
                avg_adverse_pct=s.avg_adverse_pct,
            )
            for s in report.top_symbols
        ],
        bottom_symbols=[
            SymbolAccuracyResponse(
                symbol=s.symbol,
                total=s.total,
                wins=s.wins,
                losses=s.losses,
                neutrals=s.neutrals,
                win_rate=s.win_rate,
                avg_favorable_pct=s.avg_favorable_pct,
                avg_adverse_pct=s.avg_adverse_pct,
            )
            for s in report.bottom_symbols
        ],
        avg_max_favorable_pct=report.avg_max_favorable_pct,
        avg_max_adverse_pct=report.avg_max_adverse_pct,
        generated_at=report.generated_at.isoformat(),
    )
