import { lazy, Suspense, useEffect } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { onAuthExpired } from './api/client'
import { ErrorBanner } from './components/ErrorBanner'
import { useSystemStore } from './store/useSystemStore'
import { BottomNav } from './components/BottomNav'
import { ErrorBoundary } from './components/ErrorBoundary'
import { useWebSocket } from './hooks/useWebSocket'
import { Dashboard } from './screens/Dashboard/Dashboard'
import { Positions } from './screens/Positions/Positions'
import { Signals } from './screens/Signals/Signals'
import { LoadingSpinner } from './components/LoadingSpinner'
// Аналитика тянет lightweight-charts — отдельным чанком, только когда экран открыт.
const Analytics = lazy(() => import('./screens/Analytics/Analytics').then((m) => ({ default: m.Analytics })))
import { Settings } from './screens/Settings/Settings'

function WsInit() {
  useWebSocket()
  return null
}

/** Ответ 401 от API — сессия истекла: опрос останавливается, показывается баннер. */
function AuthWatch() {
  const setAuthExpired = useSystemStore((s) => s.setAuthExpired)
  useEffect(() => onAuthExpired(() => setAuthExpired(true)), [setAuthExpired])
  return null
}

export function App() {
  const authExpired = useSystemStore((s) => s.authExpired)
  return (
    <div style={{ minHeight: '100dvh', background: 'var(--bg)', color: 'var(--text)' }}>
      <WsInit />
      <AuthWatch />
      {authExpired && (
        <div style={{ padding: '8px 12px 0' }}>
          <ErrorBanner message="Сессия истекла или приложение открыто не из Telegram. Закройте его и откройте снова из чата с ботом." />
        </div>
      )}
      {/* Нижняя навигация на iPhone выше на safe-area-inset-bottom — контент не должен под неё уходить. */}
      <div style={{ paddingBottom: 'calc(64px + env(safe-area-inset-bottom))' }}>
        <Routes>
          <Route path="/" element={<ErrorBoundary><Dashboard /></ErrorBoundary>} />
          <Route path="/positions" element={<ErrorBoundary><Positions /></ErrorBoundary>} />
          <Route path="/signals" element={<ErrorBoundary><Signals /></ErrorBoundary>} />
          <Route path="/analytics" element={<ErrorBoundary><Suspense fallback={<LoadingSpinner />}><Analytics /></Suspense></ErrorBoundary>} />
          <Route path="/settings" element={<ErrorBoundary><Settings /></ErrorBoundary>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
      <BottomNav />
    </div>
  )
}
