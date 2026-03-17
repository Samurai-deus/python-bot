import { format, formatDistanceToNow } from 'date-fns'

export function formatUSDT(value: number): string {
  return `$${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export function formatPct(value: number): string {
  return `${(value * 100).toFixed(2)}%`
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
