import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import type { AxiosAdapter, InternalAxiosRequestConfig } from 'axios'
import { apiClient, getAuthToken, resetSession, setInitData, startSession, waitForSession } from './client'

const INIT_DATA = 'query_id=1&user=%7B%22id%22%3A1%7D&auth_date=1&hash=abc'

let seen: InternalAxiosRequestConfig[] = []
const originalAdapter = apiClient.defaults.adapter

const recordingAdapter: AxiosAdapter = async (config) => {
  seen.push(config)
  return { data: {}, status: 200, statusText: 'OK', headers: {}, config }
}

function header(config: InternalAxiosRequestConfig, name: string): unknown {
  return config.headers[name]
}

beforeEach(() => {
  seen = []
  resetSession()
  setInitData(INIT_DATA)
  apiClient.defaults.adapter = recordingAdapter
})

afterEach(() => {
  apiClient.defaults.adapter = originalAdapter
  setInitData('')
  resetSession()
})

describe('session (2.8)', () => {
  it('after the exchange requests carry the session and not initData', async () => {
    await startSession(async () => ({ token: 's1.abc' }))
    await apiClient.get('/api/system/health')
    expect(header(seen[0], 'Authorization')).toBe('Bearer s1.abc')
    expect(header(seen[0], 'X-Telegram-Init-Data')).toBeUndefined()
    expect(getAuthToken()).toBe('s1.abc')
  })

  it('a failed exchange falls back to initData', async () => {
    await startSession(async () => { throw new Error('503') })
    await apiClient.get('/api/system/health')
    expect(header(seen[0], 'X-Telegram-Init-Data')).toBe(INIT_DATA)
    expect(header(seen[0], 'Authorization')).toBeUndefined()
    expect(getAuthToken()).toBe(INIT_DATA)
  })

  it('an unexpected answer is treated as a failed exchange', async () => {
    await startSession(async () => ({ token: 'not-a-session' }))
    expect(getAuthToken()).toBe(INIT_DATA)
  })

  it('a request sent before the exchange finished waits for it', async () => {
    let finish: (value: unknown) => void = () => undefined
    const exchange = startSession(() => new Promise((resolve) => { finish = resolve }))
    const request = apiClient.get('/api/positions/open')
    await Promise.resolve()
    expect(seen).toHaveLength(0)
    finish({ token: 's1.late' })
    await exchange
    await request
    expect(header(seen[0], 'Authorization')).toBe('Bearer s1.late')
  })

  it('outside Telegram there is no exchange and no auth header', async () => {
    setInitData('')
    let called = false
    await startSession(async () => { called = true; return { token: 's1.x' } })
    await waitForSession()
    await apiClient.get('/api/system/health')
    expect(called).toBe(false)
    expect(header(seen[0], 'Authorization')).toBeUndefined()
    expect(header(seen[0], 'X-Telegram-Init-Data')).toBeUndefined()
  })
})
