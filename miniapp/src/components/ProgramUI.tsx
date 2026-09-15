import type { ReactNode } from 'react'
import { Badge } from './Badge'
import { formatSymbol } from '../lib/formatters'
import { day, statusLabel } from '../lib/program'
import type { ResearchEvent, ResearchPortfolio } from '../api/types'

/** Общие элементы экранов программы: заголовок, карточка, строка «метка — значение», критерии, события, позиции. Помощники форматирования — lib/program.ts. */

export function PageHeader({ kicker, title, sub }: { kicker: string; title: string; sub?: string }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <p style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.35em', textTransform: 'uppercase', color: 'var(--cyan)', textShadow: '0 0 10px rgba(0,212,255,0.55)', marginBottom: 2 }}>
        {kicker}
      </p>
      <h1 style={{ fontSize: 20, fontWeight: 700, color: '#fff', margin: 0, lineHeight: 1.1 }}>{title}</h1>
      {sub && <p style={{ fontSize: 10, color: 'var(--text-dim)', fontFamily: 'monospace', marginTop: 3 }}>{sub}</p>}
    </div>
  )
}

export function Card({ title, status, children, accent }: { title: string; status?: string; children: ReactNode; accent?: string }) {
  const s = status ? statusLabel[status] : undefined
  return (
    <div className="fade-up" style={{ borderRadius: 16, padding: '14px 16px', background: 'var(--surface)', border: `1px solid ${accent ?? 'var(--border)'}` }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8, marginBottom: 10 }}>
        <p style={{ fontSize: 12, fontWeight: 700, color: '#fff', margin: 0, lineHeight: 1.3 }}>{title}</p>
        {s && <Badge label={s.text} variant={s.variant} />}
      </div>
      {children}
    </div>
  )
}

export function Row({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '4px 0', borderBottom: '1px solid rgba(78,96,112,0.15)' }}>
      <span style={{ fontSize: 10, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-dim)', flexShrink: 0 }}>{label}</span>
      <span style={{ fontSize: 12, fontFamily: 'monospace', fontWeight: 600, color: accent ?? 'var(--text)', textAlign: 'right' }}>{value}</span>
    </div>
  )
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div style={{ marginTop: 10 }}>
      <p style={{ fontSize: 9, letterSpacing: '0.2em', textTransform: 'uppercase', color: 'var(--text-dim)', marginBottom: 4 }}>{title}</p>
      {children}
    </div>
  )
}

export function Criteria({ items, notes }: { items: string[]; notes?: string[] }) {
  return (
    <Section title="Критерии">
      <ul style={{ margin: 0, paddingLeft: 16, fontSize: 11, color: 'var(--text)', lineHeight: 1.45 }}>
        {items.map((c) => <li key={c}>{c}</li>)}
        {notes?.map((n) => <li key={n} style={{ color: 'var(--text-dim)' }}>{n}</li>)}
      </ul>
    </Section>
  )
}

export function Events({ events }: { events: ResearchEvent[] }) {
  if (!events.length) return null
  return (
    <Section title="События">
      {events.slice(0, 5).map((e, i) => (
        <p key={`${e.at}-${i}`} style={{ fontSize: 10, fontFamily: 'monospace', color: 'var(--text-dim)', margin: '2px 0' }}>
          {day(e.at)} · <span style={{ color: e.kind === 'error' || e.kind === 'halt' ? 'var(--red)' : 'var(--text)' }}>{e.kind}</span>{e.detail ? ` · ${e.detail}` : ''}
        </p>
      ))}
    </Section>
  )
}

export function PositionsTable({ positions }: { positions: ResearchPortfolio['positions'] }) {
  if (!positions.length) return <p style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 8 }}>Позиций нет.</p>
  const longs = positions.filter((p) => p.side === 'LONG')
  const shorts = positions.filter((p) => p.side === 'SHORT')
  return (
    <Section title={`Позиции — ${positions.length} (лонг ${longs.length}, шорт ${shorts.length})`}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr auto auto', gap: '3px 10px', fontFamily: 'monospace', fontSize: 11 }}>
        {[...longs, ...shorts].map((x) => (
          <PositionRow key={x.symbol} symbol={x.symbol} side={x.side} notional={x.notional} weight={x.weight} />
        ))}
      </div>
    </Section>
  )
}

function PositionRow({ symbol, side, notional, weight }: { symbol: string; side: 'LONG' | 'SHORT'; notional: number; weight: number }) {
  const color = side === 'LONG' ? 'var(--green)' : 'var(--red)'
  return (
    <>
      <span style={{ color: '#fff', fontWeight: 700 }}>{formatSymbol(symbol)}</span>
      <span style={{ color, fontWeight: 700 }}>{side === 'LONG' ? 'ЛОНГ' : 'ШОРТ'}</span>
      <span style={{ color: 'var(--text)', textAlign: 'right' }}>{notional.toFixed(0)} $ · {(weight * 100).toFixed(1)} %</span>
    </>
  )
}

/** Плитка сводки на обзоре: заголовок, статус, две-три цифры. */
export function SummaryTile({ title, status, rows, to }: { title: string; status?: string; rows: { label: string; value: string; accent?: string }[]; to?: string }) {
  const s = status ? statusLabel[status] : undefined
  const body = (
    <div style={{ borderRadius: 14, padding: '12px 14px', background: 'var(--surface)', border: '1px solid var(--border)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <p style={{ fontSize: 11, fontWeight: 700, color: '#fff', margin: 0 }}>{title}</p>
        {s && <Badge label={s.text} variant={s.variant} />}
      </div>
      {rows.map((r) => <Row key={r.label} label={r.label} value={r.value} accent={r.accent} />)}
    </div>
  )
  return to ? <a href={to} style={{ textDecoration: 'none', color: 'inherit' }}>{body}</a> : body
}
