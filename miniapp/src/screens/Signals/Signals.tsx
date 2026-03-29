import { useState } from 'react'
import { useLatestSignals } from '../../hooks/useSignals'
import { Badge } from '../../components/Badge'
import { LoadingSpinner } from '../../components/LoadingSpinner'
import { ErrorBanner } from '../../components/ErrorBanner'
import { formatDate, formatSymbol } from '../../lib/formatters'

// Must match SYMBOLS in config.py
const KNOWN_SYMBOLS = [
  'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT',
  'ADAUSDT', 'DOGEUSDT', 'AVAXUSDT', 'DOTUSDT', 'POLUSDT',
  'LINKUSDT', 'UNIUSDT', 'AAVEUSDT', 'ARBUSDT', 'OPUSDT',
  'SUIUSDT', 'APTUSDT', 'SHIB1000USDT', '1000PEPEUSDT',
  'ATOMUSDT', 'NEARUSDT', 'TONUSDT', 'INJUSDT', 'WLDUSDT',
  'TIAUSDT', 'RENDERUSDT', 'EIGENUSDT', 'JUPUSDT',
] as const

type KnownSymbol = typeof KNOWN_SYMBOLS[number]
const SYMBOLS: readonly string[] = ['ALL', ...KNOWN_SYMBOLS]

function isKnownSymbol(s: string): s is KnownSymbol {
  return KNOWN_SYMBOLS.includes(s as KnownSymbol)
}

function ConfidenceBar({ value }: { value: number | null }) {
  if (value === null) {
    return <div className="h-1.5 rounded-full bg-[var(--tg-hint)]/30 w-full" />
  }
  const pct = Math.round(value * 100)
  const color = value >= 0.7 ? 'bg-green-400' : value >= 0.5 ? 'bg-amber-400' : 'bg-red-400'
  return (
    <div className="space-y-0.5">
      <div className="h-1.5 rounded-full bg-[var(--tg-hint)]/20 w-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <p className="text-xs text-[var(--tg-hint)]">{pct}%</p>
    </div>
  )
}

export function Signals() {
  const [filter, setFilter] = useState('ALL')
  const { data: signals, isLoading, error } = useLatestSignals(50)

  const filtered = signals
    ? filter === 'ALL'
      ? signals
      : signals.filter(s => isKnownSymbol(s.symbol) && s.symbol === filter)
    : []

  return (
    <div className="px-4 pt-4">
      <h1 className="text-xl font-bold mb-4">Signals</h1>

      <select
        value={filter}
        onChange={e => setFilter(e.target.value)}
        className="w-full mb-4 px-3 py-2 bg-[var(--tg-secondary)] text-[var(--tg-text)] rounded-xl border-0 text-sm"
      >
        {SYMBOLS.map(s => (
          <option key={s} value={s}>{s === 'ALL' ? 'All Symbols' : formatSymbol(s)}</option>
        ))}
      </select>

      {isLoading && <LoadingSpinner />}
      {error && <ErrorBanner message={error.message} />}

      <div className="space-y-3">
        {filtered.map((signal, i) => (
          <div key={i} className="bg-[var(--tg-secondary)] rounded-2xl p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="font-semibold">{formatSymbol(signal.symbol)}</span>
              <Badge
                label={signal.decision}
                variant={
                  signal.decision === 'BUY' ? 'buy'
                  : signal.decision === 'SELL' ? 'sell'
                  : 'neutral'
                }
              />
            </div>
            <ConfidenceBar value={signal.confidence} />
            <p className="text-xs text-[var(--tg-hint)] mt-1">{formatDate(signal.timestamp)}</p>
          </div>
        ))}
        {!isLoading && filtered.length === 0 && (
          <p className="text-[var(--tg-hint)] text-center py-8">No signals</p>
        )}
      </div>
    </div>
  )
}
