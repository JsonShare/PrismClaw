/**
 * 咨询聊天 WS hook —— 域自建轻量客户端
 *
 * 复用核心 WS 协议（/api/ws，hello→identify→ready→chat.send/delta/done），
 * 但完全不依赖宿主的 useWebSocket / useChatStore / useSessionStore。
 *
 * 域分包依赖原则：
 * - 禁止：宿主 store / context / 运行时服务（如 useToast 依赖 ToastProvider）
 * - 允许：纯叶子组件（string→HTML，零 store 零 context，如 MarkdownRenderer）
 */

import { useCallback, useEffect, useRef, useState } from 'react'

// ── 协议常量 ────────────────────────────────────────────────

const PROTOCOL_VERSION = 1
// 鉴权/握手失败：4001/4002/4003/4005（版本不匹配）。
// 4006（心跳超时）是连接层问题，走断连重连，不触发鉴权失败回调。
const AUTH_FAILURE_CODES = new Set([4001, 4002, 4003, 4005])

// ── 轻量消息类型（域内部，不全量复制宿主 chat.ts） ───────

export interface ConsultMessage {
  id: string
  role: 'user' | 'assistant' | 'error'
  content: string
  timestamp: number
}

export type ConsultWSState = 'disconnected' | 'connecting' | 'identifying' | 'ready'

// AskUserQuestion 澄清（执行中途暂停）：回答经 tool.clarify 回传、同轮续跑，
// 不追加用户消息——澄清是执行过程的一步，不是独立提问。
export interface ConsultClarifyOption {
  label: string
  description: string
}

export interface ConsultClarifyQuestion {
  question: string
  header: string
  multiSelect: boolean
  options: ConsultClarifyOption[]
}

export interface ConsultClarifyRequest {
  approvalId: string
  toolCallId: string
  questions: ConsultClarifyQuestion[]
}

// WS 帧的最小子集（域只用 chat.send / chat.delta / chat.done / tool.clarify* / error / hello / ready / ping）
type WSFrameType =
  | 'hello'
  | 'ready'
  | 'chat.delta'
  | 'chat.done'
  | 'chat.queued'
  | 'tool.clarify_request'
  | 'tool.clarify'
  | 'error'
  | 'ping'

interface WSFrame {
  type: WSFrameType
  conversation_id?: string
  data?: Record<string, unknown>
}

// ── Hook 返回值 ─────────────────────────────────────────────

export interface UseConsultationChatResult {
  wsState: ConsultWSState
  messages: ConsultMessage[]
  isStreaming: boolean
  streamingText: string
  /** 发送用户消息（如 convId 未给定且无活跃会话，自动建会话） */
  sendMessage: (text: string, convId?: string) => Promise<string | null>
  /** 仅创建核心会话并设为活跃（不发 WS 帧），供调用方在发消息前完成域登记 */
  ensureSession: (title?: string) => Promise<string | null>
  /** 中止当前流式 */
  abort: () => void
  /** 手动连接 */
  connect: () => void
  /** 手动断开 */
  disconnect: () => void
  /** 当前活跃会话 ID */
  activeConvId: string | null
  /** 设定活跃会话（不发送消息，仅用于恢复已有会话） */
  setActiveConv: (convId: string) => void
  /** 当前待回答的澄清请求；非空时普通输入应被澄清面板替代 */
  clarifyRequest: ConsultClarifyRequest | null
  /** 提交澄清答案：走 tool.clarify 同轮续跑，不追加用户消息 */
  submitClarify: (answer: string) => void
  /** 错误信息 */
  lastError: string | null
}

// ── 工具函数 ────────────────────────────────────────────────

function getAuthToken(): string | null {
  try {
    const raw = localStorage.getItem('prismclaw.auth.v1')
    if (!raw) return null
    const parsed = JSON.parse(raw) as { token?: unknown }
    return typeof parsed.token === 'string' ? parsed.token : null
  } catch {
    return null
  }
}

function buildWsUrl(): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}/api/ws`
}

// ── Hook 主体 ──────────────────────────────────────────────

function toClarifyQuestions(raw: unknown): ConsultClarifyQuestion[] {
  const list = Array.isArray(raw) ? raw : []
  return list
    .filter((q): q is Record<string, unknown> => typeof q === 'object' && q !== null)
    .map((q) => ({
      question: typeof q.question === 'string' ? q.question : '',
      header: typeof q.header === 'string' ? q.header : '',
      multiSelect: q.multi_select === true || q.multiSelect === true,
      options: (Array.isArray(q.options) ? q.options : [])
        .filter((o): o is Record<string, unknown> => typeof o === 'object' && o !== null)
        .map((o) => ({
          label: typeof o.label === 'string' ? o.label : '',
          description: typeof o.description === 'string' ? o.description : '',
        }))
        .filter((o) => o.label.length > 0),
    }))
    .filter((q) => q.question.length > 0 && q.options.length > 0)
}

export function useConsultationChat(): UseConsultationChatResult {
  const [wsState, setWsState] = useState<ConsultWSState>('disconnected')
  const [messages, setMessages] = useState<ConsultMessage[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const [streamingText, setStreamingText] = useState('')
  const [activeConvId, setActiveConvId] = useState<string | null>(null)
  const [lastError, setLastError] = useState<string | null>(null)
  const [clarifyRequest, setClarifyRequest] = useState<ConsultClarifyRequest | null>(null)

  const wsRef = useRef<WebSocket | null>(null)
  const tokenRef = useRef<string | null>(null)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // 用 ref 持有最新 state，避免闭包过期
  const convIdRef = useRef<string | null>(null)
  const clarifyRef = useRef<ConsultClarifyRequest | null>(null)
  const isStreamingRef = useRef(false)
  const streamingTextRef = useRef('')
  const wsStateRef = useRef<ConsultWSState>('disconnected')

  // 同步 ref
  convIdRef.current = activeConvId
  clarifyRef.current = clarifyRequest
  isStreamingRef.current = isStreaming
  streamingTextRef.current = streamingText
  wsStateRef.current = wsState

  // ── WS send 原语 ──
  const wsSend = useCallback((msg: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg))
    }
  }, [])

  // ── 消息追加 ──
  const appendMsg = useCallback((msg: ConsultMessage) => {
    setMessages((prev) => [...prev, msg])
  }, [])

  // ── 连接 ──
  const connect = useCallback(() => {
    const token = getAuthToken()
    if (!token) {
      setLastError('未登录，请先登录')
      return
    }
    tokenRef.current = token

    // 防止重复连接
    const cur = wsRef.current
    if (cur && (cur.readyState === WebSocket.OPEN || cur.readyState === WebSocket.CONNECTING)) {
      return
    }

    // 清理旧连接
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }

    setLastError(null)
    const ws = new WebSocket(buildWsUrl())
    wsRef.current = ws
    setWsState('connecting')

    ws.onopen = () => {
      // 等待 hello 帧
    }

    ws.onmessage = (ev) => {
      let frame: WSFrame
      try {
        frame = JSON.parse(ev.data) as WSFrame
      } catch {
        return
      }

      switch (frame.type) {
        case 'hello':
          setWsState('identifying')
          wsSend({
            type: 'identify',
            token: tokenRef.current!,
            protocol_version: PROTOCOL_VERSION,
          })
          break

        case 'ready':
          setWsState('ready')
          break

        case 'ping':
          wsSend({ type: 'pong' })
          break

        case 'tool.clarify_request': {
          const convId = frame.conversation_id
          if (convId && convId !== convIdRef.current) break
          const d = frame.data ?? {}
          const approvalId = typeof d.approval_id === 'string' ? d.approval_id : ''
          const toolCallId = typeof d.tool_call_id === 'string' ? d.tool_call_id : ''
          const questions = toClarifyQuestions(d.questions)
          if (approvalId && toolCallId && questions.length > 0) {
            setClarifyRequest({ approvalId, toolCallId, questions })
          }
          break
        }

        case 'chat.delta': {
          const convId = frame.conversation_id
          if (convId && convId !== convIdRef.current) break
          const text = (frame.data?.text as string) ?? ''
          setIsStreaming(true)
          setStreamingText((prev) => prev + text)
          break
        }

        case 'chat.done': {
          const convId = frame.conversation_id
          if (convId && convId !== convIdRef.current) break
          const finalMessage = frame.data?.final_message
          const finalText = typeof finalMessage === 'string' ? finalMessage : ''
          const aborted = frame.data?.aborted === true

          // 正文 = 后端 final_message，兜底 ref 中的最新 streamingText
          const snapshot = streamingTextRef.current
          const bodyContent = finalText.trim().length > 0 ? finalText : snapshot

          if (bodyContent.trim().length > 0) {
            appendMsg({
              id: `asst_${Date.now()}`,
              role: 'assistant',
              content: bodyContent,
              timestamp: Date.now(),
            })
          }

          setStreamingText('')
          setIsStreaming(false)
          setClarifyRequest(null)

          // 中止且无正文 → 落错误提示
          if (aborted && bodyContent.trim().length === 0) {
            appendMsg({
              id: `err_abort_${Date.now()}`,
              role: 'error',
              content: '回复已中止',
              timestamp: Date.now(),
            })
          }
          break
        }

        case 'error': {
          const errMsg = (frame.data?.message as string) ?? '未知错误'
          setIsStreaming(false)
          setStreamingText('')
          setClarifyRequest(null)
          appendMsg({
            id: `err_${Date.now()}`,
            role: 'error',
            content: errMsg,
            timestamp: Date.now(),
          })
          break
        }

        default:
          break
      }
    }

    ws.onclose = (ev) => {
      if (wsRef.current === ws) wsRef.current = null
      setWsState('disconnected')

      if (AUTH_FAILURE_CODES.has(ev.code)) {
        setLastError(`鉴权失败（WS close ${ev.code}）`)
      } else if (tokenRef.current) {
        // 简单重连：2 秒后重试，不做指数退避（v1）
        reconnectTimerRef.current = setTimeout(() => {
          if (tokenRef.current && !wsRef.current) {
            connect()
          }
        }, 2000)
      }
    }

    ws.onerror = () => {
      setLastError('WebSocket 连接错误')
      ws.close()
    }
  }, [wsSend, appendMsg])

  // ── 断开 ──
  const disconnect = useCallback(() => {
    tokenRef.current = null
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
    setWsState('disconnected')
    setIsStreaming(false)
    setStreamingText('')
  }, [])

  // ── 仅创建核心会话（不发 WS 帧） ──
  const ensureSession = useCallback(async (title?: string): Promise<string | null> => {
    const token = getAuthToken()
    if (!token) {
      setLastError('未登录，请先登录')
      return null
    }

    // 已有活跃会话则直接返回
    if (convIdRef.current) return convIdRef.current

    try {
      const res = await fetch('/api/sessions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ title: title || '咨询会话' }),
      })
      if (!res.ok) throw new Error(`创建会话失败: ${res.status}`)
      const data = await res.json()
      const sessionId = data.session_id as string
      setActiveConvId(sessionId)
      convIdRef.current = sessionId
      return sessionId
    } catch (err) {
      const msg = err instanceof Error ? err.message : '创建会话失败'
      setLastError(msg)
      return null
    }
  }, [])

  // ── 发送消息 ──
  const sendMessage = useCallback(async (text: string, convId?: string): Promise<string | null> => {
    const token = getAuthToken()
    if (!token) {
      setLastError('未登录，请先登录')
      return null
    }

    let sessionId = convId ?? convIdRef.current

    // 无会话则新建
    if (!sessionId) {
      try {
        const res = await fetch('/api/sessions', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ title: text.trim() || '咨询会话' }),
        })
        if (!res.ok) throw new Error(`创建会话失败: ${res.status}`)
        const data = await res.json()
        sessionId = data.session_id as string
      } catch (err) {
        const msg = err instanceof Error ? err.message : '创建会话失败'
        setLastError(msg)
        appendMsg({
          id: `err_${Date.now()}`,
          role: 'error',
          content: msg,
          timestamp: Date.now(),
        })
        return null
      }
    }

    setActiveConvId(sessionId)
    convIdRef.current = sessionId

    // 追加用户消息
    appendMsg({
      id: `usr_${Date.now()}`,
      role: 'user',
      content: text,
      timestamp: Date.now(),
    })

    // WS 发送 chat.send
    if (wsStateRef.current !== 'ready') {
      appendMsg({
        id: `err_${Date.now()}`,
        role: 'error',
        content: '连接未就绪，消息未发送。请等待连接恢复后重试。',
        timestamp: Date.now(),
      })
      return sessionId
    }

    setIsStreaming(true)
    setStreamingText('')

    wsSend({
      type: 'chat.send',
      conversation_id: sessionId,
      content: text,
      trajectory: 'full',
    })

    return sessionId
  }, [wsSend, appendMsg])

  // ── 重连/刷新后从运行投影恢复待答澄清 ──
  // 不恢复的话面板会丢失，用户只能打字，回答就会变成一条独立消息。
  const hydratedRunRef = useRef<string | null>(null)
  useEffect(() => {
    if (wsState !== 'ready' || !activeConvId) return
    if (hydratedRunRef.current === activeConvId) return
    hydratedRunRef.current = activeConvId
    const token = getAuthToken()
    if (!token) return
    let cancelled = false
    fetch(`/api/conversations/${encodeURIComponent(activeConvId)}/run`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (cancelled || !data) return
        const clar = (data.run ?? data)?.clarification as Record<string, unknown> | undefined
        if (!clar) return
        const approvalId = typeof clar.approval_id === 'string' ? clar.approval_id : ''
        const toolCallId = typeof clar.tool_call_id === 'string' ? clar.tool_call_id : ''
        const questions = toClarifyQuestions(clar.questions)
        if (approvalId && toolCallId && questions.length > 0) {
          setClarifyRequest({ approvalId, toolCallId, questions })
        }
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [wsState, activeConvId])

  // ── 澄清回答：同轮续跑，不追加用户消息 ──
  const submitClarify = useCallback((answer: string) => {
    const req = clarifyRef.current
    const convId = convIdRef.current
    if (!req || !convId) return
    wsSend({
      type: 'tool.clarify',
      conversation_id: convId,
      approval_id: req.approvalId,
      tool_call_id: req.toolCallId,
      answer,
    })
    setClarifyRequest(null)
    setIsStreaming(true)
    setStreamingText('')
  }, [wsSend])

  // -- 设定活跃会话（不发送消息，仅恢复已有会话用） --
  const setActiveConv = useCallback((convId: string) => {
    setActiveConvId(convId)
    convIdRef.current = convId
  }, [])

  // ── 中止 ──
  const abort = useCallback(() => {
    if (convIdRef.current) {
      wsSend({ type: 'chat.abort', conversation_id: convIdRef.current })
    }
  }, [wsSend])

  // ── 自动连接 / 清理 ──
  useEffect(() => {
    connect()
    return () => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
    }
  }, [connect])

  return {
    wsState,
    messages,
    isStreaming,
    streamingText,
    sendMessage,
    ensureSession,
    abort,
    connect,
    disconnect,
    activeConvId,
    setActiveConv,
    clarifyRequest,
    submitClarify,
    lastError,
  }
}
