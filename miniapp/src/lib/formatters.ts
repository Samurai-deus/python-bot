import { format, formatDistanceToNow } from 'date-fns'

export function formatUSDT(value: number): string {
  const abs = Math.abs(value)
  let maximumFractionDigits = 2
  if (abs > 0 && abs < 0.01) maximumFractionDigits = 6
  else if (abs > 0 && abs < 1) maximumFractionDigits = 4
  return `$${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits })}`
}

// Backend returns win_rate/drawdown as 0-100 (e.g. 75.5), confidence as 0-1
export function formatPct(value: number): string {
  return `${value.toFixed(2)}%`
}

// For 0-1 values (e.g. confidence)
export function formatPct01(value: number): string {
  return `${(value * 100).toFixed(0)}%`
}

export function formatPnl(value: number): string {
  const sign = value >= 0 ? '+' : ''
  return `${sign}${formatUSDT(value)}`
}

export function formatDate(iso: string): string {
  try { return format(new Date(iso), 'dd MMM, HH:mm') } catch { return iso }
}

export function formatRelative(iso: string): string {
  try { return formatDistanceToNow(new Date(iso), { addSuffix: true }) } catch { return iso }
}

export function formatSymbol(symbol: string): string {
  if (symbol.endsWith('USDT')) {
    return symbol.slice(0, -4) + '/USDT'
  }
  return symbol
}
