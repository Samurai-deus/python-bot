import { describe, expect, it } from 'vitest'
import { pctLabel, WARN_SHARE } from './limits'

describe('пороги правил в подписях', () => {
  it('показывает стоп и полосу так же, как их писали в разметке до 29.09', () => {
    expect(pctLabel(0.25)).toBe('25 %')
    expect(pctLabel(2 / 3)).toBe('66,7 %')
    expect(pctLabel(0.05)).toBe('5 %')
  })

  it('жёлтый — с 60 % стопа (было 15 % при стопе 25 %)', () => {
    expect(0.25 * WARN_SHARE).toBeCloseTo(0.15)
  })
})
