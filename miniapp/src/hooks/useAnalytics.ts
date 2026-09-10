import { useQuery } from '@tanstack/react-query'
import { useSystemStore } from '../store/useSystemStore'
import {
  fetchAnalyticsSummary,
  fetchEquityCurve,
  fetchBySymbol,
  fetchMonthlyTarget,
} from '../api/endpoints'

export function useAnalyticsSummary(days: number) {
  const authExpired = useSystemStore((s) => s.authExpired)
  return useQuery({
    queryKey: ['analytics-summary', days],
    queryFn: () => fetchAnalyticsSummary(days),
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
    enabled: !authExpired,
  })
}

export function useEquityCurve(days: number) {
  const authExpired = useSystemStore((s) => s.authExpired)
  return useQuery({
    queryKey: ['equity-curve', days],
    queryFn: () => fetchEquityCurve(days),
    refetchInterval: 120_000,
    refetchIntervalInBackground: false,
    enabled: !authExpired,
  })
}

export function useBySymbol(days: number) {
  const authExpired = useSystemStore((s) => s.authExpired)
  return useQuery({
    queryKey: ['by-symbol', days],
    queryFn: () => fetchBySymbol(days),
    refetchInterval: 120_000,
    refetchIntervalInBackground: false,
    enabled: !authExpired,
  })
}

export function useMonthlyTarget() {
  const authExpired = useSystemStore((s) => s.authExpired)
  return useQuery({
    queryKey: ['monthly-target'],
    queryFn: fetchMonthlyTarget,
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
    enabled: !authExpired,
  })
}
