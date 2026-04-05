import { useRef, useEffect } from 'react'
import { createChart, AreaSeries, ColorType } from 'lightweight-charts'
import type { Time } from 'lightweight-charts'
import { useSettingsStore } from '../../store/useSettingsStore'
import { useAnalyticsSummary, useEquityCurve, useBySymbol } from '../../hooks/useAnalytics'
import { LoadingSpinner } from '../../components/LoadingSpinner'
import { ErrorBanner } from '../../components/ErrorBanner'
import { formatPnl, formatPct, formatSymbol } from '../../lib/formatters'
import { logger } from '../../lib/logger'

const DAYS_OPTIONS = [7, 30, 90]

export function Analytics() {
  const { analyticsDays, setAnalyticsDays } = useSettingsStore()
  const { data: summary, isLoading: loadingSum, error: errSum } = useAnalyticsSummary(analyticsDays)
  const { data: equity, isLoading: loadingEq, error: errEq } = useEquityCurve(analyticsDays)
  const { data: bySymbol, isLoading: loadingSym, error: errSym } = useBySymbol(analyticsDays)

  return (
    <div className="grid-bg" style={{ padding: '16px 16px 0', minHeight: '100dvh' }}>

      {/* Header + period selector */}
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <p style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.35em', textTransform: 'uppercase', color: 'var(--cyan)', textShadow: '0 0 10px rgba(0,212,255,0.55)', marginBottom: 2 }}>
            PERFORMANCE
          </p>
          <h1 style={{ fontSize: 20, fontWeight: 700, color: '#fff', margin: 0 }}>Analytics</h1>
        </div>
        <div style={{
          display: 'flex',
          padding: 3,
          borderRadius: 12,
          background: 'rgba(10,17,32,0.7)',
          border: '1px solid var(--border)',
          gap: 2,
        }}>
          {DAYS_OPTIONS.map(d => (
            <button
              key={d}
              onClick={() => setAnalyticsDays(d)}
              style={{
                padding: '5px 10px',
                borderRadius: 8,
                fontSize: 11,
                fontWeight: 700,
                letterSpacing: '0.08em',
                border: analyticsDays === d ? '1px solid rgba(0,212,255,0.22)' : '1px solid transparent',
                background: analyticsDays === d ? 'rgba(0,212,255,0.12)' : 'transparent',
                color: analyticsDays === d ? 'var(--cyan)' : 'var(--text-dim)',
                textShadow: analyticsDays === d ? '0 0 8px rgba(0,212,255,0.5)' : 'none',
                cursor: 'pointer',
                transition: 'all 0.2s',
              }}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>

      {errSum && <ErrorBanner message={errSum.message} />}
      {errEq && <ErrorBanner message={errEq.message} />}

      {/* Equity curve */}
      <div style={{
        borderRadius: 16,
        padding: '14px 16px',
        marginBottom: 12,
        background: 'var(--surface)',
        border: '1px solid var(--border)',
      }}>
        <p style={{ fontSize: 9, letterSpacing: '0.28em', textTransform: 'uppercase', color: 'var(--text-dim)', marginBottom: 10 }}>
          Equity Curve
        </p>
        {loadingEq ? <LoadingSpinner /> : equity && <EquityCurveChart data={equity} />}
      </div>

      {/* Summary stats */}
      {loadingSum ? <LoadingSpinner /> : summary && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 12 }}>
          <StatCard label="Win Rate"     value={formatPct(summary.win_rate)}
            color={summary.win_rate >= 50 ? 'var(--green)' : 'var(--red)'} />
          <StatCard label="Max Drawdown" value={formatPct(summary.max_drawdown_pct)}
            color="var(--red)" />
          <StatCard label="Profit Factor"
            value={summary.profit_factor != null && isFinite(summary.profit_factor) ? summary.profit_factor.toFixed(2) : '—'}
            color={summary.profit_factor != null && isFinite(summary.profit_factor) && summary.profit_factor >= 1 ? 'var(--green)' : 'var(--red)'} />
          <StatCard label="Sharpe Ratio"
            value={summary.sharpe_ratio != null && isFinite(summary.sharpe_ratio) ? summary.sharpe_ratio.toFixed(2) : '—'}
            color="var(--cyan)" />
          <StatCard label="Net P&L"      value={formatPnl(summary.net_pnl)}
            color={summary.net_pnl >= 0 ? 'var(--green)' : 'var(--red)'} />
          <StatCard label="Total Trades" value={String(summary.total_trades)}
            color="var(--text)" />
        </div>
      )}

      {/* By symbol table */}
      <div style={{
        borderRadius: 16,
        padding: '14px 16px',
        marginBottom: 16,
        background: 'var(--surface)',
        border: '1px solid var(--border)',
      }}>
        <p style={{ fontSize: 9, letterSpacing: '0.28em', textTransform: 'uppercase', color: 'var(--text-dim)', marginBottom: 10 }}>
          By Symbol
        </p>
        {loadingSym && <LoadingSpinner />}
        {errSym && <ErrorBanner message={errSym.message} />}
        {bySymbol && (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                {['Symbol', 'Trades', 'Win%', 'P&L'].map((h, j) => (
                  <th key={h} style={{
                    textAlign: j === 0 ? 'left' : 'right',
                    paddingBottom: 8,
                    fontSize: 9,
                    letterSpacing: '0.2em',
                    textTransform: 'uppercase',
                    color: 'var(--text-dim)',
                    fontWeight: 600,
                  }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {bySymbol.map((row) => (
                <tr key={row.symbol} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '7px 0', fontFamily: 'monospace', fontSize: 12, fontWeight: 600, color: 'var(--text)' }}>
                    {formatSymbol(row.symbol)}
                  </td>
                  <td style={{ padding: '7px 0', textAlign: 'right', fontFamily: 'monospace', fontSize: 11, color: 'var(--text-dim)' }}>
                    {row.trades}
                  </td>
                  <td style={{
                    padding: '7px 0', textAlign: 'right',
                    fontFamily: 'monospace', fontSize: 11,
                    color: row.win_rate >= 50 ? 'var(--green)' : 'var(--red)',
                  }}>
                    {formatPct(row.win_rate)}
                  </td>
                  <td style={{
                    padding: '7px 0', textAlign: 'right',
                    fontFamily: 'monospace', fontSize: 12, fontWeight: 700,
                    color: row.net_pnl >= 0 ? 'var(--green)' : 'var(--red)',
                    textShadow: row.net_pnl >= 0 ? '0 0 8px rgba(0,255,157,0.3)' : '0 0 8px rgba(255,68,102,0.3)',
                  }}>
                    {formatPnl(row.net_pnl)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

function StatCard({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{
      borderRadius: 14,
      padding: '12px 14px',
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      position: 'relative',
      overflow: 'hidden',
    }}>
      <div style={{
        position: 'absolute', bottom: 0, left: 0, right: 0, height: 1,
        background: `linear-gradient(90deg, transparent, ${color}28, transparent)`,
      }} />
      <p style={{ fontSize: 9, letterSpacing: '0.22em', textTransform: 'uppercase', color: 'var(--text-dim)', marginBottom: 6 }}>
        {label}
      </p>
      <p style={{
        fontSize: 16,
        fontWeight: 700,
        fontFamily: 'monospace',
        color,
        textShadow: `0 0 10px ${color}60`,
        letterSpacing: '-0.02em',
      }}>
        {value}
      </p>
    </div>
  )
}

function EquityCurveChart({ data }: { data: { points: number[]; timestamps: string[] } }) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!containerRef.current || data.points.length === 0) return

    let chart: ReturnType<typeof createChart> | null = null
    let observer: ResizeObserver | null = null

    try {
      chart = createChart(containerRef.current, {
        width: containerRef.current.clientWidth,
        height: 190,
        layout: {
          background: { type: ColorType.Solid, color: 'transparent' },
          textColor: '#4e6070',
          fontSize: 10,
        },
        grid: {
          vertLines: { visible: false },
          horzLines: { color: 'rgba(0,212,255,0.05)' },
        },
        timeScale: {
          borderVisible: false,
          borderColor: 'rgba(0,212,255,0.08)',
          timeVisible: true,
        },
        rightPriceScale: { borderVisible: false },
        handleScroll: false,
        handleScale: false,
      })

      const series = chart.addSeries(AreaSeries, {
        lineColor: '#00d4ff',
        topColor: 'rgba(0,212,255,0.22)',
        bottomColor: 'rgba(0,212,255,0.0)',
        lineWidth: 2,
      })

      // Deduplicate and sort by time (lightweight-charts requires strictly ascending times)
      // Ensure UTC parsing: append 'Z' if no timezone indicator present
      const rawData = data.points.map((value, i) => {
        const ts = data.timestamps[i]
        const utcTs = /[Zz+\-]\d{0,4}$/.test(ts) ? ts : ts + 'Z'
        return {
          time: Math.floor(new Date(utcTs).getTime() / 1000) as Time,
          value,
        }
      })
      rawData.sort((a, b) => (a.time as number) - (b.time as number))
      // Remove duplicates (keep last value for same timestamp)
      const seen = new Map<number, number>()
      for (const d of rawData) seen.set(d.time as number, d.value)
      const chartData = Array.from(seen.entries()).map(([time, value]) => ({ time: time as Time, value }))

      if (chartData.length === 0) return
      series.setData(chartData)
      chart.timeScale().fitContent()

      observer = new ResizeObserver(() => {
        try {
          if (containerRef.current && chart) {
            chart.applyOptions({ width: containerRef.current.clientWidth })
          }
        } catch { /* unmounted */ }
      })
      observer.observe(containerRef.current)
    } catch (err) {
      logger.error('[EquityCurveChart] Failed to create chart:', err)
    }

    return () => {
      try { observer?.disconnect() } catch { /* already disconnected */ }
      try { chart?.remove() } catch { /* already removed */ }
    }
  }, [data])

  return <div ref={containerRef} style={{ width: '100%', height: 190 }} />
}
