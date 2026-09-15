import { useResearch } from '../../hooks/useResearch'
import { LoadingSpinner } from '../../components/LoadingSpinner'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Card, Row, PageHeader } from '../../components/ProgramUI'
import { day, ruDate } from '../../lib/program'

/** Данные: что копится вперёд (стакан, новости) и что наблюдается на бумаге. */
export function Data() {
  const { data, isLoading, error } = useResearch()
  return (
    <div className="grid-bg" style={{ padding: '16px 16px 0', minHeight: '100dvh' }}>
      <PageHeader kicker="Копится вперёд" title="Данные и наблюдения" />
      {isLoading && <LoadingSpinner />}
      {error && <ErrorBanner message={error.message} />}
      {data && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, paddingBottom: 16 }}>
          <Card title={data.program.recorder.title} accent={data.recorder && (data.recorder.gaps > 0 || (data.recorder.last_message_age_s ?? 0) > 120) ? 'rgba(255,170,0,0.4)' : undefined}>
            {!data.recorder && <p style={{ fontSize: 11, color: 'var(--text-dim)' }}>Данных нет.</p>}
            {data.recorder && (
              <>
                <Row label="Пишется с" value={day(data.recorder.since)} />
                <Row label="Символы" value={data.program.recorder.symbols} />
                <Row label="Объём" value={`${data.recorder.size_mb.toFixed(0)} МБ · ${data.recorder.mb_per_day === null ? '—' : data.recorder.mb_per_day.toFixed(0)} МБ/сут, предел ${data.program.recorder.cap_gb} ГБ`} />
                <Row label="Разрывов стакана" value={String(data.recorder.gaps)} accent={data.recorder.gaps ? 'var(--amber)' : 'var(--green)'} />
                <Row label="Переподключений" value={String(data.recorder.events.disconnect ?? 0)} />
                <Row label="Последнее сообщение биржи" value={data.recorder.last_message_age_s === null ? '—' : `${Math.round(data.recorder.last_message_age_s)} с назад`} accent={(data.recorder.last_message_age_s ?? 0) > 120 ? 'var(--red)' : undefined} />
                <Row label="Гипотезы на этих данных" value={`с ${ruDate(data.program.recorder.hypotheses_from)}`} />
              </>
            )}
          </Card>
          <Card title={data.program.news.title}>
            <Row label="Заголовков всего" value={String(data.news.items)} />
            <Row label="Оценено (свежих)" value={Object.entries(data.news.fresh).map(([k, v]) => `${k}: ${v}`).join(', ') || '0'} />
            <Row label="Сигналов по правилу" value={`${data.news.signals} из ${data.news.score_rows} оценок`} />
            <Row label="Без названия монеты (И10б)" value={Object.entries(data.news.blind ?? {}).map(([k, v]) => `${k}: ${v}`).join(', ') || '0'} />
            <Row label="Расход сегодня" value={data.news.spend_today === null ? '—' : `${data.news.spend_today.toFixed(3)} $ из ${data.program.news.budget_usd} $`} />
            <Row label="Сигнал" value={data.program.news.signal} />
            <Row label="Первая проверка" value={ruDate(data.program.news.first_check)} />
          </Card>
          <Card title={data.program.watch.title}>
            <Row label="Идёт с" value={ruDate(data.program.watch.start)} />
            <Row label="Проверки" value={`каждые ${data.program.watch.every_weeks} недель, первая ${ruDate(data.program.watch.first_check)}`} />
            <Row label="Остановка" value={data.program.watch.stop} />
            <p style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 8, lineHeight: 1.4 }}>{data.program.watch.note}</p>
          </Card>
        </div>
      )}
    </div>
  )
}
