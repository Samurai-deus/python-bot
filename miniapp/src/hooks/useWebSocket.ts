import { useEffect, useRef } from 'react'
import { useSystemStore } from '../store/useSystemStore'
import type { WsSnapshot } from '../api/types'

const MAX_RETRIES = 10
const BASE_DELAY = 1000
const MAX_DELAY = 30_000

export function useWebSocket() {
  const { setSnapshot, setWsStatus } = useSystemStore()
  const retryRef = useRef(0)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    let destroyed = false

    function connect() {
      if (destroyed) return
      setWsStatus(retryRef.current === 0 ? 'connecting' : 'reconnecting')

      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const ws = new WebSocket(`${protocol}://${window.location.host}/api/ws`)
      wsRef.current = ws

      ws.onopen = () => {
        if (destroyed) { ws.close(); return }
        retryRef.current = 0
        setWsStatus('connected')
      }

      ws.onmessage = (e) => {
        if (destroyed) return
        try {
          const data: WsSnapshot = JSON.parse(e.data)
          setSnapshot(data)
        } catch { /* ignore malformed */ }
      }

      ws.onclose = () => {
        if (destroyed) return
        setWsStatus('reconnecting')
        if (retryRef.current >= MAX_RETRIES) {
          setWsStatus('disconnected')
          return
        }
        const delay = Math.min(BASE_DELAY * 2 ** retryRef.current, MAX_DELAY)
        retryRef.current++
        timerRef.current = setTimeout(connect, delay)
      }

      ws.onerror = () => ws.close()
    }

    connect()

    return () => {
      destroyed = true
      if (timerRef.current) clearTimeout(timerRef.current)
      if (wsRef.current) wsRef.current.close()
      setWsStatus('disconnected')
    }
  }, [setSnapshot, setWsStatus])
}
