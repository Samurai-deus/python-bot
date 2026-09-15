import type { ReactNode } from 'react'
import { NavLink } from 'react-router-dom'

const icon = (children: ReactNode) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ width: 20, height: 20 }}>
    {children}
  </svg>
)

const tabs = [
  { path: '/', label: 'Обзор', icon: icon(<><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></>) },
  { path: '/portfolios', label: 'Портфели', icon: icon(<><polyline points="22 7 13.5 15.5 8.5 10.5 2 17" /><polyline points="16 7 22 7 22 13" /></>) },
  { path: '/data', label: 'Данные', icon: icon(<><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5" /><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" /></>) },
  { path: '/calendar', label: 'Календарь', icon: icon(<><rect x="3" y="4" width="18" height="17" rx="2" /><path d="M3 9h18M8 2v4M16 2v4M8 13h3M13 13h3M8 17h3" /></>) },
  { path: '/system', label: 'Система', icon: icon(<><circle cx="12" cy="12" r="3" /><path d="M19.07 4.93a10 10 0 0 1 0 14.14M4.93 4.93a10 10 0 0 0 0 14.14M12 2v2M12 20v2M2 12h2M20 12h2" /></>) },
]

export function BottomNav() {
  return (
    <nav
      className="fixed bottom-0 left-0 right-0"
      style={{
        background: 'rgba(4, 8, 18, 0.94)',
        backdropFilter: 'blur(20px)',
        WebkitBackdropFilter: 'blur(20px)',
        borderTop: '1px solid rgba(0, 212, 255, 0.12)',
        boxShadow: '0 -8px 32px rgba(0, 0, 0, 0.5)',
        paddingBottom: 'env(safe-area-inset-bottom)',
      }}
    >
      <div className="flex">
        {tabs.map((tab) => (
          <NavLink key={tab.path} to={tab.path} end={tab.path === '/'} className="flex-1">
            {({ isActive }) => (
              <div
                className="flex flex-col items-center py-2.5 gap-1 relative transition-all duration-200"
                style={{ color: isActive ? 'var(--cyan)' : 'var(--text-dim)', filter: isActive ? 'drop-shadow(0 0 5px rgba(0,212,255,0.7))' : 'none' }}
              >
                {isActive && (
                  <span className="absolute top-0" style={{ left: '25%', right: '25%', height: 2, borderRadius: 1, background: 'var(--cyan)', boxShadow: '0 0 8px var(--cyan), 0 0 16px rgba(0,212,255,0.4)' }} />
                )}
                {tab.icon}
                <span style={{ fontSize: 9, fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase' }}>{tab.label}</span>
              </div>
            )}
          </NavLink>
        ))}
      </div>
    </nav>
  )
}
