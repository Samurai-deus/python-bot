import { useSearchParams } from 'react-router-dom'
import { useResearch } from '../../hooks/useResearch'
import { LoadingSpinner } from '../../components/LoadingSpinner'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Card, Row, Criteria, Events, PositionsTable, PageHeader, Section } from '../../components/ProgramUI'
import { usd, signed, pctOf, day, ruDate } from '../../lib/program'
import { formatSymbol } from '../../lib/formatters'
import type { ResearchCarry, ResearchOverview, ResearchPortfolio } from '../../api/types'

type Tab = 'portfolio' | 'btcalts' | 'carry'
const TABS: { id: Tab; label: string }[] = [
  { id: 'portfolio', label: 'И14 портфель' },
  { id: 'btcalts', label: 'И18 BTC/альты' },
  { id: 'carry', label: 'И13 фандинг' },
]

/** Портфели: три исполнителя на демо — позиции, размеры, сроки, критерии. */
export function Portfolios() {
  const { data, isLoading, error } = useResearch()
  const [params, setParams] = useSearchParams()
  const tab = (TABS.some((t) => t.id === params.get('tab')) ? params.get('tab') : 'portfolio') as Tab
  return (
    <div className="grid-bg" style={{ padding: '16px 16px 0', minHeight: '100dvh' }}>
      <PageHeader kicker="Демо-счета Bybit" title="Портфели" />
      <div style={{ display: 'flex', marginBottom: 14, padding: 3, borderRadius: 14, background: 'rgba(10,17,32,0.7)', border: '1px solid var(--border)', gap: 3 }}>
        {TABS.map((t) => (
          <button key={t.id} onClick={() => setParams({ tab: t.id })} style={{
            flex: 1, padding: '8px 0', borderRadius: 11, fontSize: 10, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase',
            border: tab === t.id ? '1px solid rgba(0,212,255,0.22)' : '1px solid transparent',
            background: tab === t.id ? 'rgba(0,212,255,0.1)' : 'transparent',
            color: tab === t.id ? 'var(--cyan)' : 'var(--text-dim)', cursor: 'pointer',
          }}>{t.label}</button>
        ))}
      </div>
      {isLoading && <LoadingSpinner />}
      {error && <ErrorBanner message={error.message} />}
      {data && tab === 'portfolio' && <PortfolioCard p={data.portfolio} meta={data.program.portfolio} />}
      {data && tab === 'btcalts' && <PortfolioCard p={data.btcalts} meta={data.program.btcalts} />}
      {data && tab === 'carry' && <CarryCard c={data.carry} meta={data.program.carry} />}
    </div>
  )
}

function PortfolioCard({ p, meta }: { p: ResearchPortfolio | null; meta: ResearchOverview['program']['portfolio'] }) {
  return (
    <div style={{ paddingBottom: 16 }}>
      <Card title={meta.title} status={p?.status}>
        {!p && <p style={{ fontSize: 11, color: 'var(--text-dim)' }}>Исполнитель ещё не запускался.</p>}
        {p && (
          <>
            <Row label="Капитал" value={usd(p.capital, 0)} />
            <Row label="Стоимость / старт" value={`${usd(p.equity)} / ${usd(p.start_equity)}`} />
            <Row label="Результат" value={`${signed(p.change)}${pctOf(p.change, p.capital)}`} accent={(p.change ?? 0) >= 0 ? 'var(--green)' : 'var(--red)'} />
            <Row label="Просадка от пика" value={`${usd(p.drawdown)}${pctOf(-p.drawdown, p.capital)} · стоп при 25 %`} accent={p.drawdown > p.capital * 0.15 ? 'var(--amber)' : undefined} />
            <Row label="Валовая экспозиция" value={`${usd(p.gross, 0)} (${(p.gross / p.capital).toFixed(2)}× капитала)`} />
            <Row label="Ребалансировка" value={`${meta.rebalance}; следующая ${day(p.next_rebalance)}`} />
            <Row label="Срок" value={`${ruDate(meta.start)} → ${ruDate(meta.end)}, итог ${ruDate(meta.verdict)}`} />
            <Row label="Риск и размер" value={meta.risk} />
            {p.halted_reason && <Row label="Причина остановки" value={p.halted_reason} accent="var(--red)" />}
            <PositionsTable positions={p.positions} />
            {p.rebalances.length > 0 && (
              <Section title="Ребалансировки">
                {p.rebalances.map((r) => (
                  <p key={`${r.t}`} style={{ fontSize: 10, fontFamily: 'monospace', color: 'var(--text-dim)', margin: '2px 0' }}>
                    {day(r.t)} — монет {r.coins}, ордеров {r.orders}{r.failed ? <span style={{ color: 'var(--amber)' }}>, не прошло {r.failed}</span> : ''}{(r.runs ?? 1) > 1 ? `, прогонов ${r.runs}` : ''}
                  </p>
                ))}
              </Section>
            )}
            <Criteria items={meta.criteria} notes={meta.notes} />
            <Events events={p.events} />
          </>
        )}
      </Card>
    </div>
  )
}

function CarryCard({ c, meta }: { c: ResearchCarry | null; meta: ResearchOverview['program']['carry'] }) {
  return (
    <div style={{ paddingBottom: 16 }}>
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
            <Row label="Срок" value={`${ruDate(meta.start)} → ${ruDate(meta.end)}, итог ${ruDate(meta.verdict)}`} />
            {c.halted_reason && <Row label="Причина остановки" value={c.halted_reason} accent="var(--red)" />}
            {c.positions.length > 0 && (
              <Section title="Пары спот + шорт">
                <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: '3px 10px', fontFamily: 'monospace', fontSize: 11 }}>
                  {c.positions.map((x) => (
                    <CarryRow key={x.symbol} x={x} />
                  ))}
                </div>
              </Section>
            )}
            <Criteria items={meta.criteria} />
            <Events events={c.events} />
          </>
        )}
      </Card>
    </div>
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
