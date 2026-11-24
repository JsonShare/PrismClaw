import { useCallback, useEffect, useRef } from 'react'
import { useWebSocket } from '../useWebSocket'
import { useChatStore } from '../../stores/chat'
import { useSessionStore } from '../../stores/session'
import { useTaskStore } from '../../stores/tasks'
import { apiUrl } from '../../lib/system-adapter'
import { mapHistoryEntries } from './useSessionLoader'
import type { MessageHistoryEntry } from './useSessionLoader'
import type {
  Message,
  MessageMetadata,
  RunProjection,
  WSClientMessage,
  WSDomainTaskUpdated,
  WSServerMessage,
  WSState,
} from '../../types'

/**
 * 需要向用户推送桌面通知的运行事件。
 * 由 useChatSocket 在对应 WS 帧到达时统一派发，通知渠道（浏览器 Notification）
 * 与展示策略由 useDesktopNotifier 消费，两者职责分离。
 */
export type NotificationEvent =
  | { kind: 'done'; convId: string; aborted: boolean }
  | { kind: 'clarify'; convId: string; questionCount: number }
  | { kind: 'error'; convId: string; message: string }

export type NotificationListener = (event: NotificationEvent) => void

/** GET /api/conversations/{id}/sync 的 wire 契约（只声明 Web 端消费的字段）。 */
interface SyncResponse {
  settled_seq?: number
  run_state?: 'active' | 'idle' | 'unknown'
  run?: RunProjection | null
  messages?: MessageHistoryEntry[]
  has_more?: boolean
  history_reset?: boolean
}

interface UseChatSocketResult {
  wsState: WSState
  send: (msg: WSClientMessage) => void
  forceReconnect: () => void
  wsDebug: string | null
  /** 注册通知事件回调，返回取消订阅函数。 */
  subscribeNotification: (listener: NotificationListener) => () => void
}

export function useChatSocket(
  token: string | null,
  onAuthFailure?: () => void,
): UseChatSocketResult {
  const { wsState, send, drainMessages, queueTick, conversations, setConversations, lastDisconnect, lastError } =
    useWebSocket(token, onAuthFailure)

  const setConvList = useSessionStore((s) => s.setConversations)
  const failedReconnectsRef = useRef(0)
  const failedReconnectsTickRef = useRef(0)
  const lastWsStateRef = useRef<WSState>('disconnected')
  // 每会话「最后一次收到任意帧」的时刻。看门狗据此判断静默，而不是按任务
  // 启动后的绝对运行时长——长任务只要持续吐帧就绝不触发对账。
  const lastFrameAtRef = useRef<Map<string, number>>(new Map())
  // 需要主动补差的会话：断线时仍处于 live 的、或收到 chat.start 的。
  // 与 /conversations/running 合并后逐个 sync，取代旧的 /run N+1 拉取。
  const needsReconcileRef = useRef<Set<string>>(new Set())
  // 同会话 single-flight：在途时只记一个 trailing 标记，完成后补跑一次。
  const syncInFlightRef = useRef<Set<string>>(new Set())
  const syncTrailingRef = useRef<Set<string>>(new Set())
  // 通知监听器集合：用 ref 持有，dispatchServerMessage 内可稳定引用，
  // 监听器注册/卸载不会触发 dispatch 重建。
  const notificationListenersRef = useRef<Set<NotificationListener>>(new Set())

  // 每会话本轮的终态通知状态：后端异常路径会先发 error 再发 chat.done，
  // chat.done 也会因可靠重投递重复到达；据此抑制同一轮的重复终态推送。
  // chat.start 时重置，表示新一轮开始。
  const terminalNotificationRef = useRef<Map<string, 'done' | 'error'>>(new Map())

  // 通知事件派发：监听器异常隔离，不影响主消息流。
  const emitNotification = useCallback((event: NotificationEvent) => {
    notificationListenersRef.current.forEach((fn) => {
      try {
        fn(event)
      } catch {
        /* 监听器异常不影响主流程 */
      }
    })
  }, [])

  // 参考来源 / 追问 / 交付物等元数据帧由 done 之后的旁路任务推送，可能早于
  // 定稿消息进入权威层（done 不再在本地建消息）。按 message_id 暂存，等 sync
  // 写入后再合并；否则这些实时元数据会挂到不存在的 id 上而丢失。
  const pendingMetadataRef = useRef<Map<string, Partial<MessageMetadata>>>(new Map())

  const mergeOrBufferMetadata = useCallback(
    (convId: string, messageId: string, patch: Partial<MessageMetadata>) => {
      const state = useChatStore.getState()
      const exists = (state.convStates[convId]?.messages ?? []).some((m) => m.id === messageId)
      if (exists) {
        state.mergeMessageMetadata(convId, messageId, patch)
        return
      }
      pendingMetadataRef.current.set(messageId, {
        ...(pendingMetadataRef.current.get(messageId) ?? {}),
        ...patch,
      })
    },
    [],
  )

  const flushPendingMetadata = useCallback((convId: string) => {
    if (pendingMetadataRef.current.size === 0) return
    const state = useChatStore.getState()
    const ids = new Set((state.convStates[convId]?.messages ?? []).map((m) => m.id))
    for (const [messageId, patch] of Array.from(pendingMetadataRef.current)) {
      if (!ids.has(messageId)) continue
      state.mergeMessageMetadata(convId, messageId, patch)
      pendingMetadataRef.current.delete(messageId)
    }
  }, [])

  // 单次补差：authority（messages + settledSeq）只在这里被服务端投影推进。
  const runSyncOnce = useCallback(
    async (convId: string): Promise<void> => {
      if (!token) return
      // 只有历史已加载才允许补差：historyStatus 是"权威层基线已建立"的唯一判据。
      // 不能用 settledSeq > 0 判断——全新会话的历史基线本来就是空的，水位为 0
      // 也必须能 sync，否则第一轮回答永远进不了权威层（下个 delta 就会把叠加层
      // 清掉，表现为"回答刚出来就消失"）。
      const convState = useChatStore.getState().convStates[convId]
      if (!convState || convState.historyStatus !== 'loaded') {
        // 基线未建立：先记住待对账，看门狗会在 loadMessagesFor 完成后补一次。
        // 直接丢弃会让"加载历史期间到达的 done"永远失去定稿正文。
        needsReconcileRef.current.add(convId)
        return
      }
      const afterSeq = convState.settledSeq ?? 0
      try {
        const res = await fetch(
          apiUrl(`/api/conversations/${encodeURIComponent(convId)}/sync?after_seq=${afterSeq}`),
          { headers: { Authorization: `Bearer ${token}` } },
        )
        if (res.status === 404) {
          // 会话已删除：与 /messages 404 同口径，清叠加层并停止对账。
          useChatStore.getState().clearLive(convId)
          needsReconcileRef.current.delete(convId)
          return
        }
        if (!res.ok) {
          // 网络/服务端故障：保留本地全部内容，等下一次触发重试（绝不清空）。
          console.warn(`sync failed for ${convId}: HTTP ${res.status}`)
          return
        }
        const data = (await res.json()) as SyncResponse
        if (data.history_reset) {
          // 本地 seq 已失效（服务端会话被清理后同名复用）：清零并回到未加载态，
          // 由 ensureHistory 重新建立基线（直接留空会让会话一直白屏）。
          useChatStore.getState().setMessages(convId, [], { settledSeq: 0 })
          useChatStore.getState().setHistoryStatus(convId, 'idle')
          // 保留待对账标记：基线重建后仍需用新水位对一次账，否则重置瞬间
          // 正在跑的 run 不会水合，要等下一次 done/重连。
          return
        }
        const chat = useChatStore.getState()
        // in_progress 的消息由 mapHistoryEntries 滤掉，不落权威层、不推进水位。
        const settled = mapHistoryEntries(Array.isArray(data.messages) ? data.messages : [])
        chat.applySettledMessages(convId, settled, data.settled_seq ?? afterSeq)
        // 定稿消息已进入权威层：把早到的元数据帧补挂上（参考来源/追问/交付物）。
        flushPendingMetadata(convId)
        if (data.run_state === 'active' && data.run) {
          chat.hydrateRun(convId, data.run)
        } else if (data.run_state === 'idle') {
          // 服务端确认收尾：定稿消息已先一步进入权威层，此刻清叠加层不会留下空白。
          chat.clearLive(convId)
        }
        // unknown：运行态无法确认，保留叠加层不动（多副本边界），并留在
        // 待对账集合里继续观察；确定的 active/idle 才算收敛。
        if (data.run_state === 'active' || data.run_state === 'idle') {
          needsReconcileRef.current.delete(convId)
        }
        // 超过单页上限：settled_seq 已推进到本页末尾，借 trailing 机制再拉下一页。
        // 水位没前进时必须停下——否则会在同一段区间反复请求（in_progress 挡住水位
        // 而分页又要求继续的极端情形）。
        if (data.has_more && (data.settled_seq ?? afterSeq) > afterSeq) {
          syncTrailingRef.current.add(convId)
        }
      } catch (err) {
        // 只有状态失败，不清任何内容——内容消失的根因正是"失败也清"。
        console.warn(`sync failed for ${convId}`, err)
      }
    },
    [token, flushPendingMetadata],
  )

  // 唯一补差入口：同会话 single-flight，在途期间的重复触发合并为一次补跑。
  const syncConversation = useCallback(
    async (convId: string): Promise<void> => {
      if (syncInFlightRef.current.has(convId)) {
        syncTrailingRef.current.add(convId)
        return
      }
      syncInFlightRef.current.add(convId)
      try {
        do {
          syncTrailingRef.current.delete(convId)
          await runSyncOnce(convId)
          // 补跑的目的是让"在途期间新到达的 done"不再丢失；一次足以收敛。
        } while (syncTrailingRef.current.delete(convId))
      } finally {
        syncInFlightRef.current.delete(convId)
      }
    },
    [runSyncOnce],
  )

  useEffect(() => {
    setConvList(conversations)
  }, [conversations, setConvList])

  useEffect(() => {
    const prev = lastWsStateRef.current
    lastWsStateRef.current = wsState

    if (wsState === 'ready') {
      if (failedReconnectsRef.current !== 0) {
        failedReconnectsRef.current = 0
        failedReconnectsTickRef.current += 1
      }

      // WS ready 是唯一的重连补差时机：
      // 1) /conversations/running 覆盖"后台会话正在跑但前端没打开"的情况；
      // 2) needsReconcile 覆盖断线前处于 live、或刚 chat.start 的会话；
      // 3) 当前打开会话无条件试一次（历史尚未加载时会自行跳过，
      //    首次全量仍由 loadMessagesFor 负责）。
      // 运行态与正文都来自 sync 的同一份服务端投影，不再逐个 GET /run（移动端仍用它）。
      const reconcile = async (bearer: string | null) => {
        if (!bearer) return
        const ids = new Set(needsReconcileRef.current)
        const activeConvId = useSessionStore.getState().activeConvId
        if (activeConvId) ids.add(activeConvId)
        try {
          // 用户级查询：刷新瞬间 activeWorkspaceId 尚未恢复，按工作区过滤会漏会话。
          const res = await fetch(apiUrl('/api/conversations/running'), {
            headers: { Authorization: `Bearer ${bearer}` },
          })
          if (res.ok) {
            const data = await res.json()
            const running: string[] = Array.isArray(data.conversations) ? data.conversations : []
            for (const convId of running) ids.add(convId)
          }
        } catch {
          /* running 列表失败不阻断已知集合的补差 */
        }
        for (const convId of ids) await syncConversation(convId)
      }
      void reconcile(token)

      return
    }

    if (
      wsState === 'disconnected' &&
      (prev === 'ready' || prev === 'connecting' || prev === 'identifying')
    ) {
      failedReconnectsRef.current += 1
      failedReconnectsTickRef.current += 1

      // 断连只标记对账、绝不清空。运行是服务端独立任务，socket 断开通常只意味
      // 本条连接死亡（页面重载 / HMR / 网络抖动 / 后端重启）：此处清空叠加层会
      // 让内容当场消失，而服务端一直有——重连后由 ready 分支 sync 补回。
      const states = useChatStore.getState().convStates
      for (const [convId, conv] of Object.entries(states)) {
        if (conv.isStreaming) needsReconcileRef.current.add(convId)
      }
    }
  }, [wsState, token, syncConversation])

  const dispatchServerMessage = useCallback((msg: WSServerMessage) => {
    const chat = useChatStore.getState()
    const session = useSessionStore.getState()
    const convId =
      'conversation_id' in msg ? msg.conversation_id : null

    // 任何带 conversation_id 的帧都刷新「最后活动时刻」，供看门狗静默判定。
    if (convId) lastFrameAtRef.current.set(convId, Date.now())

    const extractMetaList = <T>(data: any, field: string, filterFn: (item: any) => boolean): T[] => {
      const raw = data?.[field]
      return Array.isArray(raw) ? (raw.filter(filterFn) as T[]) : []
    }

    // 只认帧里显式的 message_id。done 不再在本地建消息，若在这里回退到
    // "最后一条 assistant"，早到的元数据会挂到**上一轮**的回答上（错误归属
    // 比丢弃更难排查）。缺 id 时宁可不显示，等 sync 投影自带的元数据。
    const resolveTargetMessageId = (data: any): string | null =>
      typeof data?.message_id === 'string' && data.message_id ? data.message_id : null

    switch (msg.type) {
      case 'chat.start':
        if (convId) {
          // 新任务开始：无条件清掉上一轮残留的执行过程状态。
          // 后端保证 chat.start 在所有 task.timeline / task.runtime / chat.delta 之前发出。
          chat.resetExecutionState(convId)
          // 这一轮尚未定稿：记入对账集合，断线/重连后能主动补回正文。
          needsReconcileRef.current.add(convId)
          // 新一轮开始：允许本轮终态再次推送。
          terminalNotificationRef.current.delete(convId)
        }
        break

      case 'chat.delta':
        // 只写 live 叠加层：允许丢失、不做权威判断（正文由 sync 投影给出）。
        if (convId) chat.initStreaming(convId)
        if (convId) chat.appendDelta(convId, msg.data.text, msg.data.segmentId)
        break

      case 'chat.done': {
        const aborted = msg.data.aborted === true

        if (convId) {
          // 仅保留"本轮曾暂停澄清"的标记：sync 会清 runGate，必须先读。
          const wasAwaitingClarify = chat.convStates[convId]?.runGate === 'awaiting_clarification'
          // 正文、元数据、时间线一律不再由 done 合成——chat.done 只是"去服务端对账"的信号。
          if (wasAwaitingClarify && typeof msg.data.message_id === 'string' && msg.data.message_id) {
            chat.markMsgClarified(convId, msg.data.message_id)
          }
          // 标记未读（如果当前不在看这个 conv）
          const activeConvId = useSessionStore.getState().activeConvId
          if (convId !== activeConvId) {
            useChatStore.getState().markConvAsUnread(convId)
          }
          // 派发「任务完成」通知事件（桌面通知等副作用消费）。
          // 本轮已推过终态（error 先到 / done 重投递）时不再重复推送。
          const prevTerminal = terminalNotificationRef.current.get(convId)
          if (prevTerminal !== 'done' && prevTerminal !== 'error') {
            emitNotification({ kind: 'done', convId, aborted })
          }
          terminalNotificationRef.current.set(convId, 'done')
          // done 只是"去对账"的信号：定稿正文经 sync 进入权威层；
          // 叠加层何时清空由 sync 的 run_state 决定，不在本地收尾。
          void syncConversation(convId)
        }
        break
      }

      case 'event:reference': {
        const sources = extractMetaList<string>(msg.data, 'sources', (s) => typeof s === 'string' && s.trim().length > 0)
        const targetId = convId ? resolveTargetMessageId(msg.data) : null
        if (targetId && sources.length > 0) {
          mergeOrBufferMetadata(convId!, targetId, { referenceSources: sources })
        }
        break
      }

      case 'event:questions': {
        const questions = extractMetaList<string>(msg.data, 'questions', (q) => typeof q === 'string' && q.trim().length > 0)
        const targetId = convId ? resolveTargetMessageId(msg.data) : null
        if (targetId && questions.length > 0) {
          mergeOrBufferMetadata(convId!, targetId, { followupQuestions: questions.slice(0, 3) })
        }
        break
      }

      case 'task.runtime':
        if (convId) {
          const projection = msg.data.projection ?? null
          chat.setTaskRuntime(
            convId,
            projection,
            msg.data.event_seq,
            msg.data.idempotency_key,
          )
        }
        break

      case 'task.timeline':
        if (convId) {
          // 快照整体替换 / patch best-effort 合并，由纯函数 nextTimelineState 承担。
          chat.applyTimelineFrame(convId, {
            nodes: msg.data.nodes,
            patch: msg.data.patch,
          })
        }
        break

      case 'run.snapshot':
        // phase 窗口周期下发的全量运行态快照——整体替换 live 叠加层，
        // 与 sync 的 run 共用同一 hydrateRun 路径（单一重建入口）。
        if (convId) {
          chat.hydrateRun(convId, msg.data)
        }
        break

      case 'event:outbox': {
        const files = extractMetaList<string>(msg.data, 'files', (p) => typeof p === 'string' && p.trim().length > 0)
        const targetId = convId ? resolveTargetMessageId(msg.data) : null
        if (targetId && files.length > 0) {
          mergeOrBufferMetadata(convId!, targetId, { outputFiles: files })
        }
        break
      }

      case 'event:file_changes': {
        const changes = extractMetaList<any>(msg.data, 'changes', (c) => c && typeof c === 'object' && typeof c.path === 'string')
        const targetId = convId ? resolveTargetMessageId(msg.data) : null
        if (targetId && changes.length > 0) {
          // Stamp message_id onto each change so the review UI can persist a
          // per-change decision (POST file-review-mark) without re-deriving it.
          const stamped = changes.map((c) => ({ ...c, message_id: targetId }))
          mergeOrBufferMetadata(convId!, targetId, { fileChanges: stamped })
        }
        break
      }

      case 'chat.queued':
        if (convId) chat.setQueued(convId, true, msg.data.position)
        break

      case 'tool.clarify_request':
        // AskUserQuestion 暂停信号：把 approval_id / tool_call_id / questions 写入
        // conv 级 clarifyRequest 状态，供 ChatArea 渲染提问卡片并在同轮续跑。
        // 必须放在 conv 级而非 messages metadata：执行中该工具调用落在合成
        // STREAMING_ID 流式消息（liveToolCalls）上，持久化 messages 数组里尚
        // 无此 assistant 消息，写 metadata 会落到错误的历史消息上。
        if (convId) {
          const approvalId = msg.data.approval_id
          const toolCallId = msg.data.tool_call_id
          if (approvalId) {
            chat.setClarifyRequest(convId, {
              approvalId,
              toolCallId,
              questions: Array.isArray(msg.data.questions) ? msg.data.questions : [],
            })
            // 澄清提问需要用户回答，立即推送通知。
            emitNotification({
              kind: 'clarify',
              convId,
              questionCount: Array.isArray(msg.data.questions) ? msg.data.questions.length : 0,
            })
          }
        }
        break

      case 'error': {
        if (convId) {
          const errMsg: Message = {
            id: `err_${Date.now()}`,
            role: 'error',
            content: msg.data.message || 'Internal error',
            timestamp: Date.now(),
          }
          chat.appendMessage(convId, errMsg)
          // 异常终止同样需要用户知晓；同一轮重复 error 不再重复推送。
          if (terminalNotificationRef.current.get(convId) !== 'error') {
            emitNotification({
              kind: 'error',
              convId,
              message: msg.data.message || 'Internal error',
            })
          }
          terminalNotificationRef.current.set(convId, 'error')
        }
        break
      }

      case 'ready':
        if (Array.isArray(msg.data.conversations) && msg.data.conversations.length > 0) {
          session.setConversations(msg.data.conversations)
          setConversations(msg.data.conversations)
        }
        break

      case 'run.keepalive':
        // executing 期心跳：仅刷新 lastFrameAtRef（本函数入口对任何带
        // conversation_id 的帧已统一处理），无数据消费、不渲染。
        break

      default:
        if (msg.type.startsWith('domain_task.')) {
          // 域任务事件：直接写入 App 级任务 store，视图是投影。
          // 前缀分发与 scheduled_task. 先例同构；帧不含 conversation_id，不走会话链路。
          useTaskStore.getState().applyEventFrame((msg as WSDomainTaskUpdated).data)
          return
        }
        if (typeof msg.type === 'string' && msg.type.startsWith('scheduled_task.')) {
          window.dispatchEvent(new CustomEvent('prismclaw:scheduler-event', { detail: msg }))
        }
        break
    }
  }, [setConversations, emitNotification, syncConversation, mergeOrBufferMetadata])

  // 按到达顺序处理本轮积压的全部帧。必须 drain 整批而非取最后一条：
  // 同一渲染周期内到达的多帧若只处理末尾一帧，中间的 chat.delta /
  // task.timeline / task.runtime 会被静默丢弃（见 useWebSocket 队列注释）。
  useEffect(() => {
    if (queueTick === 0) return
    for (const msg of drainMessages()) dispatchServerMessage(msg)
  }, [queueTick, drainMessages, dispatchServerMessage])

  // 看门狗只负责"怀疑"，不负责"判决"：流式会话静默超过 30s 就向服务端对账，
  // 是否收尾由 sync 返回的 run_state 决定。绝不在本地合成"中断"消息或清空正文——
  // 那正是"内容一会没了一会又出来"的根因。判定基准是静默时长（每次收到帧刷新），
  // 长任务只要持续吐帧就不会触发。
  useEffect(() => {
    const WATCHDOG_INTERVAL_MS = 30_000
    const STUCK_THRESHOLD_MS = 30_000
    const timer = setInterval(() => {
      const state = useChatStore.getState()
      const now = Date.now()
      for (const [convId, conv] of Object.entries(state.convStates)) {
        if (!conv.isStreaming) continue
        // 以「最后收到帧的时刻」为基准；首次见到的会话先记一个起点。
        const lastFrameAt = lastFrameAtRef.current.get(convId)
        if (!lastFrameAt) {
          lastFrameAtRef.current.set(convId, now)
          continue
        }
        if (now - lastFrameAt > STUCK_THRESHOLD_MS) {
          // 失败时 runSyncOnce 保留现状，下个周期继续重试。
          void syncConversation(convId)
        }
      }
      // 待对账集合：覆盖"done 到达时历史尚未加载完"与 unknown 运行态，
      // 否则它们没有任何后续触发点（幂等，成功后自动移出）。
      for (const convId of Array.from(needsReconcileRef.current)) {
        if (state.convStates[convId]?.historyStatus === 'loaded') {
          void syncConversation(convId)
        }
      }
    }, WATCHDOG_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [syncConversation])

  // 后台标签页被节流期间帧可能整段丢失：重新可见时对需要补差的会话再对一次账。
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState !== 'visible') return
      const ids = new Set(needsReconcileRef.current)
      const activeConvId = useSessionStore.getState().activeConvId
      if (activeConvId) ids.add(activeConvId)
      for (const convId of ids) void syncConversation(convId)
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [syncConversation])

  const forceReconnect = useCallback(() => {
    failedReconnectsRef.current = 0
    failedReconnectsTickRef.current += 1
    if (typeof window !== 'undefined') {
      window.location.reload()
    }
  }, [])

  // 注册通知事件监听器，返回取消订阅函数。
  const subscribeNotification = useCallback((listener: NotificationListener) => {
    const set = notificationListenersRef.current
    set.add(listener)
    return () => {
      set.delete(listener)
    }
  }, [])

  return {
    wsState,
    send,
    forceReconnect,
    subscribeNotification,
    wsDebug:
      lastError
        ? lastError
        : lastDisconnect
          ? `WS close ${lastDisconnect.code}${lastDisconnect.reason ? `: ${lastDisconnect.reason}` : ''} (${new Date(lastDisconnect.at).toLocaleTimeString()})\n${lastDisconnect.url}`
          : null,
  }
}
