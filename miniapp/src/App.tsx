import { Routes, Route } from 'react-router-dom'
import { BottomNav } from './components/BottomNav'
import { ErrorBoundary } from './components/ErrorBoundary'
import { useWebSocket } from './hooks/useWebSocket'
import { Dashboard } from './screens/Dashboard/Dashboard'
import { Positions } from './screens/Positions/Positions'
import { Signals } from './screens/Signals/Signals'
import { Analytics } from './screens/Analytics/Analytics'
import { Settings } from './screens/Settings/Settings'

function WsInit() {
  useWebSocket()
  return null
}

export function App() {
  return (
    <div style={{ minHeight: '100dvh', background: 'var(--bg)', color: 'var(--text)' }}>
      <WsInit />
      <div style={{ paddingBottom: 64 }}>
        <Routes>
          <Route path="/" element={<ErrorBoundary><Dashboard /></ErrorBoundary>} />
          <Route path="/positions" element={<ErrorBoundary><Positions /></ErrorBoundary>} />
          <Route path="/signals" element={<ErrorBoundary><Signals /></ErrorBoundary>} />
          <Route path="/analytics" element={<ErrorBoundary><Analytics /></ErrorBoundary>} />
          <Route path="/settings" element={<ErrorBoundary><Settings /></ErrorBoundary>} />
        </Routes>
      </div>
      <BottomNav />
    </div>
  )
}
