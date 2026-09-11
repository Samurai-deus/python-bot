/**
 * Типы официального скрипта Telegram (telegram-web-app.js) — только то, чем
 * пользуется приложение. Полное описание: https://core.telegram.org/bots/webapps
 */
interface TelegramThemeParams {
  bg_color?: string
  secondary_bg_color?: string
  text_color?: string
  hint_color?: string
  link_color?: string
  button_color?: string
  button_text_color?: string
}

interface TelegramWebApp {
  /** Подписанная строка запуска; вне Telegram — пустая. */
  initData: string
  themeParams: TelegramThemeParams
  ready(): void
  expand(): void
  onEvent?(event: 'themeChanged', handler: () => void): void
}

interface Window {
  Telegram?: { WebApp?: TelegramWebApp }
}
