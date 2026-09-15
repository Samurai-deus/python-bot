import { apiClient } from './client'
import { MOCK, fixtureHealth, fixtureOverview } from './mock'
import type { ResearchOverview, SystemHealth } from './types'

/** Мини-апп v2 читает два запроса: здоровье бота и обзор исследовательской программы; в режиме фикстур (mock.ts) — из JSON. */

export const fetchHealth = () =>
  MOCK ? Promise.resolve(fixtureHealth()) : apiClient.get<SystemHealth>('/api/system/health').then(r => r.data)

export const fetchResearchOverview = () =>
  MOCK ? Promise.resolve(fixtureOverview()) : apiClient.get<ResearchOverview>('/api/research/overview').then(r => r.data)
