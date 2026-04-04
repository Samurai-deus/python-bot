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

const SYMBOLS: readonly string[] = ['ALL', ...KNOWN_SYMBOLS]

function ConfidenceBar({ value }: { value: number | null }) {
  if (value === null) {
    return (
      <div>
        <div style={{ height: 3, borderRadius: 2, background: 'rgba(78,96,112,0.2)' }} />
        <p style={{ fontSize: 10, marginTop: 3, color: 'var(--text-dim)', fontFamily: 'monospace' }}>—</p>
      </div>
    )
  }
  const pct = Math.round(value * 100)
  const color  = value >= 0.7 ? 'var(--green)'  : value >= 0.5 ? 'var(--amber)'  : 'var(--red)'
  const shadow = value >= 0.7 ? 'rgba(0,255,157,0.55)' : value >= 0.5 ? 'rgba(255,170,0,0.55)' : 'rgba(255,68,102,0.55)'
  return (
    <div>
      <div style={{ height: 3, borderRadius: 2, background: 'rgba(78,96,112,0.18)', overflow: 'hidden' }}>
        <div style={{
          width: `${pct}%`,
          height: '100%',
          borderRadius: 2,
          background: color,
          boxShadow: `0 0 6px ${shadow}`,
          transition: 'width 0.5s ease',
        }} />
      </div>
      <p style={{ fontSize: 10, marginTop: 3, color, fontFamily: 'monospace', fontWeight: 700 }}>
        {pct}%
      </p>
    </div>
  )
}

export function Signals() {
  const [filter, setFilter] = useState('ALL')
  const { data: signals, isLoading, error } = useLatestSignals(50)

  const filtered = signals
    ? filter === 'ALL'
      ? signals
      : signals.filter(s => s.symbol === filter)
    : []

  return (
    <div className="grid-bg" style={{ padding: '16px 16px 0', minHeight: '100dvh' }}>

      {/* Header */}
      <div style={{ marginBottom: 16 }}>
        <p style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.35em', textTransform: 'uppercase', color: 'var(--cyan)', textShadow: '0 0 10px rgba(0,212,255,0.55)', marginBottom: 2 }}>
          REAL-TIME
        </p>
        <h1 style={{ fontSize: 20, fontWeight: 700, color: '#fff', margin: 0 }}>Signals</h1>
      </div>

      {/* Symbol filter */}
      <div style={{ position: 'relative', marginBottom: 16 }}>
        <select
          value={filter}
          onChange={e => setFilter(e.target.value)}
          style={{
            width: '100%',
            padding: '10px 36px 10px 14px',
            borderRadius: 12,
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            color: 'var(--text)',
            fontSize: 13,
            outline: 'none',
            appearance: 'none',
            WebkitAppearance: 'none',
            fontFamily: 'monospace',
          }}
        >
          {SYMBOLS.map(s => (
            <option key={s} value={s}>
              {s === 'ALL' ? 'All Symbols' : formatSymbol(s)}
            </option>
          ))}
        </select>
        <svg
          viewBox="0 0 10 6" fill="none" stroke="currentColor" strokeWidth="1.5"
          style={{
            position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)',
            width: 10, height: 10, pointerEvents: 'none', color: 'var(--cyan)',
          }}
        >
          <path d="M1 1l4 4 4-4" />
        </svg>
      </div>

      {isLoading && <LoadingSpinner />}
      {error && <ErrorBanner message={error.message} />}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, paddingBottom: 16 }}>
        {filtered.map((signal, i) => {
          const isBuy  = signal.decision === 'BUY'
          const isSell = signal.decision === 'SELL'
          const accentColor = isBuy ? 'rgba(0,255,157,0.12)' : isSell ? 'rgba(255,68,102,0.12)' : 'var(--border)'
          return (
            <div
              key={signal.id ?? `${signal.symbol}-${signal.timestamp}`}
              className="fade-up"
              style={{
                borderRadius: 16,
                padding: '14px 16px',
                background: 'var(--surface)',
                border: `1px solid ${accentColor}`,
                animationDelay: `${Math.min(i * 0.03, 0.3)}s`,
                position: 'relative',
                overflow: 'hidden',
              }}
            >
              {/* Side accent bar */}
              <div style={{
                position: 'absolute', left: 0, top: 0, bottom: 0, width: 3,
                background: isBuy ? 'var(--green)' : isSell ? 'var(--red)' : 'transparent',
                boxShadow: isBuy ? '0 0 8px rgba(0,255,157,0.5)' : isSell ? '0 0 8px rgba(255,68,102,0.5)' : 'none',
                borderRadius: '3px 0 0 3px',
              }} />

              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 10, paddingLeft: 8 }}>
                <div>
                  <span style={{ fontFamily: 'monospace', fontWeight: 700, fontSize: 14, color: '#fff' }}>
                    {formatSymbol(signal.symbol)}
                  </span>
                  <p style={{ fontSize: 10, marginTop: 2, color: 'var(--text-dim)', fontFamily: 'monospace' }}>
                    {formatDate(signal.timestamp)}
                  </p>
                </div>
                <Badge
                  label={signal.decision}
                  variant={isBuy ? 'buy' : isSell ? 'sell' : 'neutral'}
                />
              </div>

              <div style={{ paddingLeft: 8 }}>
                <ConfidenceBar value={signal.confidence} />
              </div>
            </div>
          )
        })}

        {!isLoading && filtered.length === 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', paddingTop: 60, gap: 12 }}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round"
              style={{ width: 40, height: 40, color: 'var(--text-dim)', opacity: 0.3 }}>
              <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
            </svg>
            <p style={{ fontSize: 11, letterSpacing: '0.25em', textTransform: 'uppercase', color: 'var(--text-dim)' }}>
              No signals
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
