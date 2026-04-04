import { apiClient } from './client'
import type {
  SystemHealth, TradeHistory,
  Signal, AnalyticsSummary, EquityCurve, SymbolPnl,
  MonthlyTarget
} from './types'

export const fetchHealth = () =>
  apiClient.get<SystemHealth>('/api/system/health').then(r => r.data)

export const fetchPositionHistory = (days = 30) =>
  apiClient.get<TradeHistory[]>('/api/positions/history', { params: { days } }).then(r => r.data)

export const fetchLatestSignals = (limit = 20) =>
  apiClient.get<Signal[]>('/api/signals/latest', { params: { limit } }).then(r => r.data)

export const fetchAnalyticsSummary = (days = 30) =>
  apiClient.get<AnalyticsSummary>('/api/analytics/summary', { params: { days } }).then(r => r.data)

export const fetchEquityCurve = (days = 30) =>
  apiClient.get<EquityCurve>('/api/analytics/equity-curve', { params: { days } }).then(r => r.data)

export const fetchBySymbol = (days = 30) =>
  apiClient.get<SymbolPnl[]>('/api/analytics/by-symbol', { params: { days } }).then(r => r.data)

export const fetchMonthlyTarget = () =>
  apiClient.get<MonthlyTarget>('/api/analytics/monthly-target').then(r => r.data)
