import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { immer } from 'zustand/middleware/immer'

interface SettingsStore {
  analyticsDays: number
  setAnalyticsDays: (d: number) => void
}

export const useSettingsStore = create<SettingsStore>()(
  persist(
    immer((set) => ({
      analyticsDays: 30,
      setAnalyticsDays: (d) => set((state) => { state.analyticsDays = d }),
    })),
    { name: 'miniapp-settings' }
  )
)
