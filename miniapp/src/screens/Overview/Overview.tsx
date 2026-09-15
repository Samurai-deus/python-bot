import { Link } from 'react-router-dom'
import { useResearch } from '../../hooks/useResearch'
import { LoadingSpinner } from '../../components/LoadingSpinner'
import { ErrorBanner } from '../../components/ErrorBanner'
import { PageHeader, SummaryTile, Row, Section } from '../../components/ProgramUI'
import { usd, signed, pctOf, day, ruDate } from '../../lib/program'
import { formatDate } from '../../lib/formatters'
import type { ResearchOverview, ResearchPortfolio } from '../../api/types'

/** Обзор: что работает сейчас, сколько стоит, что дальше. Всё — по docs/TRADER_PLAN.md. */
export function Overview() {
  const { data, isLoading, error } = useResearch()
  return (
    <div className="grid-bg" style={{ padding: '16px 16px 0', minHeight: '100dvh' }}>
      <PageHeader kicker="Исследовательская программа" title="Обзор" sub={data ? `обновлено ${formatDate(data.generated_at)}` : undefined} />
      {isLoading && <LoadingSpinner />}
      {error && <ErrorBanner message={error.message} />}
      {data && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, paddingBottom: 16 }}>
          <ExecutorTile title="И14 · портфель (тренд + моментум + продолжение)" p={data.portfolio} />
          <ExecutorTile title="И18 · BTC против альтов (демо-субсчёт)" p={data.btcalts} />
          <CarryTile data={data} />
          <NextEvents data={data} />
          <DataTile data={data} />
          <p style={{ fontSize: 10, color: 'var(--text-dim)', lineHeight: 1.5, marginTop: 4 }}>
            Сигнальная торговля бота выключена по правилу И14 (14.09.2026). Все результаты — демо-счета Bybit; итоги судятся только по заранее записанным критериям в сроки из календаря.
          </p>
        </div>
      )}
    </div>
  )
}

function ExecutorTile({ title, p }: { title: string; p: ResearchPortfolio | null }) {
  if (!p) return <SummaryTile title={title} rows={[{ label: 'Состояние', value: 'исполнитель ещё не запускался' }]} />
  const change = p.change ?? 0
  return (
    <Link to="/portfolios" style={{ textDecoration: 'none', color: 'inherit' }}>
      <SummaryTile
        title={title}
        status={p.status}
        rows={[
          { label: 'Результат', value: `${signed(p.change)}${pctOf(p.change, p.capital)}`, accent: change >= 0 ? 'var(--green)' : 'var(--red)' },
          { label: 'Просадка от пика', value: `${(p.drawdown / p.capital * 100).toFixed(1)} % · стоп при 25 %`, accent: p.drawdown > p.capital * 0.15 ? 'var(--amber)' : undefined },
          { label: 'Позиции · валовая', value: `${p.positions.length} · ${(p.gross / p.capital).toFixed(2)}× капитала ${usd(p.capital, 0)}` },
          { label: 'Следующая ребалансировка', value: day(p.next_rebalance) },
        ]}
      />
    </Link>
  )
}

function CarryTile({ data }: { data: ResearchOverview }) {
  const c = data.carry
  if (!c) return <SummaryTile title="И13 · сбор фандинга (демо-субсчёт)" rows={[{ label: 'Состояние', value: 'исполнитель ещё не запускался' }]} />
  return (
    <Link to="/portfolios?tab=carry" style={{ textDecoration: 'none', color: 'inherit' }}>
      <SummaryTile
        title="И13 · сбор фандинга BTC + ETH (демо-субсчёт)"
        status={c.status}
        rows={[
          { label: 'Результат', value: signed(c.change), accent: (c.change ?? 0) >= 0 ? 'var(--green)' : 'var(--red)' },
          { label: 'Фандинг · комиссии', value: `${signed(c.funding)} · ${usd(c.fees)}` },
          { label: 'Хедж вне ±5 %', value: `${(c.outside_share * 100).toFixed(1)} % времени`, accent: c.outside_share > 0.01 ? 'var(--amber)' : undefined },
        ]}
      />
    </Link>
  )
}

function NextEvents({ data }: { data: ResearchOverview }) {
  const upcoming = data.calendar.filter((c) => c.days_left >= 0).slice(0, 3)
  return (
    <div style={{ borderRadius: 14, padding: '12px 14px', background: 'var(--surface)', border: '1px solid var(--border)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
        <p style={{ fontSize: 11, fontWeight: 700, color: '#fff', margin: 0 }}>Ближайшие контрольные даты</p>
        <Link to="/calendar" style={{ fontSize: 10, color: 'var(--cyan)', textDecoration: 'none', letterSpacing: '0.1em' }}>ВСЕ →</Link>
      </div>
      {upcoming.map((c) => (
        <div key={c.date} style={{ display: 'flex', gap: 10, padding: '4px 0', borderBottom: '1px solid rgba(78,96,112,0.15)' }}>
          <span style={{ fontFamily: 'monospace', fontSize: 11, color: c.days_left <= 3 ? 'var(--amber)' : 'var(--cyan)', flexShrink: 0, minWidth: 92 }}>
            {ruDate(c.date)} · {c.days_left === 0 ? 'сегодня' : `через ${c.days_left} дн.`}
          </span>
          <span style={{ fontSize: 11, color: 'var(--text)', lineHeight: 1.35 }}>{c.text}</span>
        </div>
      ))}
    </div>
  )
}

function DataTile({ data }: { data: ResearchOverview }) {
  const r = data.recorder
  const n = data.news
  return (
    <Link to="/data" style={{ textDecoration: 'none', color: 'inherit' }}>
      <div style={{ borderRadius: 14, padding: '12px 14px', background: 'var(--surface)', border: '1px solid var(--border)' }}>
        <p style={{ fontSize: 11, fontWeight: 700, color: '#fff', margin: '0 0 6px' }}>Сбор данных вперёд</p>
        <Row label="Стакан и сделки" value={r ? `${r.size_mb.toFixed(0)} МБ · разрывов ${r.gaps} · биржа ${r.last_message_age_s === null ? '—' : Math.round(r.last_message_age_s) + ' с назад'}` : 'нет данных'}
             accent={r && (r.gaps > 0 || (r.last_message_age_s ?? 0) > 120) ? 'var(--amber)' : undefined} />
        <Row label="Новости с оценкой ИИ" value={`${n.items} заголовков · сигналов ${n.signals} · ${n.spend_today === null ? '—' : n.spend_today.toFixed(2) + ' $'} сегодня`} />
        <Section title="Наблюдение на бумаге">
          <p style={{ fontSize: 11, color: 'var(--text)', margin: 0, lineHeight: 1.4 }}>{data.program.watch.title.replace('Наблюдение вперёд на бумаге: ', '')} — первая проверка {ruDate(data.program.watch.first_check)}</p>
        </Section>
      </div>
    </Link>
  )
}
