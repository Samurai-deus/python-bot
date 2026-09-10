import { useQuery } from '@tanstack/react-query'
import { useSystemStore } from '../store/useSystemStore'
import { fetchHealth } from '../api/endpoints'

export function useSystemHealth() {
  const authExpired = useSystemStore((s) => s.authExpired)
  return useQuery({
    queryKey: ['health'],
    queryFn: fetchHealth,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    enabled: !authExpired,
  })
}
