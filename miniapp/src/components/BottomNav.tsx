import { NavLink } from 'react-router-dom'
import { clsx } from 'clsx'

const tabs = [
  { path: '/', label: 'Dashboard', icon: '📊' },
  { path: '/positions', label: 'Positions', icon: '📈' },
  { path: '/signals', label: 'Signals', icon: '⚡' },
  { path: '/analytics', label: 'Analytics', icon: '📉' },
  { path: '/settings', label: 'Settings', icon: '⚙️' },
]

export function BottomNav() {
  return (
    <nav className="fixed bottom-0 left-0 right-0 bg-[var(--tg-secondary)] border-t border-white/10 pb-safe">
      <div className="flex">
        {tabs.map((tab) => (
          <NavLink
            key={tab.path}
            to={tab.path}
            end={tab.path === '/'}
            className={({ isActive }) =>
              clsx(
                'flex-1 flex flex-col items-center py-2 gap-0.5 text-xs transition-colors',
                isActive ? 'text-[var(--tg-button)]' : 'text-[var(--tg-hint)]'
              )
            }
          >
            <span className="text-lg">{tab.icon}</span>
            <span>{tab.label}</span>
          </NavLink>
        ))}
      </div>
    </nav>
  )
}
