const isDev = import.meta.env.DEV

export const logger = {
  debug: (...args: unknown[]) => { if (isDev) console.debug('[miniapp]', ...args) },
  warn: (...args: unknown[]) => console.warn('[miniapp]', ...args),
  error: (...args: unknown[]) => console.error('[miniapp]', ...args),
}
