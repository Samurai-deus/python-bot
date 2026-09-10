import type { WsSnapshot } from '../api/types'

const MAX_POSITIONS = 200

/**
 * Снимок из WebSocket — или null, если сообщение на снимок не похоже.
 *
 * balance_usdt может быть null: сервер отдаёт null, когда баланс не успел
 * посчитаться. Раньше такой снимок отбрасывался целиком — вместе с позициями.
 */
export function parseWsSnapshot(data: unknown): WsSnapshot | null {
  if (!data || typeof data !== 'object') return null
  const d = data as Record<string, unknown>

  const balanceOk =
    d.balance_usdt === null || (typeof d.balance_usdt === 'number' && Number.isFinite(d.balance_usdt))
  if (
    typeof d.timestamp !== 'string' ||
    typeof d.system_state !== 'string' ||
    typeof d.trading_paused !== 'boolean' ||
    !balanceOk ||
    !Array.isArray(d.positions) ||
    d.positions.length > MAX_POSITIONS
  ) {
    return null
  }

  const positionsOk = d.positions.every((p) => {
    if (!p || typeof p !== 'object') return false
    const q = p as Record<string, unknown>
    return (
      typeof q.symbol === 'string' &&
      typeof q.side === 'string' &&
      typeof q.entry_price === 'number' &&
      Number.isFinite(q.entry_price)
    )
  })
  return positionsOk ? (d as unknown as WsSnapshot) : null
}
