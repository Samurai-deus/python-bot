import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface SettingsStore {
  analyticsDays: number
  historyDays: number
  setAnalyticsDays: (d: number) => void
  setHistoryDays: (d: number) => void
}

export const useSettingsStore = create<SettingsStore>()(
  persist(
    (set) => ({
      analyticsDays: 30,
      historyDays: 30,
      setAnalyticsDays: (analyticsDays) => set({ analyticsDays }),
      setHistoryDays: (historyDays) => set({ historyDays }),
    }),
    { name: 'miniapp-settings' }
  )
)
