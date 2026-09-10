import axios from 'axios'

export const apiClient = axios.create({
  baseURL: '/',
  timeout: 10_000,
})

let initData = ''

export function setInitData(data: string) {
  initData = data
}

export function getInitData(): string {
  return initData
}

/**
 * Ошибка API со статусом ответа (null — ответа не было: сеть, таймаут).
 * Раньше перехватчик превращал всё в Error(detail): статус терялся, ответ 422
 * (detail — массив) становился «[object Object]», а решение «повторять ли
 * запрос» принималось по подстроке «401» в тексте.
 */
export class ApiError extends Error {
  readonly status: number | null

  constructor(message: string, status: number | null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** Текст из detail FastAPI: строка — как есть, список ошибок валидации — сообщения через «; ». */
export function describeDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item && typeof item === 'object' && 'msg' in item ? String((item as { msg: unknown }).msg) : ''))
      .filter(Boolean)
    if (messages.length) return messages.join('; ')
  }
  return ''
}

interface AxiosLikeError {
  message?: string
  response?: { status?: number; data?: { detail?: unknown } }
}

export function toApiError(err: unknown): ApiError {
  const e = (err ?? {}) as AxiosLikeError
  const status = typeof e.response?.status === 'number' ? e.response.status : null
  const text = describeDetail(e.response?.data?.detail) || e.message || 'Unknown error'
  return new ApiError(text, status)
}

type Listener = () => void
const authExpiredListeners = new Set<Listener>()

/** Подписка на «сессия истекла» (ответ 401). Возвращает функцию отписки. */
export function onAuthExpired(fn: Listener): () => void {
  authExpiredListeners.add(fn)
  return () => {
    authExpiredListeners.delete(fn)
  }
}

apiClient.interceptors.request.use((config) => {
  if (initData) {
    config.headers['X-Telegram-Init-Data'] = initData
  }
  return config
})

apiClient.interceptors.response.use(
  (res) => res,
  (err) => {
    const apiErr = toApiError(err)
    if (apiErr.status === 401) {
      authExpiredListeners.forEach((fn) => fn())
    }
    return Promise.reject(apiErr)
  },
)
