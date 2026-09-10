import { describe, it, expect } from 'vitest'
import { ApiError, describeDetail, onAuthExpired, toApiError } from './client'
import { shouldRetry } from '../lib/queryClient'

describe('toApiError', () => {
  it('keeps the status and the server text', () => {
    const e = toApiError({ message: 'Request failed', response: { status: 401, data: { detail: 'Invalid Telegram InitData' } } })
    expect(e).toBeInstanceOf(ApiError)
    expect(e.status).toBe(401)
    expect(e.message).toBe('Invalid Telegram InitData')
  })

  it('joins validation messages instead of printing [object Object]', () => {
    const e = toApiError({
      message: 'Request failed',
      response: { status: 422, data: { detail: [{ msg: 'field required' }, { msg: 'value is not a valid integer' }] } },
    })
    expect(e.status).toBe(422)
    expect(e.message).toBe('field required; value is not a valid integer')
  })

  it('has no status when there was no response', () => {
    const e = toApiError({ message: 'Network Error' })
    expect(e.status).toBeNull()
    expect(e.message).toBe('Network Error')
  })
})

describe('describeDetail', () => {
  it('ignores what it cannot read', () => {
    expect(describeDetail({ foo: 1 })).toBe('')
    expect(describeDetail(undefined)).toBe('')
  })
})

describe('shouldRetry', () => {
  it('retries network errors and 5xx', () => {
    expect(shouldRetry(0, new ApiError('Network Error', null))).toBe(true)
    expect(shouldRetry(1, new ApiError('Bad Gateway', 502))).toBe(true)
  })

  it('does not retry access and request errors', () => {
    for (const status of [401, 403, 404, 422]) {
      expect(shouldRetry(0, new ApiError('no', status))).toBe(false)
    }
  })

  it('gives up after two failures', () => {
    expect(shouldRetry(2, new ApiError('Bad Gateway', 502))).toBe(false)
  })
})

describe('onAuthExpired', () => {
  it('returns an unsubscribe function', () => {
    const off = onAuthExpired(() => undefined)
    expect(typeof off).toBe('function')
    off()
  })
})
