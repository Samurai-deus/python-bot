import { useSystemHealth } from '../../hooks/useSystemHealth'
import { useResearch } from '../../hooks/useResearch'
import { useSystemStore } from '../../store/useSystemStore'
import { LoadingSpinner } from '../../components/LoadingSpinner'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Badge } from '../../components/Badge'
import { Card, Row, PageHeader } from '../../components/ProgramUI'
import { formatDate, parseUTC } from '../../lib/formatters'

const WS_LABEL: Record<string, string> = { connected: 'подключён', connecting: 'подключение', reconnecting: 'переподключение', disconnected: 'нет связи' }

function ageText(iso: string | null | undefined, now: number): { text: string; stale: boolean } {
  if (!iso) return { text: 'нет данных', stale: true }
  const min = Math.round((now - parseUTC(iso).getTime()) / 60_000)
  return { text: min < 1 ? 'только что' : `${min} мин назад`, stale: min > 90 }
}

/** Система: здоровье бота и исполнителей (по свежести их снимков), связь мини-аппа. */
export function System() {
  const { data: health, isLoading, error } = useSystemHealth()
  const { data: research } = useResearch()
  const { wsStatus } = useSystemStore()
  // «Сейчас» — время ответа сервера, а не Date.now() в рендере (правило чистоты React); ответ обновляется раз в минуту.
  const now = research ? parseUTC(research.generated_at).getTime() : 0
  const rows = research ? [
    { name: 'И14 портфель', ...ageText(research.portfolio?.snapshot_at, now) },
    { name: 'И18 BTC/альты', ...ageText(research.btcalts?.snapshot_at, now) },
    { name: 'И13 фандинг', ...ageText(research.carry?.snapshot_at, now) },
    { name: 'Запись стакана', text: research.recorder?.last_message_age_s === null || research.recorder?.last_message_age_s === undefined ? 'нет данных' : `биржа ${Math.round(research.recorder.last_message_age_s)} с назад`, stale: (research.recorder?.last_message_age_s ?? 9999) > 120 },
  ] : []
  return (
    <div className="grid-bg" style={{ padding: '16px 16px 0', minHeight: '100dvh' }}>
      <PageHeader kicker="Диагностика" title="Система" />
      {isLoading && <LoadingSpinner />}
      {error && <ErrorBanner message={error.message} />}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, paddingBottom: 16 }}>
        {research && (
          <Card title="Исполнители программы (свежесть снимков)">
            {rows.map((r) => <Row key={r.name} label={r.name} value={r.text} accent={r.stale ? 'var(--amber)' : 'var(--green)'} />)}
            <p style={{ fontSize: 10, color: 'var(--text-dim)', marginTop: 8, lineHeight: 1.4 }}>Снимки — раз в час; старше 90 минут — повод посмотреть контейнер. За перезапуски отвечает сторожевой таймер хоста.</p>
          </Card>
        )}
        {health && (
          <Card title="Бот (сигнальная торговля выключена по правилу И14)">
            <Row label="Состояние" value={health.state} accent={health.state === 'RUNNING' ? 'var(--green)' : 'var(--amber)'} />
            <Row label="Торговля" value={health.trading_paused ? 'на паузе' : 'активна (только учёт, сигналы отключены)'} />
            <Row label="Режим" value={(health.trading_mode ?? 'DRY_RUN').replace('_', ' ')} />
            <Row label="Ошибок подряд" value={String(health.consecutive_errors)} accent={health.consecutive_errors > 0 ? 'var(--amber)' : 'var(--green)'} />
            <Row label="Последнее обновление" value={formatDate(health.timestamp)} />
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}>
              <span style={{ fontSize: 10, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-dim)' }}>Связь мини-аппа</span>
              <Badge label={WS_LABEL[wsStatus] ?? wsStatus} variant={wsStatus === 'connected' ? 'running' : wsStatus === 'disconnected' ? 'halt' : 'degraded'} />
            </div>
          </Card>
        )}
        <p style={{ fontSize: 10, color: 'var(--text-dim)', lineHeight: 1.5 }}>Управление — только командами Telegram-бота; мини-апп ничего не меняет.</p>
      </div>
    </div>
  )
}
