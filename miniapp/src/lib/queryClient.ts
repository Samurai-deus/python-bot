import { QueryClient } from '@tanstack/react-query'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 300_000,
      retry: (failureCount, error) => {
        // Don't retry auth failures — they won't resolve on their own
        if (error?.message?.includes('401') || error?.message?.includes('InitData'))
          return false
        return failureCount < 2
      },
      refetchOnWindowFocus: false,
    },
  },
})
