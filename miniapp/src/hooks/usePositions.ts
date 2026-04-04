import { useQuery } from '@tanstack/react-query'
import { fetchPositionHistory } from '../api/endpoints'

export function usePositionHistory(days: number) {
  return useQuery({
    queryKey: ['position-history', days],
    queryFn: () => fetchPositionHistory(days),
    refetchInterval: 60_000,
  })
}
