import { useQuery } from '@tanstack/react-query'
import { fetchHealth } from '../api/endpoints'

export function useSystemHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: fetchHealth,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  })
}
