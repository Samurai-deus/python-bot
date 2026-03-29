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

export function Dashboard() {
  const { snapshot, wsStatus, lastSnapshotAt } = useSystemStore()
  const { data: summary, isLoading } = useAnalyticsSummary(1)

  // HTTP fallback: poll /api/system/health when WS is fully disconnected
  const { data: healthFallback } = useQuery({
    queryKey: ['health-fallback'],
    queryFn: fetchHealth,
    enabled: wsStatus === 'disconnected',
    refetchInterval: wsStatus === 'disconnected' ? 30_000 : false,
  })

  const balance = snapshot?.balance_usdt ?? healthFallback?.balance_usdt
  const systemState = snapshot?.system_state ?? healthFallback?.state
  const tradingPaused = snapshot?.trading_paused ?? healthFallback?.trading_paused ?? false

  return (
    <div className="px-4 pt-4 space-y-4">
      <h1 className="text-xl font-bold text-[var(--tg-text)]">Signal Bot</h1>

      {/* WS status */}
      {wsStatus !== 'connected' && (
        <div className="flex items-center gap-2 text-sm text-[var(--tg-hint)]">
          <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
          {wsStatus === 'connecting' ? 'Connecting...'
            : wsStatus === 'reconnecting' ? 'Reconnecting...'
            : lastSnapshotAt
              ? `Disconnected · data from ${formatRelative(lastSnapshotAt.toISOString())}`
              : 'Disconnected'}
        </div>
      )}

      {/* Trading paused warning */}
      {tradingPaused && (
        <div className="p-3 bg-red-900/30 border border-red-700 rounded-xl text-red-300 text-sm">
          Trading is PAUSED
        </div>
      )}

      {/* Balance card */}
      <div className="bg-[var(--tg-secondary)] rounded-2xl p-4 space-y-1">
        <p className="text-sm text-[var(--tg-hint)]">Balance</p>
        <p className="text-3xl font-bold">
          {balance != null ? formatUSDT(balance) : '—'}
        </p>
        {systemState && (
          <div className="flex items-center gap-2 pt-1">
            <Badge label={systemState} variant={stateVariant(systemState)} />
          </div>
        )}
      </div>

      {/* Quick stats */}
      {isLoading ? (
        <LoadingSpinner />
      ) : summary ? (
        <div className="grid grid-cols-3 gap-3">
          <StatTile label="Positions" value={String(snapshot?.positions.length ?? 0)} />
          <StatTile label="Win Rate" value={formatPct(summary.win_rate)} />
          <StatTile
            label="P&L Today"
            value={formatPnl(summary.net_pnl)}
            positive={summary.net_pnl >= 0}
          />
        </div>
      ) : null}

      {/* Open positions preview */}
      {snapshot && snapshot.positions.length > 0 && (
        <div className="bg-[var(--tg-secondary)] rounded-2xl p-4">
          <p className="text-sm text-[var(--tg-hint)] mb-3">Open Positions ({snapshot.positions.length})</p>
          <div className="space-y-2">
            {snapshot.positions.slice(0, 3).map((p) => (
              <div key={p.id} className="flex items-center justify-between">
                <span className="font-medium text-sm">{p.symbol}</span>
                <Badge label={p.side} variant={p.side === 'BUY' ? 'buy' : 'sell'} />
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function StatTile({ label, value, positive }: { label: string; value: string; positive?: boolean }) {
  return (
    <div className="bg-[var(--tg-secondary)] rounded-xl p-3 text-center">
      <p className="text-xs text-[var(--tg-hint)] mb-1">{label}</p>
      <p className={`text-base font-semibold ${positive === false ? 'text-red-400' : positive === true ? 'text-green-400' : 'text-[var(--tg-text)]'}`}>
        {value}
      </p>
    </div>
  )
}
