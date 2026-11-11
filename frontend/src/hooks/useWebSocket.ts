import { useRef, useState, useEffect, useCallback } from 'react'
import type {
  WSState,
  WSServerMessage,
  WSClientMessage,
  Conversation,
} from '../types'

const MIN_BACKOFF = 500
const MAX_BACKOFF = 30_000

const PROTOCOL_VERSION = 1
// 鉴权/握手失败：4001 未知错误 / 4002 未先 identify / 4003 token 无效 / 4005 协议版本不匹配。
// 4006（心跳超时/发送失败）只是连接层问题，走普通断连重连，不登出——
// 长任务期间事件循环繁忙会饿死 pong 分发，误判为鉴权失败会把用户踢到登录页。
const AUTH_FAILURE_CODES = new Set([4001, 4002, 4003, 4005])

/** 出站缓冲上限：断连期间用户连续发送的消息按序排队，超出丢弃最旧并告警 */
const OUTBOX_MAX = 100

/** 可缓冲的消息类型：会话类消息在 WS 重连后补发有意义；
    协议消息（identify/pong）由握手流程驱动，不入队 */
const OUTBOX_BUFFERABLE_TYPES = new Set([
  'chat.send',
  'chat.abort',
  'tool.clarify',
])

function jitter(ms: number): number {
  return ms + Math.random() * ms * 0.3
}

type WsDisconnectInfo = {
  url: string
  code: number
  reason: string
  wasClean: boolean
  at: number
}

export function useWebSocket(token: string | null, onAuthFailure?: () => void) {
  const [wsState, setWsState] = useState<WSState>('disconnected')
  // 消息队列：帧到达即入队，消费端按序 drain。
  //
  // 不能用单个 useState 承载「最后一条消息」：React 并发渲染会把同一渲染周期内
  // 到达的多条帧批处理成一次更新，只有最后一个 setState 的值存活，其余被静默
  // 丢弃。实测连发 100 条 chat.delta 丢失 99 条，且后端 ToolCallStart 分支
  // 背靠背下发的 task.timeline / task.runtime 只会活下最后一帧
  // ——正是「正文跳字 / 执行过程停滞」的直接原因。
  const queueRef = useRef<WSServerMessage[]>([])
  const [queueTick, setQueueTick] = useState(0)
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [lastDisconnect, setLastDisconnect] = useState<WsDisconnectInfo | null>(null)
  const [lastError, setLastError] = useState<string | null>(null)
  const [lastWsUrl, setLastWsUrl] = useState<string | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  /** 出站缓冲：WS 未就绪期间待补发的会话消息 */
  const outboxRef = useRef<WSClientMessage[]>([])
  const backoffRef = useRef(MIN_BACKOFF)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const tokenRef = useRef(token)
  tokenRef.current = token
  const onAuthFailureRef = useRef(onAuthFailure)
  onAuthFailureRef.current = onAuthFailure

  const send = useCallback((msg: WSClientMessage) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg))
      return
    }
    // WS 未就绪（断连/重连中）：会话类消息入出站队列，ready 后按序补发——
    // 后端重启窗口内用户发送的消息不再静默丢失（此前直接 drop，前端乐观
    // 气泡造成「发送成功」假象，后端实际零落库）。
    if (OUTBOX_BUFFERABLE_TYPES.has(msg.type)) {
      outboxRef.current.push(msg)
      if (outboxRef.current.length > OUTBOX_MAX) {
        const dropped = outboxRef.current.shift()
        console.warn('[WebSocket] Outbox overflow, dropped oldest:', dropped?.type)
      }
      console.info('[WebSocket] Queued (WS not OPEN):', msg.type, 'queue:', outboxRef.current.length)
    } else {
      console.warn('[WebSocket] Message dropped - WS not OPEN:', msg.type, 'readyState:', wsRef.current?.readyState)
    }
  }, [])

  /** ready（identify 完成）后按序补发出站队列。不能在 onopen flush——
      未通过 identify 前服务端会以 4002 关连接 */
  const flushOutbox = useCallback(() => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    const pending = outboxRef.current.splice(0)
    for (const msg of pending) {
      ws.send(JSON.stringify(msg))
    }
    if (pending.length > 0) {
      console.info('[WebSocket] Outbox flushed:', pending.length, 'message(s)')
    }
  }, [])

  const connect = useCallback(() => {
    void (async () => {
      if (!tokenRef.current) return
      // 防止 StrictMode 双挂载 / 重连竞争创建多个 WS 连接：已有活跃连接则跳过。
      const cur = wsRef.current
      if (cur && (cur.readyState === WebSocket.OPEN || cur.readyState === WebSocket.CONNECTING)) {
        return
      }
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current)
        reconnectTimer.current = null
      }
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }

      // 云端 B/S 架构：WebSocket 与 HTTP 同源，直接用 window.location.host。
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const wsUrl = `${proto === 'wss:' ? 'wss:' : 'ws:'}//${window.location.host}/api/ws`
      setLastWsUrl(wsUrl)

      setLastError(null)
      const ws = new WebSocket(wsUrl)
      wsRef.current = ws
      setWsState('connecting')

      ws.onopen = () => {
        backoffRef.current = MIN_BACKOFF
      }

      ws.onmessage = (ev) => {
        let msg: WSServerMessage
        try {
          msg = JSON.parse(ev.data)
        } catch {
          return
        }

        switch (msg.type) {
          case 'hello':
            setWsState('identifying')
            send({ type: 'identify', token: tokenRef.current!, protocol_version: PROTOCOL_VERSION })
            break
          case 'ready':
            setWsState('ready')
            if (Array.isArray(msg.data.conversations) && msg.data.conversations.length > 0) {
              setConversations(msg.data.conversations)
            }
            // identify 已完成、连接已认证——补发断连期间排队的会话消息
            flushOutbox()
            break
          case 'ping':
            send({ type: 'pong' })
            break
          default:
            break
        }

        queueRef.current.push(msg)
        setQueueTick((n) => n + 1)
      }

      ws.onclose = (ev) => {
        // 仅当关闭的是当前活跃连接时才清除引用，避免误清其他连接的引用。
        if (wsRef.current === ws) wsRef.current = null
        setWsState('disconnected')
        setLastDisconnect({
          url: wsUrl,
          code: ev.code,
          reason: ev.reason || '',
          wasClean: ev.wasClean,
          at: Date.now(),
        })

        if (AUTH_FAILURE_CODES.has(ev.code)) {
          setLastError(`鉴权失败（WS close ${ev.code}${ev.reason ? `: ${ev.reason}` : ''}）`)
          onAuthFailureRef.current?.()
          return
        }

        if (tokenRef.current) {
          const delay = jitter(backoffRef.current)
          backoffRef.current = Math.min(backoffRef.current * 2, MAX_BACKOFF)
          reconnectTimer.current = setTimeout(connect, delay)
        }
      }

      ws.onerror = () => {
        setLastError('WebSocket 连接错误')
        ws.close()
      }
    })()
  }, [send, flushOutbox])

  useEffect(() => {
    if (token) {
      connect()
    } else {
      wsRef.current?.close()
      setWsState('disconnected')
    }
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [token, connect])

  /** 取出并清空当前积压的全部帧（按到达顺序）。与 queueTick 配对使用。 */
  const drainMessages = useCallback(() => queueRef.current.splice(0), [])

  return { wsState, send, drainMessages, queueTick, conversations, setConversations, lastDisconnect, lastError, lastWsUrl }
}
