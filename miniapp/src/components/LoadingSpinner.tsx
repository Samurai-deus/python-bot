export function LoadingSpinner() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '40px 0' }}>
      <div style={{ position: 'relative', width: 36, height: 36 }}>
        {/* Outer static ring */}
        <div style={{
          position: 'absolute', inset: 0,
          borderRadius: '50%',
          border: '1px solid rgba(0,212,255,0.1)',
        }} />
        {/* Spinning arc */}
        <div style={{
          position: 'absolute', inset: 0,
          borderRadius: '50%',
          border: '2px solid transparent',
          borderTopColor: '#00d4ff',
          borderRightColor: 'rgba(0,212,255,0.25)',
          animation: 'neon-spin 0.75s linear infinite',
          boxShadow: '0 0 10px rgba(0,212,255,0.4)',
        }} />
        {/* Center dot */}
        <div style={{
          position: 'absolute', inset: 0,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <div style={{
            width: 5, height: 5,
            borderRadius: '50%',
            background: '#00d4ff',
            boxShadow: '0 0 8px #00d4ff',
            animation: 'pulse-glow 1s ease-in-out infinite',
          }} />
        </div>
      </div>
    </div>
  )
}
