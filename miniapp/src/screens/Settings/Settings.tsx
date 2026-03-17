import type { ReactNode } from 'react'
import { useSystemHealth } from '../../hooks/useSystemHealth'
import { useSystemStore } from '../../store/useSystemStore'
import { LoadingSpinner } from '../../components/LoadingSpinner'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Badge } from '../../components/Badge'
import { formatDate } from '../../lib/formatters'

export function Settings() {
  const { data: health, isLoading, error } = useSystemHealth()
  const { wsStatus } = useSystemStore()

  return (
    <div className="px-4 pt-4">
      <h1 className="text-xl font-bold mb-4">Settings</h1>

      {isLoading && <LoadingSpinner />}
      {error && <ErrorBanner message={error.message} />}

      {health && (
        <div className="space-y-3">
          <InfoCard label="System State" value={<Badge label={health.state} variant={
            health.state === 'RUNNING' ? 'running'
            : health.state === 'DEGRADED' || health.state === 'RECOVERY' ? 'degraded'
            : 'halt'
          } />} />
          <InfoCard label="Trading" value={
            health.trading_paused
              ? <span className="text-red-400 text-sm">PAUSED</span>
              : <span className="text-green-400 text-sm">ACTIVE</span>
          } />
          <InfoCard label="Mode" value={
            <span className="text-sm text-[var(--tg-text)]">DRY RUN</span>
          } />
          <InfoCard label="Consecutive Errors" value={
            <span className="text-sm">{health.consecutive_errors}</span>
          } />
          <InfoCard label="Last Update" value={
            <span className="text-sm text-[var(--tg-hint)]">{formatDate(health.timestamp)}</span>
          } />
          <InfoCard label="WS Connection" value={
            <span className={`text-sm ${wsStatus === 'connected' ? 'text-green-400' : 'text-amber-400'}`}>
              {wsStatus}
            </span>
          } />
        </div>
      )}

      <div className="mt-6 p-4 bg-[var(--tg-secondary)] rounded-2xl">
        <p className="text-xs text-[var(--tg-hint)]">Write controls are not available in this version. Use Telegram bot commands to manage settings.</p>
      </div>
    </div>
  )
}

function InfoCard({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="bg-[var(--tg-secondary)] rounded-xl p-4 flex items-center justify-between">
      <span className="text-sm text-[var(--tg-hint)]">{label}</span>
      {value}
    </div>
  )
}
