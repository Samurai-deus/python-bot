import { useEffect, useRef } from 'react'
import { useSystemStore } from '../store/useSystemStore'
import { getAuthToken, waitForSession } from '../api/client'
import { logger } from '../lib/logger'
import { parseWsSnapshot } from '../lib/wsSnapshot'

const MAX_RETRIES = 10
const BASE_DELAY = 1000
const MAX_DELAY = 30_000
const CONNECT_TIMEOUT = 10_000  // close and retry if not opened within 10s
// Сервер шлёт снимок раз в 5 с, но при медленной базе сборка снимка занимает до
// ~15 с (два запроса по 5 с таймаута). Прежние 15 с давали ложные переподключения.
const STALE_TIMEOUT = 40_000
// WS close codes that mean auth failure — no point retrying with same token
const AUTH_FAILURE_CODES = [4001, 4003]

export function useWebSocket() {
  const { setSnapshot, setWsStatus, touchSnapshot, setAuthExpired } = useSystemStore()
  const retryRef = useRef(0)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    let destroyed = false

    function connect() {
      if (destroyed) return
      // Без токена сервер всё равно закроет сокет через 5 с — не открываем его зря.
      if (!getAuthToken()) {
        logger.warn('WS: нет initData — приложение открыто не из Telegram, сокет не открываю')
        setWsStatus('disconnected')
        return
      }
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
            logger.warn(`WS stale — no message for ${STALE_TIMEOUT / 1000}s, forcing reconnect`)
            ws.close()
          }
        }, STALE_TIMEOUT)
      }

      ws.onopen = () => {
        clearTimeout(connectTimer)
        if (destroyed) { ws.close(); return }
        ws.send(JSON.stringify({ type: 'auth', token: getAuthToken() }))
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
          const snapshot = parseWsSnapshot(data)
          if (snapshot) {
            setSnapshot(snapshot)
            touchSnapshot()
          } else {
            logger.warn('WS message failed validation', data)
          }
        } catch (err) {
          logger.warn('WS malformed message', err)
        }
      }

      ws.onclose = (e) => {
        clearTimeout(connectTimer)
        if (staleTimer) clearTimeout(staleTimer)
        if (destroyed) return

        // Auth failure — don't retry, token is invalid; показать баннер «откройте заново»
        if (AUTH_FAILURE_CODES.includes(e.code)) {
          logger.warn(`WS auth failure (code ${e.code}) — not retrying`)
          setWsStatus('disconnected')
          setAuthExpired(true)
          return
        }

        logger.debug(`WS closed (code ${e.code}, retry ${retryRef.current}/${MAX_RETRIES})`)
        setWsStatus('reconnecting')
        if (retryRef.current >= MAX_RETRIES) {
          setWsStatus('disconnected')
          logger.warn('WS max retries reached — will retry in 60s')
          timerRef.current = setTimeout(() => {
            if (destroyed) return
            retryRef.current = 0
            connect()
          }, 60_000)
          return
        }
        const baseDelay = Math.min(BASE_DELAY * 2 ** retryRef.current, MAX_DELAY)
        // Jitter: 50-100% of base delay to prevent thundering herd
        const delay = Math.round(baseDelay * (0.5 + Math.random() * 0.5))
        retryRef.current++
        timerRef.current = setTimeout(connect, delay)
      }

      ws.onerror = (e) => {
        logger.error('WS error', e)
        ws.close()
      }
    }

    // Первое соединение — после обмена initData на сессию (2.8): сокет, открытый
    // с initData, после выкладки 3 получил бы 4001 и ложный баннер «откройте снова».
    void waitForSession().then(connect)

    return () => {
      destroyed = true
      if (timerRef.current) clearTimeout(timerRef.current)
      if (wsRef.current) wsRef.current.close()
      setWsStatus('disconnected')
    }
  }, [setSnapshot, setWsStatus, touchSnapshot, setAuthExpired])
}
