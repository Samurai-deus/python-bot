"""
Pydantic v2 response schemas (схемы удалённых роутеров убраны 23.09.2026) for the FastAPI backend.
"""
from pydantic import BaseModel


class SystemHealthResponse(BaseModel):
    state: str
    duration_in_state: float
    consecutive_errors: int
    trading_paused: bool
    balance_usdt: float
    timestamp: str
    trading_mode: str = "DRY_RUN"


class BalanceResponse(BaseModel):
    equity: float
    available: float
    wallet_balance: float
    coin: str


class ConfidenceBucketResponse(BaseModel):
    label: str
    total: int
    wins: int
    losses: int
    win_rate: float
    expected_rate: float
    calibration_error: float


class SymbolAccuracyResponse(BaseModel):
    symbol: str
    total: int
    wins: int
    losses: int
    neutrals: int
    win_rate: float
    avg_favorable_pct: float
    avg_adverse_pct: float


class MonthlyTargetResponse(BaseModel):
    month: str
    target_pct: float
    current_pnl: float
    starting_balance: float
    target_pnl: float
    progress_pct: float
    days_elapsed: int
    days_in_month: int
    daily_target_pnl: float
    on_track: bool


class AccuracyReportResponse(BaseModel):
    period_days: int
    total_outcomes: int
    total_wins: int
    total_losses: int
    total_neutrals: int
    overall_win_rate: float
    long_win_rate: float
    long_total: int
    short_win_rate: float
    short_total: int
    by_confidence: list[ConfidenceBucketResponse]
    by_state_15m: dict[str, dict]
    mean_calibration_error: float
    top_symbols: list[SymbolAccuracyResponse]
    bottom_symbols: list[SymbolAccuracyResponse]
    avg_max_favorable_pct: float
    avg_max_adverse_pct: float
    generated_at: str
