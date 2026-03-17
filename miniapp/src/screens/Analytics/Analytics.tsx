import { useRef, useEffect } from 'react'
import { createChart, AreaSeries, ColorType } from 'lightweight-charts'
import type { Time } from 'lightweight-charts'
import { useSettingsStore } from '../../store/useSettingsStore'
import { useAnalyticsSummary, useEquityCurve, useBySymbol } from '../../hooks/useAnalytics'
import { LoadingSpinner } from '../../components/LoadingSpinner'
import { ErrorBanner } from '../../components/ErrorBanner'
import { formatUSDT, formatPct, formatSymbol } from '../../lib/formatters'

const DAYS_OPTIONS = [7, 30, 90]

export function Analytics() {
  const { analyticsDays, setAnalyticsDays } = useSettingsStore()
  const { data: summary, isLoading: loadingSum, error: errSum } = useAnalyticsSummary(analyticsDays)
  const { data: equity, isLoading: loadingEq, error: errEq } = useEquityCurve(analyticsDays)
  const { data: bySymbol, isLoading: loadingSym, error: errSym } = useBySymbol(analyticsDays)

  return (
    <div className="px-4 pt-4">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-bold">Analytics</h1>
        <div className="flex gap-1">
          {DAYS_OPTIONS.map(d => (
            <button
              key={d}
              onClick={() => setAnalyticsDays(d)}
              className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                analyticsDays === d
                  ? 'bg-[var(--tg-button)] text-[var(--tg-button-text)]'
                  : 'bg-[var(--tg-secondary)] text-[var(--tg-hint)]'
              }`}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>

      {errSum && <ErrorBanner message={errSum.message} />}
      {errEq && <ErrorBanner message={errEq.message} />}

      {/* Equity curve */}
      <div className="bg-[var(--tg-secondary)] rounded-2xl p-4 mb-4">
        <p className="text-sm text-[var(--tg-hint)] mb-3">Equity Curve</p>
        {loadingEq ? <LoadingSpinner /> : equity && <EquityCurveChart data={equity} />}
      </div>

      {/* Summary stats */}
      {loadingSum ? <LoadingSpinner /> : summary && (
        <div className="grid grid-cols-2 gap-3 mb-4">
          <StatCard label="Win Rate" value={formatPct(summary.win_rate)} />
          <StatCard label="Max Drawdown" value={formatPct(summary.max_drawdown_pct)} negative />
          <StatCard
            label="Profit Factor"
            value={summary.profit_factor != null ? summary.profit_factor.toFixed(2) : '—'}
          />
          <StatCard
            label="Sharpe Ratio"
            value={summary.sharpe_ratio != null ? summary.sharpe_ratio.toFixed(2) : '—'}
          />
          <StatCard label="Net P&L" value={formatUSDT(summary.net_pnl)} positive={summary.net_pnl >= 0} />
          <StatCard label="Total Trades" value={String(summary.total_trades)} />
        </div>
      )}

      {/* By symbol */}
      <div className="bg-[var(--tg-secondary)] rounded-2xl p-4 mb-4">
        <p className="text-sm text-[var(--tg-hint)] mb-3">By Symbol</p>
        {loadingSym && <LoadingSpinner />}
        {errSym && <ErrorBanner message={errSym.message} />}
        {bySymbol && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-[var(--tg-hint)] text-xs">
                  <th className="text-left pb-2">Symbol</th>
                  <th className="text-right pb-2">Trades</th>
                  <th className="text-right pb-2">Win%</th>
                  <th className="text-right pb-2">P&L</th>
                </tr>
              </thead>
              <tbody>
                {bySymbol.map((row) => (
                  <tr key={row.symbol} className="border-t border-white/5">
                    <td className="py-2 pr-2">{formatSymbol(row.symbol)}</td>
                    <td className="py-2 text-right text-[var(--tg-hint)]">{row.trades}</td>
                    <td className="py-2 text-right">{formatPct(row.win_rate)}</td>
                    <td className={`py-2 text-right ${row.net_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {formatUSDT(row.net_pnl)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

function StatCard({ label, value, positive, negative }: {
  label: string; value: string; positive?: boolean; negative?: boolean
}) {
  const color = positive === true ? 'text-green-400' : negative ? 'text-red-400' : 'text-[var(--tg-text)]'
  return (
    <div className="bg-[var(--tg-secondary)] rounded-xl p-3">
      <p className="text-xs text-[var(--tg-hint)] mb-1">{label}</p>
      <p className={`text-base font-semibold ${color}`}>{value}</p>
    </div>
  )
}

function EquityCurveChart({ data }: { data: { points: number[]; timestamps: string[] } }) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!containerRef.current || data.points.length === 0) return

    const style = getComputedStyle(document.documentElement)
    const bg = style.getPropertyValue('--tg-bg').trim() || '#1c1c1e'
    const hint = style.getPropertyValue('--tg-hint').trim() || '#8e8e93'
    const button = style.getPropertyValue('--tg-button').trim() || '#0a84ff'

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 200,
      layout: { background: { type: ColorType.Solid, color: bg }, textColor: hint },
      grid: { vertLines: { visible: false }, horzLines: { color: hint + '20' } },
      timeScale: { borderVisible: false },
      rightPriceScale: { borderVisible: false },
      handleScroll: false,
      handleScale: false,
    })

    const series = chart.addSeries(AreaSeries, {
      lineColor: button,
      topColor: button + '40',
      bottomColor: button + '00',
      lineWidth: 2,
    })

    const chartData = data.points.map((value, i) => ({
      time: Math.floor(new Date(data.timestamps[i]).getTime() / 1000) as Time,
      value,
    }))

    series.setData(chartData)
    chart.timeScale().fitContent()

    const observer = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth })
      }
    })
    observer.observe(containerRef.current)

    return () => {
      observer.disconnect()
      chart.remove()
    }
  }, [data])

  return <div ref={containerRef} style={{ width: '100%', height: 200 }} />
}
