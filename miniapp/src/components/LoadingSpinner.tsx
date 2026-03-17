export function LoadingSpinner() {
  return (
    <div className="flex items-center justify-center py-8">
      <div className="w-8 h-8 border-2 border-[var(--tg-hint)] border-t-[var(--tg-button)] rounded-full animate-spin" />
    </div>
  )
}
