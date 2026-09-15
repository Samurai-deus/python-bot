import { describe, expect, it } from 'vitest'
import { MOCK, fixtureHealth, fixtureOverview, mockEnabled } from './mock'

describe('режим фикстур', () => {
  it('включается только на dev-сервере и только флагом VITE_MOCK=1', () => {
    expect(mockEnabled({ DEV: true, VITE_MOCK: '1' })).toBe(true)
    expect(mockEnabled({ DEV: false, VITE_MOCK: '1' })).toBe(false)
    expect(mockEnabled({ DEV: true })).toBe(false)
    expect(mockEnabled({ DEV: true, VITE_MOCK: 'true' })).toBe(false)
    expect(MOCK).toBe(false)
  })

  it('фикстуры покрывают все экраны и помечаются текущим временем', () => {
    const now = new Date('2026-09-16T10:00:00Z')
    const o = fixtureOverview(now)
    expect(o.generated_at).toBe(now.toISOString())
    expect(o.portfolio?.positions.length).toBeGreaterThan(0)
    expect(o.btcalts?.positions.length).toBeGreaterThan(0)
    expect(o.carry?.positions.length).toBeGreaterThan(0)
    expect(o.recorder?.events).toBeTruthy()
    expect(o.calendar.length).toBeGreaterThan(0)
    expect(o.program.btcalts.title).toContain('И18')
    expect(fixtureHealth(now).timestamp).toBe(now.toISOString())
  })
})
