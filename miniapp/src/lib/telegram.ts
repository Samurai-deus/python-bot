/**
 * Связь с Telegram — через официальный скрипт telegram-web-app.js (подключён в
 * index.html раньше приложения), без SDK. До 11.09.2026 здесь был
 * @telegram-apps/sdk-react: из него брались пять вызовов (init, initData, развернуть
 * окно, тема), а вместе с ним приходили 5 уязвимостей valibot, которые не
 * закрывались обновлением — последняя версия SDK жёстко держит valibot 1.0.0.
 *
 * Зависимости передаются параметрами, чтобы тесты шли без браузера.
 */
import { setInitData } from '../api/client'

interface StyleTarget {
  style: { setProperty(name: string, value: string): void }
}

const THEME_VARS: ReadonlyArray<[keyof TelegramThemeParams, string]> = [
  ['bg_color', '--tg-bg'],
  ['secondary_bg_color', '--tg-secondary'],
  ['text_color', '--tg-text'],
  ['hint_color', '--tg-hint'],
  ['link_color', '--tg-link'],
  ['button_color', '--tg-button'],
  ['button_text_color', '--tg-button-text'],
]

export const FALLBACK_THEME: Readonly<Record<string, string>> = {
  '--tg-bg': '#1c1c1e',
  '--tg-secondary': '#2c2c2e',
  '--tg-text': '#ffffff',
  '--tg-hint': '#8e8e93',
  '--tg-link': '#0a84ff',
  '--tg-button': '#0a84ff',
  '--tg-button-text': '#ffffff',
}

function defaultWebApp(): TelegramWebApp | undefined {
  return typeof window === 'undefined' ? undefined : window.Telegram?.WebApp
}

function defaultRoot(): StyleTarget | undefined {
  return typeof document === 'undefined' ? undefined : document.documentElement
}

function applyFallbackTheme(root: StyleTarget) {
  for (const [name, value] of Object.entries(FALLBACK_THEME)) root.style.setProperty(name, value)
}

/** Тема Telegram в CSS-переменные; вне Telegram (тема пустая) — запасная тёмная. */
export function applyTheme(webApp: TelegramWebApp, root: StyleTarget) {
  const params = webApp.themeParams ?? {}
  if (Object.keys(params).length === 0) {
    applyFallbackTheme(root)
    return
  }
  for (const [key, cssVar] of THEME_VARS) {
    const value = params[key]
    if (value) root.style.setProperty(cssVar, value)
  }
}

export function initTelegram(webApp: TelegramWebApp | undefined = defaultWebApp(),
                             root: StyleTarget | undefined = defaultRoot()) {
  if (!root) return
  if (!webApp) {
    applyFallbackTheme(root)
    return
  }
  try {
    webApp.ready()
    webApp.expand()
  } catch {
    /* старый клиент Telegram без части методов — работаем в текущем размере окна */
  }
  // Вне Telegram скрипт тоже есть, но initData пуст — тогда заголовок не ставится.
  if (webApp.initData) setInitData(webApp.initData)
  applyTheme(webApp, root)
  webApp.onEvent?.('themeChanged', () => applyTheme(webApp, root))
}
