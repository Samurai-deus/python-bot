interface Props {
  label: string
  variant?: 'buy' | 'sell' | 'long' | 'short' | 'running' | 'degraded' | 'halt' | 'recovery' | 'neutral'
}

const variantStyles: Record<string, { bg: string; color: string; border: string; shadow: string }> = {
  buy:      { bg: 'rgba(0,255,157,0.08)',   color: '#00ff9d', border: 'rgba(0,255,157,0.35)',   shadow: '0 0 8px rgba(0,255,157,0.25)' },
  long:     { bg: 'rgba(0,255,157,0.08)',   color: '#00ff9d', border: 'rgba(0,255,157,0.35)',   shadow: '0 0 8px rgba(0,255,157,0.25)' },
  sell:     { bg: 'rgba(255,68,102,0.08)',  color: '#ff4466', border: 'rgba(255,68,102,0.35)',  shadow: '0 0 8px rgba(255,68,102,0.25)' },
  short:    { bg: 'rgba(255,68,102,0.08)',  color: '#ff4466', border: 'rgba(255,68,102,0.35)',  shadow: '0 0 8px rgba(255,68,102,0.25)' },
  running:  { bg: 'rgba(0,255,157,0.08)',   color: '#00ff9d', border: 'rgba(0,255,157,0.35)',   shadow: '0 0 8px rgba(0,255,157,0.25)' },
  degraded: { bg: 'rgba(255,170,0,0.08)',   color: '#ffaa00', border: 'rgba(255,170,0,0.35)',   shadow: '0 0 8px rgba(255,170,0,0.25)' },
  halt:     { bg: 'rgba(255,68,102,0.08)',  color: '#ff4466', border: 'rgba(255,68,102,0.35)',  shadow: '0 0 8px rgba(255,68,102,0.25)' },
  recovery: { bg: 'rgba(255,170,0,0.08)',   color: '#ffaa00', border: 'rgba(255,170,0,0.35)',   shadow: '0 0 8px rgba(255,170,0,0.25)' },
  neutral:  { bg: 'rgba(78,96,112,0.12)',   color: '#64748b', border: 'rgba(78,96,112,0.25)',   shadow: 'none' },
}

export function Badge({ label, variant = 'neutral' }: Props) {
  const s = variantStyles[variant] ?? variantStyles.neutral
  return (
    <span
      style={{
        display: 'inline-block',
        padding: '2px 8px',
        borderRadius: 4,
        fontSize: 10,
        fontWeight: 700,
        letterSpacing: '0.12em',
        textTransform: 'uppercase',
        background: s.bg,
        color: s.color,
        border: `1px solid ${s.border}`,
        boxShadow: s.shadow,
        textShadow: s.shadow !== 'none' ? `0 0 6px ${s.color}80` : 'none',
        fontFamily: 'monospace',
      }}
    >
      {label}
    </span>
  )
}
