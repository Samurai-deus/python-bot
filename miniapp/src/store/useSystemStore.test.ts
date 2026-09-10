import { describe, it, expect, beforeEach } from 'vitest'
import { useSystemStore } from './useSystemStore'

// Стор собран на middleware immer из zustand. Сборка проверяет только типы; этот
// тест — что при обновлении immer (10 → 11) сеттеры по-прежнему меняют состояние.
describe('useSystemStore (zustand + immer)', () => {
  beforeEach(() => {
    useSystemStore.setState({ snapshot: null, wsStatus: 'connecting', lastSnapshotAt: null, authExpired: false })
  })

  it('setAuthExpired flips the flag', () => {
    useSystemStore.getState().setAuthExpired(true)
    expect(useSystemStore.getState().authExpired).toBe(true)
  })

  it('setWsStatus and touchSnapshot update state', () => {
    const { setWsStatus, touchSnapshot } = useSystemStore.getState()
    setWsStatus('connected')
    touchSnapshot()
    expect(useSystemStore.getState().wsStatus).toBe('connected')
    expect(useSystemStore.getState().lastSnapshotAt).toBeInstanceOf(Date)
  })

  it('setSnapshot stores the snapshot', () => {
    const snap = { timestamp: 't', system_state: 'RUNNING', trading_paused: false, balance_usdt: null, positions: [] }
    useSystemStore.getState().setSnapshot(snap)
    expect(useSystemStore.getState().snapshot).toEqual(snap)
  })
})
