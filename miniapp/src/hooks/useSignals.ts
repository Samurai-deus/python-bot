import { useQuery } from '@tanstack/react-query'
import { fetchLatestSignals } from '../api/endpoints'

export function useLatestSignals(limit = 20) {
  return useQuery({
    queryKey: ['signals-latest', limit],
    queryFn: () => fetchLatestSignals(limit),
    refetchInterval: 30_000,
  })
}
