import { useQuery } from '@tanstack/react-query'
import { useSystemStore } from '../store/useSystemStore'
import { fetchResearchOverview } from '../api/endpoints'

export function useResearch() {
  const authExpired = useSystemStore((s) => s.authExpired)
  return useQuery({
    queryKey: ['research-overview'],
    queryFn: fetchResearchOverview,
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
    enabled: !authExpired,
  })
}
