interface Props { message: string }

export function ErrorBanner({ message }: Props) {
  return (
    <div
      className="mx-0 mt-2 mb-3"
      style={{
        padding: '10px 14px',
        borderRadius: 12,
        background: 'rgba(255,68,102,0.07)',
        border: '1px solid rgba(255,68,102,0.28)',
        color: '#ff4466',
        fontSize: 13,
        display: 'flex',
        alignItems: 'flex-start',
        gap: 8,
        boxShadow: '0 0 16px rgba(255,68,102,0.08), inset 0 0 16px rgba(255,68,102,0.04)',
      }}
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
        style={{ width: 15, height: 15, flexShrink: 0, marginTop: 1 }}>
        <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
        <line x1="12" y1="9" x2="12" y2="13" />
        <line x1="12" y1="17" x2="12.01" y2="17" />
      </svg>
      <span>{message}</span>
    </div>
  )
}
