import type { ReactNode } from 'react'
import { useSystemHealth } from '../../hooks/useSystemHealth'
import { useSystemStore } from '../../store/useSystemStore'
import { LoadingSpinner } from '../../components/LoadingSpinner'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Badge } from '../../components/Badge'
import { formatDate } from '../../lib/formatters'

const WS_COLOR: Record<string, string> = {
  connected:    'var(--green)',
  connecting:   'var(--amber)',
  reconnecting: 'var(--amber)',
  disconnected: 'var(--red)',
}

export function Settings() {
  const { data: health, isLoading, error } = useSystemHealth()
  const { wsStatus } = useSystemStore()

  return (
    <div className="grid-bg" style={{ padding: '16px 16px 0', minHeight: '100dvh' }}>

      {/* Header */}
      <div style={{ marginBottom: 16 }}>
        <p style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.35em', textTransform: 'uppercase', color: 'var(--cyan)', textShadow: '0 0 10px rgba(0,212,255,0.55)', marginBottom: 2 }}>
          DIAGNOSTICS
        </p>
        <h1 style={{ fontSize: 20, fontWeight: 700, color: '#fff', margin: 0 }}>System Health</h1>
      </div>

      {isLoading && <LoadingSpinner />}
      {error && <ErrorBanner message={error.message} />}

      {health && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 16 }}>

          <InfoCard
            label="System State"
            icon={<StateIcon color={health.state === 'RUNNING' ? 'var(--green)' : health.state === 'DEGRADED' || health.state === 'RECOVERY' ? 'var(--amber)' : 'var(--red)'} />}
            value={
              <Badge
                label={health.state}
                variant={
                  health.state === 'RUNNING' ? 'running'
                  : health.state === 'DEGRADED' || health.state === 'RECOVERY' ? 'degraded'
                  : 'halt'
                }
              />
            }
          />

          <InfoCard
            label="Trading"
            icon={<SignalIcon color={health.trading_paused ? 'var(--red)' : 'var(--green)'} />}
            value={
              health.trading_paused
                ? <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--red)', textShadow: '0 0 8px rgba(255,68,102,0.5)' }}>PAUSED</span>
                : <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--green)', textShadow: '0 0 8px rgba(0,255,157,0.5)' }}>ACTIVE</span>
            }
          />

          <InfoCard
            label="Mode"
            icon={<ModeIcon />}
            value={<span style={{ fontSize: 11, fontFamily: 'monospace', fontWeight: 700, color: 'var(--cyan)', letterSpacing: '0.08em' }}>{(health.trading_mode ?? 'DRY_RUN').replace('_', ' ')}</span>}
          />

          <InfoCard
            label="Consecutive Errors"
            icon={<ErrorIcon active={health.consecutive_errors > 0} />}
            value={
              <span style={{
                fontSize: 14,
                fontFamily: 'monospace',
                fontWeight: 700,
                color: health.consecutive_errors > 0 ? 'var(--amber)' : 'var(--green)',
                textShadow: health.consecutive_errors > 0 ? '0 0 8px rgba(255,170,0,0.5)' : '0 0 8px rgba(0,255,157,0.4)',
              }}>
                {health.consecutive_errors}
              </span>
            }
          />

          <InfoCard
            label="Last Update"
            icon={<ClockIcon />}
            value={<span style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--text-dim)' }}>{formatDate(health.timestamp)}</span>}
          />

          <InfoCard
            label="WebSocket"
            icon={<WsIcon color={WS_COLOR[wsStatus] ?? 'var(--text-dim)'} active={wsStatus !== 'disconnected'} />}
            value={
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{
                  width: 6, height: 6, borderRadius: '50%', display: 'inline-block',
                  background: WS_COLOR[wsStatus] ?? 'var(--text-dim)',
                  boxShadow: `0 0 6px ${WS_COLOR[wsStatus] ?? 'transparent'}`,
                  animation: wsStatus !== 'connected' ? 'pulse-glow 1s ease-in-out infinite' : undefined,
                }} />
                <span style={{
                  fontSize: 11, fontFamily: 'monospace', fontWeight: 700,
                  textTransform: 'uppercase', letterSpacing: '0.1em',
                  color: WS_COLOR[wsStatus] ?? 'var(--text-dim)',
                }}>
                  {wsStatus}
                </span>
              </div>
            }
          />
        </div>
      )}

      {/* Note */}
      <div style={{
        padding: '12px 16px',
        borderRadius: 14,
        background: 'rgba(0,212,255,0.03)',
        border: '1px solid rgba(0,212,255,0.07)',
        marginBottom: 16,
      }}>
        <p style={{ fontSize: 11, color: 'var(--text-dim)', lineHeight: 1.6, letterSpacing: '0.02em' }}>
          Write controls are not available in this version.<br />
          Use Telegram bot commands to manage settings.
        </p>
      </div>
    </div>
  )
}

function InfoCard({ label, value, icon }: { label: string; value: ReactNode; icon?: ReactNode }) {
  return (
    <div style={{
      borderRadius: 14,
      padding: '12px 14px',
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        {icon}
        <span style={{ fontSize: 11, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-dim)', fontWeight: 500 }}>
          {label}
        </span>
      </div>
      {value}
    </div>
  )
}

/* ── Small inline SVG icons ─── */
function StateIcon({ color }: { color: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
      style={{ width: 14, height: 14, flexShrink: 0, filter: `drop-shadow(0 0 3px ${color}80)` }}>
      <circle cx="8" cy="8" r="6" />
      <circle cx="8" cy="8" r="2" fill={color} stroke="none" />
    </svg>
  )
}
function SignalIcon({ color }: { color: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
      style={{ width: 14, height: 14, flexShrink: 0, filter: `drop-shadow(0 0 3px ${color}80)` }}>
      <polyline points="2 10 5 7 8 9 11 5 14 7" />
    </svg>
  )
}
function ModeIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="var(--cyan)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
      style={{ width: 14, height: 14, flexShrink: 0, opacity: 0.6 }}>
      <rect x="2" y="4" width="12" height="8" rx="1.5" />
      <path d="M5 8h2M9 8h2" />
    </svg>
  )
}
function ErrorIcon({ active }: { active: boolean }) {
  const color = active ? 'var(--amber)' : 'var(--text-dim)'
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
      style={{ width: 14, height: 14, flexShrink: 0, opacity: active ? 1 : 0.5 }}>
      <path d="M8 2l6 12H2L8 2z" />
      <line x1="8" y1="7" x2="8" y2="10" />
      <line x1="8" y1="12" x2="8.01" y2="12" />
    </svg>
  )
}
function ClockIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="var(--text-dim)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
      style={{ width: 14, height: 14, flexShrink: 0, opacity: 0.55 }}>
      <circle cx="8" cy="8" r="6" />
      <polyline points="8 5 8 8 10 10" />
    </svg>
  )
}
function WsIcon({ color, active }: { color: string; active: boolean }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
      style={{ width: 14, height: 14, flexShrink: 0, filter: active ? `drop-shadow(0 0 3px ${color}80)` : 'none' }}>
      <path d="M2 8a6 6 0 0 1 12 0" />
      <path d="M4.5 8a3.5 3.5 0 0 1 7 0" />
      <circle cx="8" cy="8" r="1" fill={color} stroke="none" />
    </svg>
  )
}
