import { useResearch } from '../../hooks/useResearch'
import { LoadingSpinner } from '../../components/LoadingSpinner'
import { ErrorBanner } from '../../components/ErrorBanner'
import { PageHeader } from '../../components/ProgramUI'
import { ruDate } from '../../lib/program'

/** Календарь контрольных дат плана: те же даты, что в уведомлениях Telegram (в день с 08:00 UTC и за 3 дня). */
export function Calendar() {
  const { data, isLoading, error } = useResearch()
  return (
    <div className="grid-bg" style={{ padding: '16px 16px 0', minHeight: '100dvh' }}>
      <PageHeader kicker="Проверки и итоги" title="Календарь" sub="уведомления в Telegram — в день события и за 3 дня" />
      {isLoading && <LoadingSpinner />}
      {error && <ErrorBanner message={error.message} />}
      {data && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, paddingBottom: 16 }}>
          {data.calendar.map((c) => {
            const past = c.days_left < 0
            const soon = c.days_left >= 0 && c.days_left <= 7
            return (
              <div key={c.date} style={{
                borderRadius: 14, padding: '10px 14px', background: 'var(--surface)',
                border: `1px solid ${soon ? 'rgba(255,170,0,0.4)' : 'var(--border)'}`, opacity: past ? 0.5 : 1,
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
                  <span style={{ fontFamily: 'monospace', fontSize: 12, fontWeight: 700, color: past ? 'var(--text-dim)' : soon ? 'var(--amber)' : 'var(--cyan)' }}>{ruDate(c.date)}</span>
                  <span style={{ fontSize: 10, color: 'var(--text-dim)', letterSpacing: '0.08em' }}>
                    {past ? `${-c.days_left} дн. назад` : c.days_left === 0 ? 'сегодня' : `через ${c.days_left} дн.`}
                  </span>
                </div>
                <p style={{ fontSize: 11, color: 'var(--text)', margin: 0, lineHeight: 1.4 }}>{c.text}</p>
              </div>
            )
          })}
          <p style={{ fontSize: 10, color: 'var(--text-dim)', lineHeight: 1.5 }}>
            Еженедельно, без отдельных дат: ребалансировки И14 и И18 в понедельник 00:02 UTC (сообщение по каждой), сводка И13 в понедельник 01:00 UTC.
          </p>
        </div>
      )}
    </div>
  )
}
