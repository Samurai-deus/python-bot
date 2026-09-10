import { useQuery } from '@tanstack/react-query'
import { useSystemStore } from '../store/useSystemStore'
import { fetchLatestSignals } from '../api/endpoints'

export function useLatestSignals(limit = 20) {
  const authExpired = useSystemStore((s) => s.authExpired)
  return useQuery({
    queryKey: ['signals-latest', limit],
    queryFn: () => fetchLatestSignals(limit),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    enabled: !authExpired,
  })
}
