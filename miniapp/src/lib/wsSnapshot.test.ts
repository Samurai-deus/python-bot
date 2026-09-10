import { describe, it, expect } from 'vitest'
import { parseWsSnapshot } from './wsSnapshot'

const base = {
  timestamp: '2026-09-10T12:00:00+00:00',
  system_state: 'RUNNING',
  trading_paused: false,
  balance_usdt: 100.5,
  positions: [{ symbol: 'SOLUSDT', side: 'LONG', entry_price: 100 }],
}

describe('parseWsSnapshot', () => {
  it('accepts a regular snapshot', () => {
    expect(parseWsSnapshot(base)).toEqual(base)
  })

  it('keeps positions when the balance is null', () => {
    // Сервер шлёт null, когда баланс не успел посчитаться; раньше терялся весь снимок.
    const snap = parseWsSnapshot({ ...base, balance_usdt: null })
    expect(snap?.positions).toHaveLength(1)
    expect(snap?.balance_usdt).toBeNull()
  })

  it('rejects a broken balance', () => {
    expect(parseWsSnapshot({ ...base, balance_usdt: '100' })).toBeNull()
    expect(parseWsSnapshot({ ...base, balance_usdt: Number.NaN })).toBeNull()
  })

  it('rejects a broken position', () => {
    expect(parseWsSnapshot({ ...base, positions: [{ symbol: 'SOLUSDT', side: 'LONG' }] })).toBeNull()
    expect(parseWsSnapshot({ ...base, positions: [null] })).toBeNull()
  })

  it('rejects too many positions and non-objects', () => {
    const many = Array.from({ length: 201 }, () => base.positions[0])
    expect(parseWsSnapshot({ ...base, positions: many })).toBeNull()
    expect(parseWsSnapshot(null)).toBeNull()
    expect(parseWsSnapshot('ping')).toBeNull()
  })
})
