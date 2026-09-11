import { describe, it, expect, beforeEach } from 'vitest'
import { getInitData, setInitData } from '../api/client'
import { FALLBACK_THEME, initTelegram } from './telegram'

function fakeRoot() {
  const vars: Record<string, string> = {}
  return { vars, style: { setProperty: (name: string, value: string) => { vars[name] = value } } }
}

function fakeWebApp(overrides: Partial<TelegramWebApp> = {}) {
  const calls: string[] = []
  const handlers: Record<string, () => void> = {}
  const webApp: TelegramWebApp = {
    initData: 'query_id=1&user=%7B%22id%22%3A1%7D&auth_date=1&hash=abc',
    themeParams: { bg_color: '#ffffff', text_color: '#000000', button_color: '#2481cc' },
    ready: () => { calls.push('ready') },
    expand: () => { calls.push('expand') },
    onEvent: (event, handler) => { handlers[event] = handler },
    ...overrides,
  }
  return { webApp, calls, handlers }
}

describe('initTelegram', () => {
  beforeEach(() => setInitData(''))

  it('outside Telegram uses the fallback theme and sends no initData', () => {
    const root = fakeRoot()
    initTelegram(undefined, root)
    expect(root.vars).toEqual(FALLBACK_THEME)
    expect(getInitData()).toBe('')
  })

  it('in a plain browser the script is there but empty — fallback theme, no initData', () => {
    const root = fakeRoot()
    const { webApp } = fakeWebApp({ initData: '', themeParams: {} })
    initTelegram(webApp, root)
    expect(root.vars).toEqual(FALLBACK_THEME)
    expect(getInitData()).toBe('')
  })

  it('inside Telegram hands initData to the API client, expands and applies the theme', () => {
    const root = fakeRoot()
    const { webApp, calls } = fakeWebApp()
    initTelegram(webApp, root)
    expect(getInitData()).toBe(webApp.initData)
    expect(calls).toEqual(['ready', 'expand'])
    expect(root.vars).toEqual({ '--tg-bg': '#ffffff', '--tg-text': '#000000', '--tg-button': '#2481cc' })
  })

  it('follows a theme change in Telegram', () => {
    const root = fakeRoot()
    const { webApp, handlers } = fakeWebApp()
    initTelegram(webApp, root)
    webApp.themeParams = { bg_color: '#000000', text_color: '#ffffff' }
    handlers.themeChanged()
    expect(root.vars['--tg-bg']).toBe('#000000')
    expect(root.vars['--tg-text']).toBe('#ffffff')
  })

  it('an old client that cannot expand still gets initData and the theme', () => {
    const root = fakeRoot()
    const { webApp } = fakeWebApp({ expand: () => { throw new Error('not supported') } })
    initTelegram(webApp, root)
    expect(getInitData()).toBe(webApp.initData)
    expect(root.vars['--tg-bg']).toBe('#ffffff')
  })
})
