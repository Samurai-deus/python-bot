import { useEffect } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { onAuthExpired } from './api/client'
import { ErrorBanner } from './components/ErrorBanner'
import { useSystemStore } from './store/useSystemStore'
import { BottomNav } from './components/BottomNav'
import { ErrorBoundary } from './components/ErrorBoundary'
import { useWebSocket } from './hooks/useWebSocket'
import { Overview } from './screens/Overview/Overview'
import { Portfolios } from './screens/Portfolios/Portfolios'
import { Data } from './screens/Data/Data'
import { Calendar } from './screens/Calendar/Calendar'
import { System } from './screens/System/System'

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

/**
 * Экраны мини-аппа — по исследовательской программе (docs/TRADER_PLAN.md): обзор, портфели
 * трёх исполнителей, данные и наблюдения, календарь проверок, система. Экраны сигнальной
 * торговли бота убраны: она выключена по правилу И14 с 14.09.2026.
 */
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
          <Route path="/" element={<ErrorBoundary><Overview /></ErrorBoundary>} />
          <Route path="/portfolios" element={<ErrorBoundary><Portfolios /></ErrorBoundary>} />
          <Route path="/data" element={<ErrorBoundary><Data /></ErrorBoundary>} />
          <Route path="/calendar" element={<ErrorBoundary><Calendar /></ErrorBoundary>} />
          <Route path="/system" element={<ErrorBoundary><System /></ErrorBoundary>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
      <BottomNav />
    </div>
  )
}
