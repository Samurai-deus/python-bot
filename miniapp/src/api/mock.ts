import type { ResearchOverview, SystemHealth } from './types'
import overviewFixture from './fixtures/overview.json'
import healthFixture from './fixtures/health.json'

/**
 * Режим фикстур (техдолг п. 8, 15.09.2026): `npm run dev:mock` (vite --mode mock; флаг VITE_MOCK
 * ставит vite.config.ts) — экраны с данными из fixtures/*.json, без API и без Telegram. Только dev-сервер: в production-сборке
 * DEV = false и флаг не читается, поэтому обхода авторизации на проде нет.
 * overview.json — живой снимок /api/research/overview с демо-счетов 15.09.2026.
 */
export function mockEnabled(env: { DEV: boolean; VITE_MOCK?: string }): boolean {
  return env.DEV && env.VITE_MOCK === '1'
}

// Записано статически (не через mockEnabled): в production-сборке выражение сворачивается в false,
// и сборщик выбрасывает ветку вместе с fixtures/*.json — в бандле на проде фикстур нет.
export const MOCK = import.meta.env.DEV && import.meta.env.VITE_MOCK === '1'

/** Снимок помечается текущим временем, чтобы «обновлено … назад» не показывало дату снимка. */
export function fixtureOverview(now = new Date()): ResearchOverview {
  return { ...(overviewFixture as ResearchOverview), generated_at: now.toISOString() }
}

export function fixtureHealth(now = new Date()): SystemHealth {
  return { ...(healthFixture as SystemHealth), timestamp: now.toISOString() }
}
