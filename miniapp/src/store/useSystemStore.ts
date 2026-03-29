import { create } from 'zustand'
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

export const useSystemStore = create<SystemStore>((set) => ({
  snapshot: null,
  wsStatus: 'connecting',
  lastSnapshotAt: null,
  setSnapshot: (snapshot) => set({ snapshot }),
  setWsStatus: (wsStatus) => set({ wsStatus }),
  touchSnapshot: () => set({ lastSnapshotAt: new Date() }),
}))
