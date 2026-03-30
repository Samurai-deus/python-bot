import { useState } from 'react'
import { useSystemStore } from '../../store/useSystemStore'
import { usePositionHistory } from '../../hooks/usePositions'
import { Badge } from '../../components/Badge'
import { LoadingSpinner } from '../../components/LoadingSpinner'
import { ErrorBanner } from '../../components/ErrorBanner'
import { formatUSDT, formatPnl, formatDate, formatSymbol } from '../../lib/formatters'
import type { OpenPosition, TradeHistory } from '../../api/types'

export function Positions() {
  const [tab, setTab] = useState<'open' | 'history'>('open')
  const { snapshot } = useSystemStore()
  const { data: history, isLoading, error } = usePositionHistory(30)

  return (
    <div className="grid-bg" style={{ padding: '16px 16px 0', minHeight: '100dvh' }}>

      {/* Header */}
      <div style={{ marginBottom: 16 }}>
        <p style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.35em', textTransform: 'uppercase', color: 'var(--cyan)', textShadow: '0 0 10px rgba(0,212,255,0.55)', marginBottom: 2 }}>
          PORTFOLIO
        </p>
        <h1 style={{ fontSize: 20, fontWeight: 700, color: '#fff', margin: 0 }}>Positions</h1>
      </div>

      {/* Tab switcher */}
      <div style={{
        display: 'flex',
        marginBottom: 16,
        padding: 3,
        borderRadius: 14,
        background: 'rgba(10,17,32,0.7)',
        border: '1px solid var(--border)',
        gap: 3,
      }}>
        {(['open', 'history'] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            style={{
              flex: 1,
              padding: '8px 0',
              borderRadius: 11,
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: '0.12em',
              textTransform: 'uppercase',
              border: tab === t ? '1px solid rgba(0,212,255,0.22)' : '1px solid transparent',
              background: tab === t ? 'rgba(0,212,255,0.1)' : 'transparent',
              color: tab === t ? 'var(--cyan)' : 'var(--text-dim)',
              boxShadow: tab === t ? '0 0 14px rgba(0,212,255,0.1)' : 'none',
              textShadow: tab === t ? '0 0 8px rgba(0,212,255,0.45)' : 'none',
              cursor: 'pointer',
              transition: 'all 0.2s',
            }}
          >
            {t === 'open' ? 'Open' : 'History'}
          </button>
        ))}
      </div>

      {tab === 'open' ? (
        <OpenPositionsList positions={snapshot?.positions ?? []} />
      ) : (
        <>
          {isLoading && <LoadingSpinner />}
          {error && <ErrorBanner message={error.message} />}
          {history && <HistoryList items={history} />}
        </>
      )}
    </div>
  )
}

function OpenPositionsList({ positions }: { positions: OpenPosition[] }) {
  if (positions.length === 0) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', paddingTop: 60, gap: 12 }}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round"
          style={{ width: 40, height: 40, color: 'var(--text-dim)', opacity: 0.3 }}>
          <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" />
          <polyline points="16 7 22 7 22 13" />
        </svg>
        <p style={{ fontSize: 11, letterSpacing: '0.25em', textTransform: 'uppercase', color: 'var(--text-dim)' }}>No open positions</p>
      </div>
    )
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, paddingBottom: 16 }}>
      {positions.map((p) => (
        <div
          key={p.id}
          className="fade-up"
          style={{
            borderRadius: 16,
            padding: '14px 16px',
            background: 'var(--surface)',
            border: `1px solid ${p.side === 'BUY' ? 'rgba(0,255,157,0.14)' : 'rgba(255,68,102,0.14)'}`,
            position: 'relative',
            overflow: 'hidden',
          }}
        >
          {/* Side accent */}
          <div style={{
            position: 'absolute', left: 0, top: 0, bottom: 0, width: 3,
            background: p.side === 'BUY' ? 'var(--green)' : 'var(--red)',
            boxShadow: p.side === 'BUY' ? '0 0 8px rgba(0,255,157,0.5)' : '0 0 8px rgba(255,68,102,0.5)',
            borderRadius: '3px 0 0 3px',
          }} />

          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 12, paddingLeft: 8 }}>
            <div>
              <span style={{ fontFamily: 'monospace', fontWeight: 700, fontSize: 14, color: '#fff' }}>
                {formatSymbol(p.symbol)}
              </span>
              <p style={{ fontSize: 10, marginTop: 2, color: 'var(--text-dim)', fontFamily: 'monospace' }}>
                {formatDate(p.opened_at)}
              </p>
            </div>
            <Badge label={p.side} variant={p.side === 'BUY' ? 'buy' : 'sell'} />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 16px', paddingLeft: 8 }}>
            <InfoRow label="Entry" value={formatUSDT(p.entry_price)} />
            <InfoRow label="Qty" value={String(p.qty)} />
            <InfoRow label="Stop Loss" value={p.stop_loss ? formatUSDT(p.stop_loss) : '—'} accent="var(--red)" />
            <InfoRow label="Take Profit" value={p.take_profit ? formatUSDT(p.take_profit) : '—'} accent="var(--green)" />
          </div>
        </div>
      ))}
    </div>
  )
}

function HistoryList({ items }: { items: TradeHistory[] }) {
  if (items.length === 0) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', paddingTop: 60, gap: 12 }}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round"
          style={{ width: 40, height: 40, color: 'var(--text-dim)', opacity: 0.3 }}>
          <path d="M12 20V10M18 20V4M6 20v-4" />
        </svg>
        <p style={{ fontSize: 11, letterSpacing: '0.25em', textTransform: 'uppercase', color: 'var(--text-dim)' }}>No history</p>
      </div>
    )
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, paddingBottom: 16 }}>
      {items.map((t, i) => (
        <div
          key={i}
          className="fade-up"
          style={{
            borderRadius: 16,
            padding: '14px 16px',
            background: 'var(--surface)',
            border: `1px solid ${t.net_pnl >= 0 ? 'rgba(0,255,157,0.12)' : 'rgba(255,68,102,0.12)'}`,
            position: 'relative',
            overflow: 'hidden',
            animationDelay: `${Math.min(i * 0.03, 0.3)}s`,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 12 }}>
            <div>
              <span style={{ fontFamily: 'monospace', fontWeight: 700, fontSize: 14, color: '#fff' }}>
                {formatSymbol(t.symbol)}
              </span>
              <p style={{ fontSize: 10, marginTop: 2, color: 'var(--text-dim)', fontFamily: 'monospace' }}>
                {formatDate(t.closed_at)}
              </p>
            </div>
            <div style={{ textAlign: 'right', display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'flex-end' }}>
              <p style={{
                fontFamily: 'monospace',
                fontWeight: 700,
                fontSize: 14,
                color: t.net_pnl >= 0 ? 'var(--green)' : 'var(--red)',
                textShadow: t.net_pnl >= 0 ? '0 0 8px rgba(0,255,157,0.4)' : '0 0 8px rgba(255,68,102,0.4)',
                letterSpacing: '-0.01em',
              }}>
                {formatPnl(t.net_pnl)}
              </p>
              <Badge label={t.side} variant={t.side === 'BUY' ? 'buy' : 'sell'} />
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 16px' }}>
            <InfoRow label="Entry" value={formatUSDT(t.entry_price)} />
            <InfoRow label="Exit" value={formatUSDT(t.exit_price)} />
            <InfoRow label="Qty" value={String(t.quantity)} />
          </div>
        </div>
      ))}
    </div>
  )
}

function InfoRow({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div>
      <p style={{ fontSize: 9, letterSpacing: '0.2em', textTransform: 'uppercase', color: 'var(--text-dim)', marginBottom: 3 }}>
        {label}
      </p>
      <p style={{ fontSize: 12, fontFamily: 'monospace', fontWeight: 600, color: accent ?? 'var(--text)' }}>
        {value}
      </p>
    </div>
  )
}
