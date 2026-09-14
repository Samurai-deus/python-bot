import type { ReactNode } from 'react'
import { useResearch } from '../../hooks/useResearch'
import { LoadingSpinner } from '../../components/LoadingSpinner'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Badge } from '../../components/Badge'
import { formatDate, formatSymbol } from '../../lib/formatters'
import type { ResearchCarry, ResearchEvent, ResearchNews, ResearchOverview, ResearchPortfolio, ResearchRecorder } from '../../api/types'

/** Экран «Программа»: что идёт вперёд по docs/TRADER_PLAN.md — позиции, размеры, сроки, критерии. */
export function Research() {
  const { data, isLoading, error } = useResearch()
  return (
    <div className="grid-bg" style={{ padding: '16px 16px 0', minHeight: '100dvh' }}>
      <div style={{ marginBottom: 16 }}>
        <p style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.35em', textTransform: 'uppercase', color: 'var(--cyan)', textShadow: '0 0 10px rgba(0,212,255,0.55)', marginBottom: 2 }}>
          TRADER PLAN
        </p>
        <h1 style={{ fontSize: 20, fontWeight: 700, color: '#fff', margin: 0 }}>Программа</h1>
        {data && <p style={{ fontSize: 10, color: 'var(--text-dim)', fontFamily: 'monospace', marginTop: 2 }}>обновлено {formatDate(data.generated_at)}</p>}
      </div>
      {isLoading && <LoadingSpinner />}
      {error && <ErrorBanner message={error.message} />}
      {data && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, paddingBottom: 16 }}>
          <PortfolioCard p={data.portfolio} meta={data.program.portfolio} />
          <CarryCard c={data.carry} meta={data.program.carry} />
          <DataCard recorder={data.recorder} news={data.news} meta={data.program} />
          <WatchCard meta={data.program.watch} />
        </div>
      )}
    </div>
  )
}

const usd = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined ? '—' : `${v >= 0 ? '' : '−'}${Math.abs(v).toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits })} $`
const signed = (v: number | null | undefined) => v === null || v === undefined ? '—' : `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(2)} $`
const pct = (v: number | null | undefined, base: number) => v === null || v === undefined || !base ? '' : ` (${v >= 0 ? '+' : '−'}${(Math.abs(v) / base * 100).toFixed(2)} %)`
const day = (iso: string | null | undefined) => (iso ? formatDate(iso) : '—')
const statusLabel: Record<string, { text: string; variant: 'buy' | 'sell' | 'neutral' }> = {
  running: { text: 'РАБОТАЕТ', variant: 'buy' },
  waiting: { text: 'ОЖИДАНИЕ', variant: 'neutral' },
  halted: { text: 'ОСТАНОВЛЕН', variant: 'sell' },
}

function Card({ title, status, children }: { title: string; status?: string; children: ReactNode }) {
  const s = status ? statusLabel[status] : undefined
  return (
    <div className="fade-up" style={{ borderRadius: 16, padding: '14px 16px', background: 'var(--surface)', border: '1px solid var(--border)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8, marginBottom: 10 }}>
        <p style={{ fontSize: 12, fontWeight: 700, color: '#fff', margin: 0, lineHeight: 1.3 }}>{title}</p>
        {s && <Badge label={s.text} variant={s.variant} />}
      </div>
      {children}
    </div>
  )
}

function Row({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '4px 0', borderBottom: '1px solid rgba(78,96,112,0.15)' }}>
      <span style={{ fontSize: 10, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-dim)' }}>{label}</span>
      <span style={{ fontSize: 12, fontFamily: 'monospace', fontWeight: 600, color: accent ?? 'var(--text)', textAlign: 'right' }}>{value}</span>
    </div>
  )
}

function Criteria({ items, notes }: { items: string[]; notes?: string[] }) {
  return (
    <div style={{ marginTop: 10 }}>
      <p style={{ fontSize: 9, letterSpacing: '0.2em', textTransform: 'uppercase', color: 'var(--text-dim)', marginBottom: 4 }}>Критерии</p>
      <ul style={{ margin: 0, paddingLeft: 16, fontSize: 11, color: 'var(--text)', lineHeight: 1.45 }}>
        {items.map((c) => <li key={c}>{c}</li>)}
        {notes?.map((n) => <li key={n} style={{ color: 'var(--text-dim)' }}>{n}</li>)}
      </ul>
    </div>
  )
}

function Events({ events }: { events: ResearchEvent[] }) {
  if (!events.length) return null
  return (
    <div style={{ marginTop: 10 }}>
      <p style={{ fontSize: 9, letterSpacing: '0.2em', textTransform: 'uppercase', color: 'var(--text-dim)', marginBottom: 4 }}>События</p>
      {events.slice(0, 5).map((e, i) => (
        <p key={`${e.at}-${i}`} style={{ fontSize: 10, fontFamily: 'monospace', color: 'var(--text-dim)', margin: '2px 0' }}>
          {day(e.at)} · <span style={{ color: e.kind === 'error' || e.kind === 'halt' ? 'var(--red)' : 'var(--text)' }}>{e.kind}</span>{e.detail ? ` · ${e.detail}` : ''}
        </p>
      ))}
    </div>
  )
}

function PortfolioCard({ p, meta }: { p: ResearchPortfolio | null; meta: ResearchOverview['program']['portfolio'] }) {
  return (
    <Card title={meta.title} status={p?.status}>
      {!p && <p style={{ fontSize: 11, color: 'var(--text-dim)' }}>Исполнитель ещё не запускался.</p>}
      {p && (
        <>
          <Row label="Капитал" value={usd(p.capital, 0)} />
          <Row label="Стоимость / старт" value={`${usd(p.equity)} / ${usd(p.start_equity)}`} />
          <Row label="Результат" value={`${signed(p.change)}${pct(p.change, p.capital)}`} accent={(p.change ?? 0) >= 0 ? 'var(--green)' : 'var(--red)'} />
          <Row label="Просадка от пика" value={`${usd(p.drawdown)}${pct(-p.drawdown, p.capital)} · стоп при 25 %`} accent={p.drawdown > p.capital * 0.15 ? 'var(--amber)' : undefined} />
          <Row label="Валовая экспозиция" value={`${usd(p.gross, 0)} (${(p.gross / p.capital).toFixed(2)}× капитала)`} />
          <Row label="Следующая ребалансировка" value={day(p.next_rebalance)} />
          <Row label="Срок" value={`${meta.start} → ${meta.end}, итог ${meta.verdict}`} />
          <Row label="Риск" value={meta.risk} />
          {p.halted_reason && <Row label="Причина остановки" value={p.halted_reason} accent="var(--red)" />}
          <Positions positions={p.positions} />
          {p.rebalances.length > 0 && (
            <p style={{ fontSize: 10, fontFamily: 'monospace', color: 'var(--text-dim)', marginTop: 8 }}>
              Ребалансировки: {p.rebalances.map((r) => `${day(r.t)} — монет ${r.coins}, ордеров ${r.orders}${r.failed ? `, не прошло ${r.failed}` : ''}`).join(' · ')}
            </p>
          )}
          <Criteria items={meta.criteria} notes={meta.notes} />
          <Events events={p.events} />
        </>
      )}
    </Card>
  )
}

function Positions({ positions }: { positions: ResearchPortfolio['positions'] }) {
  if (!positions.length) return <p style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 8 }}>Позиций нет.</p>
  return (
    <div style={{ marginTop: 10 }}>
      <p style={{ fontSize: 9, letterSpacing: '0.2em', textTransform: 'uppercase', color: 'var(--text-dim)', marginBottom: 4 }}>Позиции — {positions.length}</p>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr auto auto', gap: '3px 10px', fontFamily: 'monospace', fontSize: 11 }}>
        {positions.map((x) => (
          <PositionRow key={x.symbol} symbol={x.symbol} side={x.side} notional={x.notional} weight={x.weight} />
        ))}
      </div>
    </div>
  )
}

function PositionRow({ symbol, side, notional, weight }: { symbol: string; side: 'LONG' | 'SHORT'; notional: number; weight: number }) {
  const color = side === 'LONG' ? 'var(--green)' : 'var(--red)'
  return (
    <>
      <span style={{ color: '#fff', fontWeight: 700 }}>{formatSymbol(symbol)}</span>
      <span style={{ color, fontWeight: 700 }}>{side}</span>
      <span style={{ color: 'var(--text)', textAlign: 'right' }}>{notional.toFixed(0)} $ · {(weight * 100).toFixed(1)} %</span>
    </>
  )
}

function CarryCard({ c, meta }: { c: ResearchCarry | null; meta: ResearchOverview['program']['carry'] }) {
  return (
    <Card title={meta.title} status={c?.status}>
      {!c && <p style={{ fontSize: 11, color: 'var(--text-dim)' }}>Исполнитель ещё не запускался.</p>}
      {c && (
        <>
          <Row label="Позиция" value={meta.notional} />
          <Row label="Стоимость / старт" value={`${usd(c.equity)} / ${usd(c.start_equity)}`} />
          <Row label="Результат" value={signed(c.change)} accent={(c.change ?? 0) >= 0 ? 'var(--green)' : 'var(--red)'} />
          <Row label="Фандинг получен" value={signed(c.funding)} accent="var(--green)" />
          <Row label="Комиссии" value={usd(c.fees)} />
          <Row label="Просадка от пика" value={usd(c.drawdown)} />
          <Row label="Маржа (mm rate)" value={c.mm_rate === null ? '—' : `${(c.mm_rate * 100).toFixed(2)} % · стоп при 66,7 %`} />
          <Row label="Хедж вне ±5 %" value={`${(c.outside_share * 100).toFixed(2)} % времени`} />
          <Row label="Срок" value={`${meta.start} → ${meta.end}, итог ${meta.verdict}`} />
          {c.halted_reason && <Row label="Причина остановки" value={c.halted_reason} accent="var(--red)" />}
          {c.positions.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <p style={{ fontSize: 9, letterSpacing: '0.2em', textTransform: 'uppercase', color: 'var(--text-dim)', marginBottom: 4 }}>Пары</p>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: '3px 10px', fontFamily: 'monospace', fontSize: 11 }}>
                {c.positions.map((x) => (
                  <CarryRow key={x.symbol} x={x} />
                ))}
              </div>
            </div>
          )}
          <Criteria items={meta.criteria} />
          <Events events={c.events} />
        </>
      )}
    </Card>
  )
}

function CarryRow({ x }: { x: ResearchCarry['positions'][number] }) {
  return (
    <>
      <span style={{ color: '#fff', fontWeight: 700 }}>{formatSymbol(x.symbol)}</span>
      <span style={{ color: 'var(--text)', textAlign: 'right' }}>
        спот {x.spot} / шорт {x.short} · {x.notional.toFixed(0)} $ · расх. {x.deviation === null ? '—' : `${(x.deviation * 100).toFixed(2)} %`}
      </span>
    </>
  )
}

function DataCard({ recorder, news, meta }: { recorder: ResearchRecorder | null; news: ResearchNews; meta: ResearchOverview['program'] }) {
  const fresh = Object.entries(news.fresh)
  const blind = Object.entries(news.blind ?? {})
  return (
    <Card title="Сбор данных вперёд">
      <p style={{ fontSize: 11, fontWeight: 700, color: 'var(--cyan)', margin: '4px 0' }}>{meta.recorder.title}</p>
      {!recorder && <p style={{ fontSize: 11, color: 'var(--text-dim)' }}>Данных нет.</p>}
      {recorder && (
        <>
          <Row label="Пишется с" value={day(recorder.since)} />
          <Row label="Символы" value={meta.recorder.symbols} />
          <Row label="Объём" value={`${recorder.size_mb.toFixed(0)} МБ · ${recorder.mb_per_day === null ? '—' : recorder.mb_per_day.toFixed(0)} МБ/сут, предел ${meta.recorder.cap_gb} ГБ`} />
          <Row label="Разрывов стакана" value={String(recorder.gaps)} accent={recorder.gaps ? 'var(--amber)' : 'var(--green)'} />
          <Row label="Переподключений" value={String(recorder.events.disconnect ?? 0)} />
          <Row label="Последнее сообщение биржи" value={recorder.last_message_age_s === null ? '—' : `${Math.round(recorder.last_message_age_s)} с назад`} accent={(recorder.last_message_age_s ?? 0) > 120 ? 'var(--red)' : undefined} />
          <Row label="Гипотезы на этих данных" value={`с ${meta.recorder.hypotheses_from}`} />
        </>
      )}
      <p style={{ fontSize: 11, fontWeight: 700, color: 'var(--cyan)', margin: '12px 0 4px' }}>{meta.news.title}</p>
      <Row label="Заголовков всего" value={String(news.items)} />
      <Row label="Оценено (свежих)" value={fresh.length ? fresh.map(([k, v]) => `${k}: ${v}`).join(', ') : '0'} />
      <Row label="Сигналов по правилу" value={`${news.signals} из ${news.score_rows} оценок`} />
      <Row label="Без названия монеты (И10б)" value={blind.length ? blind.map(([k, v]) => `${k}: ${v}`).join(', ') : '0'} />
      <Row label="Расход сегодня" value={news.spend_today === null ? '—' : `${news.spend_today.toFixed(3)} $ из ${meta.news.budget_usd} $`} />
      <Row label="Сигнал" value={meta.news.signal} />
      <Row label="Первая проверка" value={meta.news.first_check} />
    </Card>
  )
}

function WatchCard({ meta }: { meta: ResearchOverview['program']['watch'] }) {
  return (
    <Card title={meta.title}>
      <Row label="Идёт с" value={meta.start} />
      <Row label="Проверки" value={`каждые ${meta.every_weeks} недель, первая ${meta.first_check}`} />
      <Row label="Остановка" value={meta.stop} />
      <p style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 8, lineHeight: 1.4 }}>{meta.note}</p>
    </Card>
  )
}
