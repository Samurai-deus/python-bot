import { init, retrieveRawInitData, miniApp, viewport, themeParams } from '@telegram-apps/sdk-react'
import { setInitData } from '../api/client'

export function initTelegram() {
  try {
    init()

    void miniApp.mount()
    void viewport.mount()
    viewport.expand()

    try {
      const rawInitData = retrieveRawInitData()
      if (rawInitData) {
        setInitData(rawInitData)
      }
    } catch {
      /* not in TMA context, no initData */
    }

    void themeParams.mount()

    applyThemeVars()
  } catch {
    applyFallbackTheme()
  }
}

function applyThemeVars() {
  try {
    const tp = themeParams.state()
    if (!tp) { applyFallbackTheme(); return }
    const root = document.documentElement
    if (tp.backgroundColor) root.style.setProperty('--tg-bg', tp.backgroundColor)
    if (tp.secondaryBackgroundColor) root.style.setProperty('--tg-secondary', tp.secondaryBackgroundColor)
    if (tp.textColor) root.style.setProperty('--tg-text', tp.textColor)
    if (tp.hintColor) root.style.setProperty('--tg-hint', tp.hintColor)
    if (tp.linkColor) root.style.setProperty('--tg-link', tp.linkColor)
    if (tp.buttonColor) root.style.setProperty('--tg-button', tp.buttonColor)
    if (tp.buttonTextColor) root.style.setProperty('--tg-button-text', tp.buttonTextColor)
  } catch {
    applyFallbackTheme()
  }
}

function applyFallbackTheme() {
  const root = document.documentElement
  root.style.setProperty('--tg-bg', '#1c1c1e')
  root.style.setProperty('--tg-secondary', '#2c2c2e')
  root.style.setProperty('--tg-text', '#ffffff')
  root.style.setProperty('--tg-hint', '#8e8e93')
  root.style.setProperty('--tg-link', '#0a84ff')
  root.style.setProperty('--tg-button', '#0a84ff')
  root.style.setProperty('--tg-button-text', '#ffffff')
}
