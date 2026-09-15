import { apiClient } from './client'
import type { ResearchOverview, SystemHealth } from './types'

/** Мини-апп v2 читает два запроса: здоровье бота и обзор исследовательской программы. */

export const fetchHealth = () =>
  apiClient.get<SystemHealth>('/api/system/health').then(r => r.data)

export const fetchResearchOverview = () =>
  apiClient.get<ResearchOverview>('/api/research/overview').then(r => r.data)
