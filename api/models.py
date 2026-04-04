"""
Pydantic v2 response schemas for the FastAPI backend.
"""
from typing import Dict, List, Optional, Union
from pydantic import BaseModel


class SystemHealthResponse(BaseModel):
    state: str
    duration_in_state: float
    consecutive_errors: int
    trading_paused: bool
    balance_usdt: float
    timestamp: str


class BalanceResponse(BaseModel):
    equity: float
    available: float
    wallet_balance: float
    coin: str


class PositionResponse(BaseModel):
    id: int
    symbol: str
    side: str
    qty: float
    entry_price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    current_price: Optional[float] = None
    opened_at: str


class TradeHistoryItem(BaseModel):
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    net_pnl: float
    closed_at: str


class SignalResponse(BaseModel):
    symbol: str
    decision: str
    confidence: Optional[float]
    timestamp: str


class AnalyticsSummaryResponse(BaseModel):
    period_days: int
    total_trades: int
    win_rate: float
    net_pnl: float
    sharpe_ratio: Optional[float]
    max_drawdown_pct: float
    profit_factor: Optional[float]


class EquityCurveResponse(BaseModel):
    points: List[float]
    timestamps: List[str]


class SymbolPnlItem(BaseModel):
    symbol: str
    trades: int
    wins: int
    net_pnl: float
    win_rate: float


class PnlHistoryItem(BaseModel):
    date: str
    realised_pnl: float
    trades_count: int
    balance_end: float


class SettingItem(BaseModel):
    key: str
    value: Union[str, float, int, bool]
    data_type: str
    updated_at: str


class SettingsResponse(BaseModel):
    settings: List[SettingItem]
    requires_restart: List[str]


class UpdateSettingsRequest(BaseModel):
    risk_percent: Optional[float] = None        # 0.5–5.0
    min_position_size: Optional[float] = None   # $10–$500
    max_position_size: Optional[float] = None   # $100–$5000
    bot_interval: Optional[int] = None          # 60–600s
    trading_mode: Optional[str] = None          # DRY_RUN | PAPER_TRADING
    symbols_enabled: Optional[List[str]] = None


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
    by_confidence: List[ConfidenceBucketResponse]
    by_state_15m: Dict[str, dict]
    mean_calibration_error: float
    top_symbols: List[SymbolAccuracyResponse]
    bottom_symbols: List[SymbolAccuracyResponse]
    avg_max_favorable_pct: float
    avg_max_adverse_pct: float
    generated_at: str
