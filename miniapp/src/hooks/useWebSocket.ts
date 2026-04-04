import { useEffect, useRef } from 'react'
import { useSystemStore } from '../store/useSystemStore'
import { getInitData } from '../api/client'
import { logger } from '../lib/logger'
import type { WsSnapshot } from '../api/types'

const MAX_RETRIES = 10
const BASE_DELAY = 1000
const MAX_DELAY = 30_000
const CONNECT_TIMEOUT = 10_000  // close and retry if not opened within 10s
const STALE_TIMEOUT = 15_000    // no message for 15s = stale, force reconnect

export function useWebSocket() {
  const { setSnapshot, setWsStatus, touchSnapshot } = useSystemStore()
  const retryRef = useRef(0)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    let destroyed = false

    function connect() {
      if (destroyed) return
      setWsStatus(retryRef.current === 0 ? 'connecting' : 'reconnecting')

      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
      // Token NOT in URL — sent as first message after open to avoid exposure in logs
      const wsUrl = `${protocol}://${window.location.host}/api/ws`
      const ws = new WebSocket(wsUrl)
      wsRef.current = ws

      // Abort if handshake stalls
      const connectTimer = setTimeout(() => {
        if (ws.readyState !== WebSocket.OPEN) {
          logger.warn('WS connect timeout — closing and retrying')
          ws.close()
        }
      }, CONNECT_TIMEOUT)

      let staleTimer: ReturnType<typeof setTimeout> | null = null
      const resetStaleTimer = () => {
        if (staleTimer) clearTimeout(staleTimer)
        staleTimer = setTimeout(() => {
          if (!destroyed && ws.readyState === WebSocket.OPEN) {
            logger.warn('WS stale — no message for 15s, forcing reconnect')
            ws.close()
          }
        }, STALE_TIMEOUT)
      }

      ws.onopen = () => {
        clearTimeout(connectTimer)
        if (destroyed) { ws.close(); return }
        // Auth: send token as first message
        const token = getInitData()
        if (token) {
          ws.send(JSON.stringify({ type: 'auth', token }))
        }
        retryRef.current = 0
        setWsStatus('connected')
        resetStaleTimer()
        logger.debug('connected')
      }

      ws.onmessage = (e) => {
        if (destroyed) return
        resetStaleTimer()
        try {
          const data = JSON.parse(e.data as string)
          // Respond to server keepalive ping
          if (data?.type === 'ping') {
            ws.send(JSON.stringify({ type: 'pong' }))
            return
          }
          // Validate required WsSnapshot fields before using
          if (
            data &&
            typeof data === 'object' &&
            typeof data.timestamp === 'string' &&
            typeof data.system_state === 'string' &&
            Array.isArray(data.positions)
          ) {
            setSnapshot(data as WsSnapshot)
            touchSnapshot()
          } else {
            logger.warn('WS message failed validation', data)
          }
        } catch (err) {
          logger.warn('WS malformed message', err)
        }
      }

      ws.onclose = () => {
        clearTimeout(connectTimer)
        if (staleTimer) clearTimeout(staleTimer)
        if (destroyed) return
        logger.debug(`WS closed (retry ${retryRef.current}/${MAX_RETRIES})`)
        setWsStatus('reconnecting')
        if (retryRef.current >= MAX_RETRIES) {
          setWsStatus('disconnected')
          logger.warn('WS max retries reached — will retry in 60s')
          // Instead of giving up forever, schedule one more attempt after 60s.
          // This handles transient server restarts that last > backoff window.
          timerRef.current = setTimeout(() => {
            if (destroyed) return
            retryRef.current = 0
            connect()
          }, 60_000)
          return
        }
        const delay = Math.min(BASE_DELAY * 2 ** retryRef.current, MAX_DELAY)
        retryRef.current++
        timerRef.current = setTimeout(connect, delay)
      }

      ws.onerror = (e) => {
        logger.error('WS error', e)
        ws.close()
      }
    }

    connect()

    return () => {
      destroyed = true
      if (timerRef.current) clearTimeout(timerRef.current)
      if (wsRef.current) wsRef.current.close()
      setWsStatus('disconnected')
    }
  }, [setSnapshot, setWsStatus, touchSnapshot])
}
