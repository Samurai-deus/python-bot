import { useState } from 'react'
import { useSystemStore } from '../../store/useSystemStore'
import { usePositionHistory } from '../../hooks/usePositions'
import { Badge } from '../../components/Badge'
import { LoadingSpinner } from '../../components/LoadingSpinner'
import { ErrorBanner } from '../../components/ErrorBanner'
import { formatUSDT, formatPnl, formatDate, formatSymbol } from '../../lib/formatters'
import type { OpenPosition, TradeHistory } from '../../api/types'

export function Positions() {
  const [tab, setTab] = useState<'open' | 'history'>('open')
  const { snapshot } = useSystemStore()
  const { data: history, isLoading, error } = usePositionHistory(30)

  return (
    <div className="px-4 pt-4">
      <h1 className="text-xl font-bold mb-4">Positions</h1>
      <div className="flex gap-2 mb-4">
        {(['open', 'history'] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-1.5 rounded-full text-sm font-medium transition-colors ${
              tab === t
                ? 'bg-[var(--tg-button)] text-[var(--tg-button-text)]'
                : 'bg-[var(--tg-secondary)] text-[var(--tg-hint)]'
            }`}
          >
            {t === 'open' ? 'Open' : 'History'}
          </button>
        ))}
      </div>

      {tab === 'open' ? (
        <OpenPositionsList positions={snapshot?.positions ?? []} />
      ) : (
        <>
          {isLoading && <LoadingSpinner />}
          {error && <ErrorBanner message={error.message} />}
          {history && <HistoryList items={history} />}
        </>
      )}
    </div>
  )
}

function OpenPositionsList({ positions }: { positions: OpenPosition[] }) {
  if (positions.length === 0) {
    return <p className="text-[var(--tg-hint)] text-center py-8">No open positions</p>
  }
  return (
    <div className="space-y-3">
      {positions.map((p) => (
        <div key={p.id} className="bg-[var(--tg-secondary)] rounded-2xl p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="font-semibold">{formatSymbol(p.symbol)}</span>
            <Badge label={p.side} variant={p.side === 'BUY' ? 'buy' : 'sell'} />
          </div>
          <div className="grid grid-cols-2 gap-2 text-sm">
            <InfoRow label="Entry" value={formatUSDT(p.entry_price)} />
            <InfoRow label="Qty" value={String(p.qty)} />
            <InfoRow label="SL" value={p.stop_loss ? formatUSDT(p.stop_loss) : '—'} />
            <InfoRow label="TP" value={p.take_profit ? formatUSDT(p.take_profit) : '—'} />
          </div>
          <p className="text-xs text-[var(--tg-hint)] mt-2">{formatDate(p.opened_at)}</p>
        </div>
      ))}
    </div>
  )
}

function HistoryList({ items }: { items: TradeHistory[] }) {
  if (items.length === 0) {
    return <p className="text-[var(--tg-hint)] text-center py-8">No history</p>
  }
  return (
    <div className="space-y-3">
      {items.map((t, i) => (
        <div key={i} className="bg-[var(--tg-secondary)] rounded-2xl p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="font-semibold">{formatSymbol(t.symbol)}</span>
            <span className={`font-semibold text-sm ${t.net_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {formatPnl(t.net_pnl)}
            </span>
          </div>
          <div className="grid grid-cols-2 gap-2 text-sm">
            <InfoRow label="Entry" value={formatUSDT(t.entry_price)} />
            <InfoRow label="Exit" value={formatUSDT(t.exit_price)} />
            <InfoRow label="Qty" value={String(t.quantity)} />
            <InfoRow label="Side" value={t.side} />
          </div>
          <p className="text-xs text-[var(--tg-hint)] mt-2">{formatDate(t.closed_at)}</p>
        </div>
      ))}
    </div>
  )
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-[var(--tg-hint)]">{label}</p>
      <p className="text-sm font-medium">{value}</p>
    </div>
  )
}
