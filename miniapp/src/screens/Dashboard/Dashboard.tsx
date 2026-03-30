import { useQuery } from '@tanstack/react-query'
import { useSystemStore } from '../../store/useSystemStore'
import { useAnalyticsSummary } from '../../hooks/useAnalytics'
import { fetchHealth } from '../../api/endpoints'
import { Badge } from '../../components/Badge'
import { LoadingSpinner } from '../../components/LoadingSpinner'
import { formatUSDT, formatPnl, formatPct, formatRelative } from '../../lib/formatters'

function stateVariant(state: string): 'running' | 'degraded' | 'halt' | 'recovery' | 'neutral' {
  if (state === 'RUNNING') return 'running'
  if (state === 'DEGRADED' || state === 'RECOVERY') return 'degraded'
  if (state === 'SAFE_HALT' || state === 'FATAL') return 'halt'
  return 'neutral'
}

const WS_COLORS: Record<string, string> = {
  connected:    'var(--green)',
  connecting:   'var(--amber)',
  reconnecting: 'var(--amber)',
  disconnected: 'var(--red)',
}
const WS_LABELS: Record<string, string> = {
  connected:    'LIVE',
  connecting:   'CONNECTING',
  reconnecting: 'RECONNECT',
  disconnected: 'OFFLINE',
}

export function Dashboard() {
  const { snapshot, wsStatus, lastSnapshotAt } = useSystemStore()
  const { data: summary, isLoading } = useAnalyticsSummary(1)

  const { data: healthFallback } = useQuery({
    queryKey: ['health-fallback'],
    queryFn: fetchHealth,
    enabled: wsStatus === 'disconnected',
    refetchInterval: wsStatus === 'disconnected' ? 30_000 : false,
  })

  const balance = snapshot?.balance_usdt ?? healthFallback?.balance_usdt
  const systemState = snapshot?.system_state ?? healthFallback?.state
  const tradingPaused = snapshot?.trading_paused ?? healthFallback?.trading_paused ?? false
  const wsColor = WS_COLORS[wsStatus] ?? 'var(--text-dim)'

  return (
    <div className="px-4 pt-4 pb-2 space-y-3 grid-bg" style={{ minHeight: '100dvh' }}>

      {/* ── Header ────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
        <div>
          <p style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.35em', textTransform: 'uppercase', color: 'var(--cyan)', textShadow: '0 0 10px rgba(0,212,255,0.55)', marginBottom: 2 }}>
            SIGNABOT
          </p>
          <h1 style={{ fontSize: 20, fontWeight: 700, color: '#fff', margin: 0, lineHeight: 1.1 }}>
            Trading Dashboard
          </h1>
        </div>
        {/* WS status badge */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
          <span style={{
            width: 7, height: 7, borderRadius: '50%',
            background: wsColor,
            boxShadow: `0 0 6px ${wsColor}`,
            animation: wsStatus !== 'connected' ? 'pulse-glow 1s ease-in-out infinite' : undefined,
            display: 'inline-block',
          }} />
          <span style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.14em', textTransform: 'uppercase', color: wsColor }}>
            {WS_LABELS[wsStatus] ?? wsStatus}
          </span>
        </div>
      </div>

      {/* ── Trading paused warning ─────────────── */}
      {tradingPaused && (
        <div style={{
          padding: '10px 14px',
          borderRadius: 12,
          background: 'rgba(255,68,102,0.07)',
          border: '1px solid rgba(255,68,102,0.35)',
          color: '#ff4466',
          fontSize: 12,
          fontWeight: 700,
          letterSpacing: '0.1em',
          textTransform: 'uppercase',
          boxShadow: '0 0 20px rgba(255,68,102,0.08)',
        }}>
          ⚠ Trading Suspended
        </div>
      )}

      {/* ── Balance card ──────────────────────── */}
      <div
        className="fade-up"
        style={{
          borderRadius: 20,
          padding: '20px',
          background: 'linear-gradient(135deg, rgba(0,212,255,0.07) 0%, rgba(13,22,38,0.9) 55%, rgba(13,22,38,0.95) 100%)',
          border: '1px solid rgba(0,212,255,0.18)',
          boxShadow: '0 0 40px rgba(0,212,255,0.04), inset 0 0 40px rgba(0,212,255,0.03)',
          position: 'relative',
          overflow: 'hidden',
        }}
      >
        {/* Corner glow */}
        <div style={{
          position: 'absolute', top: 0, right: 0,
          width: 120, height: 120, pointerEvents: 'none',
          background: 'radial-gradient(circle at top right, rgba(0,212,255,0.08), transparent 70%)',
        }} />
        {/* Horizontal scan line */}
        <div style={{
          position: 'absolute', left: 0, right: 0,
          height: 1,
          background: 'linear-gradient(90deg, transparent, rgba(0,212,255,0.15), transparent)',
          top: '40%',
        }} />

        <p style={{ fontSize: 9, letterSpacing: '0.3em', textTransform: 'uppercase', color: 'var(--text-dim)', marginBottom: 6 }}>
          Balance · USDT
        </p>
        <p style={{
          fontSize: 38,
          fontWeight: 700,
          letterSpacing: '-0.02em',
          color: balance != null ? '#fff' : 'var(--text-dim)',
          fontVariantNumeric: 'tabular-nums',
          lineHeight: 1,
          marginBottom: 12,
          fontFamily: 'monospace',
        }}>
          {balance != null ? formatUSDT(balance) : '—·——'}
        </p>
        {systemState && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Badge label={systemState} variant={stateVariant(systemState)} />
            {lastSnapshotAt && wsStatus !== 'connected' && (
              <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>
                {formatRelative(lastSnapshotAt.toISOString())}
              </span>
            )}
          </div>
        )}
      </div>

      {/* ── Stats grid ───────────────────────── */}
      {isLoading ? (
        <LoadingSpinner />
      ) : summary ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10 }}>
          <StatTile
            label="Positions"
            value={String(snapshot?.positions.length ?? 0)}
            color="var(--cyan)"
          />
          <StatTile
            label="Win Rate"
            value={formatPct(summary.win_rate)}
            color={summary.win_rate >= 0.5 ? 'var(--green)' : 'var(--red)'}
          />
          <StatTile
            label="P&L Today"
            value={formatPnl(summary.net_pnl)}
            color={summary.net_pnl >= 0 ? 'var(--green)' : 'var(--red)'}
          />
        </div>
      ) : null}

      {/* ── Open positions preview ────────────── */}
      {snapshot && snapshot.positions.length > 0 && (
        <div style={{
          borderRadius: 16,
          padding: '14px 16px',
          background: 'var(--surface)',
          border: '1px solid var(--border)',
        }}>
          <p style={{ fontSize: 9, letterSpacing: '0.28em', textTransform: 'uppercase', color: 'var(--text-dim)', marginBottom: 10 }}>
            Open Positions · {snapshot.positions.length}
          </p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {snapshot.positions.slice(0, 3).map((p) => (
              <div
                key={p.id}
                style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  paddingBottom: 10,
                  borderBottom: '1px solid var(--border)',
                }}
              >
                <span style={{ fontFamily: 'monospace', fontWeight: 600, fontSize: 13 }}>
                  {p.symbol.replace('USDT', '')}<span style={{ color: 'var(--text-dim)', fontWeight: 400 }}>/USDT</span>
                </span>
                <Badge label={p.side} variant={p.side === 'BUY' || p.side === 'LONG' ? 'buy' : 'sell'} />
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function StatTile({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{
      borderRadius: 14,
      padding: '12px 10px',
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      position: 'relative',
      overflow: 'hidden',
    }}>
      {/* Bottom glow accent */}
      <div style={{
        position: 'absolute', bottom: 0, left: 0, right: 0, height: 1,
        background: `linear-gradient(90deg, transparent, ${color}30, transparent)`,
      }} />
      <p style={{ fontSize: 9, letterSpacing: '0.22em', textTransform: 'uppercase', color: 'var(--text-dim)', marginBottom: 6 }}>
        {label}
      </p>
      <p style={{
        fontSize: 15,
        fontWeight: 700,
        fontFamily: 'monospace',
        color,
        textShadow: `0 0 10px ${color}70`,
        letterSpacing: '-0.01em',
      }}>
        {value}
      </p>
    </div>
  )
}
