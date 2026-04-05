import { create } from 'zustand'
import { immer } from 'zustand/middleware/immer'
import type { WsSnapshot } from '../api/types'

type WsStatus = 'connecting' | 'connected' | 'reconnecting' | 'disconnected'

interface SystemStore {
  snapshot: WsSnapshot | null
  wsStatus: WsStatus
  lastSnapshotAt: Date | null
  setSnapshot: (s: WsSnapshot) => void
  setWsStatus: (s: WsStatus) => void
  touchSnapshot: () => void
}

export const useSystemStore = create<SystemStore>()(
  immer((set) => ({
    snapshot: null,
    wsStatus: 'connecting',
    lastSnapshotAt: null,
    setSnapshot: (snapshot) => set((state) => { state.snapshot = snapshot }),
    setWsStatus: (wsStatus) => set((state) => { state.wsStatus = wsStatus }),
    touchSnapshot: () => set((state) => { state.lastSnapshotAt = new Date() }),
  }))
)
