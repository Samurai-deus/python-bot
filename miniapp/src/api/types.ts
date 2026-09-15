export interface SystemHealth {
  state: string
  duration_in_state: number
  consecutive_errors: number
  trading_paused: boolean
  balance_usdt: number
  timestamp: string
  trading_mode: string
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

export interface WsSnapshot {
  timestamp: string
  system_state: string
  trading_paused: boolean
  /** null — сервер не успел посчитать баланс; позиции в снимке при этом верны. */
  balance_usdt: number | null
  positions: OpenPosition[]
}

export interface ResearchEvent {
  at: string | null
  kind: string
  detail: string
}

export interface ResearchPortfolio {
  status: 'waiting' | 'running' | 'halted'
  halted_reason: string | null
  started_at: string | null
  snapshot_at: string | null
  capital: number
  start_equity: number | null
  equity: number | null
  change: number | null
  drawdown: number
  gross: number
  positions: { symbol: string; side: 'LONG' | 'SHORT'; notional: number; weight: number }[]
  next_rebalance: string | null
  rebalances: { t: string | null; done_at: string | null; coins: number; orders: number; failed: number; runs?: number }[]
  events: ResearchEvent[]
}

export interface ResearchCarry {
  status: 'waiting' | 'running' | 'halted'
  halted_reason: string | null
  opened_at: string | null
  snapshot_at: string | null
  start_equity: number | null
  equity: number | null
  change: number | null
  funding: number
  fees: number
  drawdown: number
  mm_rate: number | null
  outside_share: number
  positions: { symbol: string; spot: number; short: number; price: number | null; notional: number; deviation: number | null }[]
  events: ResearchEvent[]
}

export interface ResearchRecorder {
  since: string | null
  events: Record<string, number>
  gaps: number
  size_mb: number
  mb_per_day: number | null
  last_message_age_s: number | null
}

export interface ResearchNews {
  items: number
  fresh: Record<string, number>
  signals: number
  score_rows: number
  blind: Record<string, number>
  spend_today: number | null
}

export interface ResearchExecutorMeta { title: string; risk: string; start: string; end: string; verdict: string; rebalance: string; criteria: string[]; notes: string[] }

export interface ResearchCalendarItem { date: string; text: string; days_left: number }

export interface ResearchProgram {
  portfolio: ResearchExecutorMeta
  btcalts: ResearchExecutorMeta
  carry: { title: string; notional: string; start: string; end: string; verdict: string; criteria: string[] }
  recorder: { title: string; symbols: string; hypotheses_from: string; cap_gb: number }
  news: { title: string; first_check: string; budget_usd: number; signal: string }
  watch: { title: string; start: string; first_check: string; every_weeks: number; stop: string; note: string }
}

export interface ResearchOverview {
  generated_at: string
  program: ResearchProgram
  portfolio: ResearchPortfolio | null
  btcalts: ResearchPortfolio | null
  calendar: ResearchCalendarItem[]
  carry: ResearchCarry | null
  recorder: ResearchRecorder | null
  news: ResearchNews
}
