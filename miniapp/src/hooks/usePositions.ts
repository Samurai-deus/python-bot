import { useQuery } from '@tanstack/react-query'
import { useSystemStore } from '../store/useSystemStore'
import { fetchPositionHistory } from '../api/endpoints'

export function usePositionHistory(days: number, enabled = true) {
  const authExpired = useSystemStore((s) => s.authExpired)
  return useQuery({
    queryKey: ['position-history', days],
    queryFn: () => fetchPositionHistory(days),
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
    enabled: !authExpired && enabled,
  })
}
