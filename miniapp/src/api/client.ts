import axios from 'axios'
import { logger } from '../lib/logger'

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

// ---------------------------------------------------------------------------
// Серверная сессия (задача 2.8, docs/DEFERRED_PLAN.md п. 4)
//
// initData обменивается на токен сессии один раз, при старте: сервер принимает
// initData для обмена только в первый час после открытия и только однократно.
// Поэтому повторного обмена нет — истекла сессия (401) → прежний баннер «откройте
// снова из Telegram». Токен живёт только в памяти, не в localStorage.
// Не удалось обменять (сеть, старый сервер, Redis недоступен) — запросы идут с
// initData, пока сервер его ещё принимает.
// ---------------------------------------------------------------------------

let sessionToken = ''
let sessionReady: Promise<void> = Promise.resolve()

type ExchangePost = (initData: string) => Promise<unknown>

async function postExchange(data: string): Promise<unknown> {
  // Отдельный axios, не apiClient: его перехватчик сам ждёт сессию.
  const res = await axios.post('/api/auth/session', null, {
    headers: { 'X-Telegram-Init-Data': data },
    timeout: 10_000,
  })
  return res.data
}

/** Обменять initData на сессию. Вне Telegram (initData пуст) — ничего не делает. */
export function startSession(post: ExchangePost = postExchange): Promise<void> {
  if (!initData) return Promise.resolve()
  sessionReady = post(initData)
    .then((body) => {
      const token = (body as { token?: unknown } | null)?.token
      if (typeof token !== 'string' || !token.startsWith('s1.')) throw new Error('unexpected session response')
      sessionToken = token
    })
    .catch((err: unknown) => {
      sessionToken = ''
      logger.warn('Сессия не получена — запросы идут с initData', err)
    })
  return sessionReady
}

/** Дождаться обмена (успешного или нет). */
export function waitForSession(): Promise<void> {
  return sessionReady
}

/** Токен для WebSocket: сессия, а без неё — initData. */
export function getAuthToken(): string {
  return sessionToken || initData
}

/** Сброс сессии — для тестов. */
export function resetSession() {
  sessionToken = ''
  sessionReady = Promise.resolve()
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

apiClient.interceptors.request.use(async (config) => {
  await sessionReady
  if (sessionToken) {
    config.headers['Authorization'] = `Bearer ${sessionToken}`
  } else if (initData) {
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
