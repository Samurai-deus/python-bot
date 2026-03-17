import { create } from 'zustand'
import type { WsSnapshot } from '../api/types'

type WsStatus = 'connecting' | 'connected' | 'reconnecting' | 'disconnected'

interface SystemStore {
  snapshot: WsSnapshot | null
  wsStatus: WsStatus
  setSnapshot: (s: WsSnapshot) => void
  setWsStatus: (s: WsStatus) => void
}

export const useSystemStore = create<SystemStore>((set) => ({
  snapshot: null,
  wsStatus: 'connecting',
  setSnapshot: (snapshot) => set({ snapshot }),
  setWsStatus: (wsStatus) => set({ wsStatus }),
}))
