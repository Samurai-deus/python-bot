import { clsx } from 'clsx'

interface Props {
  label: string
  variant?: 'buy' | 'sell' | 'long' | 'short' | 'running' | 'degraded' | 'halt' | 'recovery' | 'neutral'
}

const variantClasses: Record<string, string> = {
  buy: 'bg-green-500/20 text-green-400',
  long: 'bg-green-500/20 text-green-400',
  sell: 'bg-red-500/20 text-red-400',
  short: 'bg-red-500/20 text-red-400',
  running: 'bg-green-500/20 text-green-400',
  degraded: 'bg-amber-500/20 text-amber-400',
  halt: 'bg-red-500/20 text-red-400',
  recovery: 'bg-amber-500/20 text-amber-400',
  neutral: 'bg-[var(--tg-hint)]/20 text-[var(--tg-hint)]',
}

export function Badge({ label, variant = 'neutral' }: Props) {
  return (
    <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium', variantClasses[variant])}>
      {label}
    </span>
  )
}
