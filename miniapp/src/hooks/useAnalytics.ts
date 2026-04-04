import { useQuery } from '@tanstack/react-query'
import {
  fetchAnalyticsSummary,
  fetchEquityCurve,
  fetchBySymbol,
  fetchPnlHistory,
  fetchMonthlyTarget,
} from '../api/endpoints'

export function useAnalyticsSummary(days: number) {
  return useQuery({
    queryKey: ['analytics-summary', days],
    queryFn: () => fetchAnalyticsSummary(days),
    refetchInterval: 60_000,
  })
}

export function useEquityCurve(days: number) {
  return useQuery({
    queryKey: ['equity-curve', days],
    queryFn: () => fetchEquityCurve(days),
    refetchInterval: 120_000,
  })
}

export function useBySymbol(days: number) {
  return useQuery({
    queryKey: ['by-symbol', days],
    queryFn: () => fetchBySymbol(days),
    refetchInterval: 120_000,
  })
}

export function usePnlHistory(days: number) {
  return useQuery({
    queryKey: ['pnl-history', days],
    queryFn: () => fetchPnlHistory(days),
  })
}

export function useMonthlyTarget() {
  return useQuery({
    queryKey: ['monthly-target'],
    queryFn: fetchMonthlyTarget,
    refetchInterval: 60_000,
  })
}
