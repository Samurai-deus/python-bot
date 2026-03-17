import { Routes, Route } from 'react-router-dom'
import { BottomNav } from './components/BottomNav'
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
    <div className="min-h-screen bg-[var(--tg-bg)] text-[var(--tg-text)]">
      <WsInit />
      <div className="pb-16">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/positions" element={<Positions />} />
          <Route path="/signals" element={<Signals />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </div>
      <BottomNav />
    </div>
  )
}
