import { formatDate } from './formatters'

/** Форматирование величин программы (docs/TRADER_PLAN.md): деньги, знак, доля капитала, даты. */

export const usd = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined ? '—' : `${v >= 0 ? '' : '−'}${Math.abs(v).toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits })} $`

export const signed = (v: number | null | undefined) => v === null || v === undefined ? '—' : `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(2)} $`

export const pctOf = (v: number | null | undefined, base: number) =>
  v === null || v === undefined || !base ? '' : ` (${v >= 0 ? '+' : '−'}${(Math.abs(v) / base * 100).toFixed(2)} %)`

export const day = (iso: string | null | undefined) => (iso ? formatDate(iso) : '—')

export const ruDate = (ymd: string) => {
  const [y, m, d] = ymd.split('-')
  return `${d}.${m}.${y}`
}

export const statusLabel: Record<string, { text: string; variant: 'buy' | 'sell' | 'neutral' }> = {
  running: { text: 'РАБОТАЕТ', variant: 'buy' },
  waiting: { text: 'ОЖИДАНИЕ', variant: 'neutral' },
  halted: { text: 'ОСТАНОВЛЕН', variant: 'sell' },
}
