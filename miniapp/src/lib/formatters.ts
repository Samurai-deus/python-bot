import { format, formatDistanceToNow } from 'date-fns'

export function formatUSDT(value: number): string {
  if (!isFinite(value)) return '$—'
  const abs = Math.abs(value)
  let maximumFractionDigits = 2
  if (abs > 0 && abs < 0.01) maximumFractionDigits = 6
  else if (abs > 0 && abs < 1) maximumFractionDigits = 4
  const formatted = abs.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits })
  return value < 0 ? `-$${formatted}` : `$${formatted}`
}

// Backend returns win_rate/drawdown as 0-100 (e.g. 75.5), confidence as 0-1
export function formatPct(value: number): string {
  return `${value.toFixed(2)}%`
}

export function formatPnl(value: number): string {
  const sign = value >= 0 ? '+' : ''
  return `${sign}${formatUSDT(value)}`
}

// Признак часового пояса в конце ISO-строки: 'Z' либо смещение ±HH:MM / ±HHMM / ±HH.
//
// Прежнее выражение /[Zz+\-]\d{0,4}$/ не распознавало формат с двоеточием, а бэкенд
// отдаёт именно его: datetime.now(UTC).isoformat() даёт "…+00:00". Проверка не
// срабатывала, к строке дописывалась 'Z', получалось "…+00:00Z" — невалидная дата.
// Дальше date-fns.format бросал RangeError, catch возвращал исходную строку, и в
// интерфейсе вместо даты стояло сырое "2026-09-09T10:00:00.123456+00:00". В графике
// эквити было хуже: там ошибка не ловилась, все точки получали time = NaN и
// схлопывались в одну (NaN — единственный ключ Map), то есть график был пуст.
//
// Экспортируется ради теста и чтобы Analytics не заводил вторую копию выражения:
// именно расхождение двух копий этой строки и делало баг незаметным при правке одной.
export const TZ_SUFFIX = /(?:Z|[+-]\d{2}:?(?:\d{2})?)$/i

export function parseUTC(iso: string): Date {
  return new Date(TZ_SUFFIX.test(iso) ? iso : iso + 'Z')
}

export function formatDate(iso: string): string {
  try { return format(parseUTC(iso), 'dd MMM, HH:mm') } catch { return iso }
}

export function formatRelative(iso: string): string {
  try { return formatDistanceToNow(parseUTC(iso), { addSuffix: true }) } catch { return iso }
}

export function formatQty(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`
  if (value >= 10_000) return `${(value / 1_000).toFixed(1)}K`
  if (value >= 100) return value.toFixed(1)
  if (value >= 1) return value.toFixed(2)
  return value.toFixed(4)
}

// Bybit uses non-standard symbol names for some tokens
const SYMBOL_DISPLAY: Record<string, string> = {
  'SHIB1000USDT': '1000SHIB/USDT',
  '1000PEPEUSDT': '1000PEPE/USDT',
}

export function formatSymbol(symbol: string): string {
  if (SYMBOL_DISPLAY[symbol]) return SYMBOL_DISPLAY[symbol]
  if (symbol.endsWith('USDT')) {
    return symbol.slice(0, -4) + '/USDT'
  }
  return symbol
}
