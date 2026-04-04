export interface SystemHealth {
  state: string
  duration_in_state: number
  consecutive_errors: number
  trading_paused: boolean
  balance_usdt: number
  timestamp: string
}

export interface Balance {
  equity: number
  available: number
  wallet_balance: number
  coin: string
}

export interface OpenPosition {
  id: number
  symbol: string
  side: string
  qty: number
  entry_price: number
  stop_loss: number | null
  take_profit: number | null
  current_price: number | null
  opened_at: string
}

export interface TradeHistory {
  symbol: string
  side: string
  entry_price: number
  exit_price: number
  quantity: number
  net_pnl: number
  closed_at: string
}

export interface Signal {
  symbol: string
  decision: string
  confidence: number | null
  timestamp: string
}

export interface AnalyticsSummary {
  period_days: number
  total_trades: number
  win_rate: number
  net_pnl: number
  sharpe_ratio: number | null
  max_drawdown_pct: number
  profit_factor: number | null
}

export interface EquityCurve {
  points: number[]
  timestamps: string[]
}

export interface SymbolPnl {
  symbol: string
  trades: number
  wins: number
  net_pnl: number
  win_rate: number
}

export interface PnlHistoryItem {
  date: string
  realised_pnl: number
  trades_count: number
  balance_end: number
}

export interface MonthlyTarget {
  month: string
  target_pct: number
  current_pnl: number
  starting_balance: number
  target_pnl: number
  progress_pct: number
  days_elapsed: number
  days_in_month: number
  daily_target_pnl: number
  on_track: boolean
}

export interface WsSnapshot {
  timestamp: string
  system_state: string
  trading_paused: boolean
  balance_usdt: number
  positions: OpenPosition[]
}
