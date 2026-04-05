import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { immer } from 'zustand/middleware/immer'

interface SettingsStore {
  analyticsDays: number
  historyDays: number
  setAnalyticsDays: (d: number) => void
  setHistoryDays: (d: number) => void
}

export const useSettingsStore = create<SettingsStore>()(
  persist(
    immer((set) => ({
      analyticsDays: 30,
      historyDays: 30,
      setAnalyticsDays: (d) => set((state) => { state.analyticsDays = d }),
      setHistoryDays: (d) => set((state) => { state.historyDays = d }),
    })),
    { name: 'miniapp-settings' }
  )
)
