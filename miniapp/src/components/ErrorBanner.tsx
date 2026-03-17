interface Props { message: string }
export function ErrorBanner({ message }: Props) {
  return (
    <div className="mx-4 mt-4 p-3 bg-red-900/30 border border-red-700 rounded-xl text-red-300 text-sm">
      {message}
    </div>
  )
}
