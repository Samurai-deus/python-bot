"""
Pydantic v2 response schemas for the FastAPI backend.
"""
from typing import List, Optional
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
