import { apiClient } from './client'
import type {
  SystemHealth, Balance, OpenPosition, TradeHistory,
  Signal, AnalyticsSummary, EquityCurve, SymbolPnl, PnlHistoryItem
} from './types'

export const fetchHealth = () =>
  apiClient.get<SystemHealth>('/api/system/health').then(r => r.data)

export const fetchBalance = () =>
  apiClient.get<Balance>('/api/balance').then(r => r.data)

export const fetchOpenPositions = () =>
  apiClient.get<OpenPosition[]>('/api/positions/open').then(r => r.data)

export const fetchPositionHistory = (days = 30) =>
  apiClient.get<TradeHistory[]>('/api/positions/history', { params: { days } }).then(r => r.data)

export const fetchLatestSignals = (limit = 20) =>
  apiClient.get<Signal[]>('/api/signals/latest', { params: { limit } }).then(r => r.data)

export const fetchSignalHistory = (symbol: string, days = 30) =>
  apiClient.get<Signal[]>('/api/signals/history', { params: { symbol, days } }).then(r => r.data)

export const fetchAnalyticsSummary = (days = 30) =>
  apiClient.get<AnalyticsSummary>('/api/analytics/summary', { params: { days } }).then(r => r.data)

export const fetchEquityCurve = (days = 30) =>
  apiClient.get<EquityCurve>('/api/analytics/equity-curve', { params: { days } }).then(r => r.data)

export const fetchBySymbol = (days = 30) =>
  apiClient.get<SymbolPnl[]>('/api/analytics/by-symbol', { params: { days } }).then(r => r.data)

export const fetchPnlHistory = (days = 30) =>
  apiClient.get<PnlHistoryItem[]>('/api/analytics/pnl-history', { params: { days } }).then(r => r.data)
