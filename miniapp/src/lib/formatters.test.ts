import { describe, it, expect } from 'vitest'
import { parseUTC, formatDate, formatPct, formatUSDT, formatPnl } from './formatters'

/**
 * Регрессия на разбор дат (аудит 10.09.2026, находка H-22).
 *
 * Бэкенд повсеместно отдаёт datetime.now(UTC).isoformat(), то есть смещение
 * с двоеточием: "…+00:00". Прежнее выражение /[Zz+\-]\d{0,4}$/ такой формат
 * не распознавало, к строке дописывалась 'Z', и получалось "…+00:00Z" —
 * невалидная дата. В карточках вместо времени показывалась сырая ISO-строка,
 * а график эквити оставался пустым: все точки получали NaN и схлопывались
 * в одну, потому что NaN как ключ Map всегда единственный.
 */
describe('parseUTC', () => {
  const expectedMs = Date.UTC(2026, 8, 9, 10, 0, 0, 123)

  it('понимает смещение с двоеточием — формат, который отдаёт бэкенд', () => {
    const d = parseUTC('2026-09-09T10:00:00.123456+00:00')
    expect(Number.isNaN(d.getTime())).toBe(false)
    expect(d.getTime()).toBe(expectedMs)
  })

  it('понимает ненулевое смещение с двоеточием', () => {
    const d = parseUTC('2026-09-09T15:00:00.123456+05:00')
    expect(Number.isNaN(d.getTime())).toBe(false)
    expect(d.getTime()).toBe(expectedMs)
  })

  it('понимает отрицательное смещение', () => {
    const d = parseUTC('2026-09-09T05:00:00.123456-05:00')
    expect(Number.isNaN(d.getTime())).toBe(false)
    expect(d.getTime()).toBe(expectedMs)
  })

  it('понимает суффикс Z', () => {
    const d = parseUTC('2026-09-09T10:00:00.123Z')
    expect(d.getTime()).toBe(expectedMs)
  })

  it('наивную строку без пояса читает как UTC, а не как локальное время', () => {
    const d = parseUTC('2026-09-09T10:00:00.123')
    expect(d.getTime()).toBe(expectedMs)
  })

  it('смещение без двоеточия тоже принимает', () => {
    const d = parseUTC('2026-09-09T10:00:00.123+0000')
    expect(Number.isNaN(d.getTime())).toBe(false)
  })
})

describe('formatDate', () => {
  it('форматирует дату со смещением, а не возвращает сырую строку', () => {
    const iso = '2026-09-09T10:00:00.123456+00:00'
    const out = formatDate(iso)
    // До правки catch возвращал исходную строку — именно её и видел пользователь
    expect(out).not.toBe(iso)
    expect(out).toMatch(/\d{2} \w{3}, \d{2}:\d{2}/)
  })
})

describe('денежные форматтеры', () => {
  it('formatUSDT показывает знак минуса перед символом валюты', () => {
    expect(formatUSDT(-1234.5)).toBe('-$1,234.50')
  })

  it('formatUSDT повышает точность для очень малых величин', () => {
    expect(formatUSDT(0.000123)).toBe('$0.000123')
  })

  it('formatUSDT не падает на нечисловом значении', () => {
    expect(formatUSDT(NaN)).toBe('$—')
    expect(formatUSDT(Infinity)).toBe('$—')
  })

  it('formatPnl добавляет плюс к неотрицательному результату', () => {
    expect(formatPnl(10)).toBe('+$10.00')
    expect(formatPnl(-10)).toBe('-$10.00')
  })

  it('formatPct печатает проценты как есть — бэкенд уже отдаёт 0-100', () => {
    expect(formatPct(75.5)).toBe('75.50%')
  })
})
