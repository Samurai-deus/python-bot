import { create } from 'zustand'
import { immer } from 'zustand/middleware/immer'
import type { WsSnapshot } from '../api/types'

type WsStatus = 'connecting' | 'connected' | 'reconnecting' | 'disconnected'

interface SystemStore {
  snapshot: WsSnapshot | null
  wsStatus: WsStatus
  lastSnapshotAt: Date | null
  /** Сервер отказал в доступе (401 или отказ WebSocket по авторизации): опрос остановлен, нужен баннер. */
  authExpired: boolean
  setSnapshot: (s: WsSnapshot) => void
  setWsStatus: (s: WsStatus) => void
  touchSnapshot: () => void
  setAuthExpired: (v: boolean) => void
}

export const useSystemStore = create<SystemStore>()(
  immer((set) => ({
    snapshot: null,
    wsStatus: 'connecting',
    lastSnapshotAt: null,
    authExpired: false,
    setSnapshot: (snapshot) => set((state) => { state.snapshot = snapshot }),
    setWsStatus: (wsStatus) => set((state) => { state.wsStatus = wsStatus }),
    touchSnapshot: () => set((state) => { state.lastSnapshotAt = new Date() }),
    setAuthExpired: (v) => set((state) => { state.authExpired = v }),
  }))
)
