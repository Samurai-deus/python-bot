import { QueryClient } from '@tanstack/react-query'
import { ApiError } from '../api/client'

/**
 * Повторять ли запрос. Только когда ответа не было (сеть, таймаут) или сервер
 * ответил 5xx: остальное само не пройдёт — 401/403 про доступ, прочие 4xx про
 * сам запрос. Раньше решение принималось по подстроке «401» в тексте ошибки, и
 * 403 повторялся впустую.
 */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= 2) return false
  if (error instanceof ApiError) return error.status === null || error.status >= 500
  return true
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 300_000,
      retry: shouldRetry,
      refetchOnWindowFocus: false,
    },
  },
})
