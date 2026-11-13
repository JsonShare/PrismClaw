import { useCallback, useEffect, useRef } from 'react'
import { useWebSocket } from '../useWebSocket'
import { useChatStore } from '../../stores/chat'
import { useSessionStore } from '../../stores/session'
import { useTaskStore } from '../../stores/tasks'
import { apiUrl } from '../../lib/system-adapter'
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

/** 断连对账历史遗留的占位消息前缀。新版 hydrateRunning 已不再补这类占位，
    仅保留识别以兼容存量会话里已存在的 asst_recovered_ 消息。 */
const RECOVERED_MESSAGE_ID_PREFIX = 'asst_recovered_'

/** 本地合成占位消息的前缀集合：看门狗（stream-watchdog-）在服务端尚未收尾时先落
    一条 interrupted 部分消息；后端用户级重投递让 chat.done 变为可靠送达，这些占位
    必须在其到达时被原地替换，否则同一条回答会在气泡里并列出现两遍。asst_recovered_
    （断连对账历史遗留）与更旧的 stream-partial- 一并识别，兼容存量会话。 */
const PLACEHOLDER_MESSAGE_ID_PREFIXES = [
  RECOVERED_MESSAGE_ID_PREFIX,
  'stream-watchdog-',
  'stream-partial-',
]

function isPlaceholderMessageId(id: string): boolean {
  return PLACEHOLDER_MESSAGE_ID_PREFIXES.some((prefix) => id.startsWith(prefix))
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
  const streamStartRef = useRef<Map<string, number>>(new Map())
  // 每会话「最后一次收到任意帧」的时刻。看门狗据此判断卡死，而不是按任务
  // 启动后的绝对运行时长——长任务只要持续吐帧就绝不强行 finalizeStreaming
  // （否则每 5 分钟会把正在执行的任务正文+思考一起清空，即用户报的抖动）。
  const lastFrameAtRef = useRef<Map<string, number>>(new Map())
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

      // ★ 刷新 / 重连后从后端内存权威源重放进行中的 run（P2）
      // 后端内存投影是运行态唯一真相源：页面加载与每次 WS ready，都对「运行中」
      // 会话拉取 GET /run，用权威快照整体重建 ConvState（hydrateRun）。不再像旧
      // restoreRunning 那样只重挂「内存已存在」的会话（刷新后 zustand 内存为空 →
      // 永不重挂，即"刷新后执行过程丢失"），也不在本地对「疑似已结束」的会话
      // finalize（那会与迟到 chat.done 的权威正文叠加成重复气泡）：会话确已结束时
      // GET /run 返回 null，此处 no-op，收尾交给可靠重投递的 chat.done / 看门狗
      // 静默兜底。
      // 查询为用户级（不带 workspace_id）：running 列表由后端内存权威源给出，
      // 刷新瞬间 activeWorkspaceId 尚未恢复（初始 'default'），按工作区过滤会
      // 漏掉非默认工作区的运行中会话——曾表现为「刷新后长任务从 UI 消失」。
      const hydrateRunning = async (bearer: string | null) => {
        if (!bearer) return
        try {
          const res = await fetch(apiUrl('/api/conversations/running'), {
            headers: { Authorization: `Bearer ${bearer}` },
          })
          if (!res.ok) return
          const data = await res.json()
          const running: string[] = data.conversations ?? []
          for (const convId of running) {
            try {
              const runRes = await fetch(apiUrl(`/api/conversations/${encodeURIComponent(convId)}/run`), {
                headers: { Authorization: `Bearer ${bearer}` },
              })
              if (!runRes.ok) continue
              const runData = await runRes.json()
              const run = runData.run as RunProjection | null
              if (!run) continue
              useChatStore.getState().hydrateRun(convId, run)
            } catch {
              /* 单会话拉取失败不影响其余会话 */
            }
          }
        } catch {
          /* ignore */
        }
      }
      hydrateRunning(token)

      return
    }

    if (
      wsState === 'disconnected' &&
      (prev === 'ready' || prev === 'connecting' || prev === 'identifying')
    ) {
      failedReconnectsRef.current += 1
      failedReconnectsTickRef.current += 1

      // 断连不再就地收尾。运行是服务端独立任务，socket 断开通常只意味本条连接
      // 死亡（页面重载 / HMR / 网络抖动 / 后端重启），后端会把后续帧重投给该用户
      // 新建立的连接（见 connection.send_event 的用户级重投递）。此处若像以前那样
      // 合成 interrupted 部分消息并 finalizeStreaming，会同时造成两个问题：
      //   1) streamingText / timeline / activeTaskRuntime 当场清空，执行过程消失；
      //      重连后本应重建而非丢失——这正是 hydrateRunning（GET /run 全量重放）
      //      要解决的；此处不 finalize，才能在 WS ready 时把后端快照整块重建回来；
      //   2) 合成的部分消息与随后送达的 chat.done.final_message（全量正文）叠加，
      //      同一条回答在气泡里出现两遍。
      // 故这里只保留流式状态：正常收尾交给 chat.done；刷新/重连后由 hydrateRunning
      // 从后端 SSOT 重放（已结束的会话 GET /run 返回 null，自然 no-op）；长时间收不到
      // chat.done 仍有看门狗静默兜底。
    }
  }, [wsState])

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

    const resolveTargetMessageId = (data: any, convId: string): string | null => {
      const messageId =
        typeof data?.message_id === 'string' && data.message_id
          ? (data.message_id as string)
          : null
      if (messageId) return messageId
      const current = chat.convStates[convId]?.messages ?? []
      const lastAssistant = [...current].reverse().find((m) => m.role === 'assistant') ?? null
      return lastAssistant ? lastAssistant.id : null
    }

    switch (msg.type) {
      case 'chat.start':
        if (convId) {
          // 新任务开始：无条件清掉上一轮残留的执行过程状态。
          // 后端保证 chat.start 在所有 task.timeline / task.runtime / chat.delta 之前发出。
          streamStartRef.current.set(convId, Date.now())
          chat.resetExecutionState(convId)
          // 新一轮开始：允许本轮终态再次推送。
          terminalNotificationRef.current.delete(convId)
        }
        break

      case 'chat.delta':
        if (convId && !streamStartRef.current.has(convId)) {
          streamStartRef.current.set(convId, Date.now())
        }
        if (convId) chat.initStreaming(convId)
        if (convId) chat.appendDelta(convId, msg.data.text, msg.data.segmentId)
        break

      case 'chat.done': {
        // 全程时间：用户发送消息（最后一条 user 消息的 timestamp）→ 助手回复完成。
        // fallback 到 streamStartRef 兼容极少数 conv 创建瞬间 race（user msg 尚未落库）。
        let startedAt: number | null = null
        if (convId) {
          const msgs = chat.convStates[convId]?.messages ?? []
          const lastUserMsg = [...msgs].reverse().find((m) => m.role === 'user') ?? null
          startedAt = lastUserMsg?.timestamp ?? streamStartRef.current.get(convId) ?? null
          streamStartRef.current.delete(convId)
        }
        const metadata = buildMetadata(startedAt, msg.data)
        const aborted = msg.data.aborted === true

        if (convId) {
          // finalizeStreaming 前快照：本回合 runGate 曾为 awaiting_clarification
          // → 需要把落库 assistant 消息标记为已澄清，防止刷新后卡片复现
          const wasAwaitingClarify = chat.convStates[convId]?.runGate === 'awaiting_clarification'
          // 在 finalizeStreaming 清空 activeTaskRuntime 之前，保存工具调用数据
          const runtimeBefore = chat.convStates[convId]?.activeTaskRuntime
          const savedToolCalls = runtimeBefore?.tool_calls ?? []
          const savedSubagentTrace = runtimeBefore?.subagent_trace ?? []
          // 正文真相源：优先用后端 chat.done.final_message。按新协议
          // final_message 已是全部迭代的完整正文（所有轮次 prose 拼接），
          // 而非仅最后一轮答案。与 reload 路径口径完全一致（live 与刷新后
          // 历史回放单一真相源，避免双真相源导致"正文两遍答案"）。
          // streamingText（实时全轮拼接）仅作为 final_message 缺失（中断/异常）
          // 时的兜底，保证中止场景下仍有可见文本落库。
          const { text, timeline } = chat.finalizeStreaming(convId)
          // finalizeStreaming 已统一清 runGate / clarifyRequest / clarifySession，
          // 此处不再额外调 setClarifyRequest——避免双写产生不必要的 set 开销。
          const finalMessage = msg.data.final_message
          const finalText = typeof finalMessage === 'string' ? finalMessage : ''
          // 正文 = 干净最终答案；无最终答案（中断/异常）才回退到全程拼接文本。
          const bodyContent = finalText.trim().length > 0 ? finalText : text
          const serverMessageId =
            typeof finalMessage === 'object' &&
            finalMessage !== null &&
            typeof (finalMessage as any).id === 'string' &&
            (finalMessage as any).id
              ? ((finalMessage as any).id as string)
              : typeof (msg.data as any).message_id === 'string'
                ? ((msg.data as any).message_id as string)
                : null

          // 本轮是否已被本地占位消息（断连对账 / 看门狗）先行收尾：若它仍是本轮
          // 末尾那条消息，则复用其 id 让权威正文原地替换（upsertMessage），而不是
          // 并列成第二条回答；其保存的执行时间线在此回填，归一后执行过程不丢。
          // 判据取"末尾消息"而非任意历史占位，避免误改以往回合的收尾消息。
          const tailMsg =
            (chat.convStates[convId]?.messages ?? []).slice(-1)[0] ?? undefined
          const recovered =
            tailMsg && isPlaceholderMessageId(tailMsg.id) ? tailMsg : undefined
          const effectiveTimeline = timeline.length > 0 ? timeline : (recovered?.timeline ?? [])

          if (aborted && bodyContent.trim().length > 0) {
            const partialMsg: Message = {
              id: recovered?.id || `asst_partial_${Date.now()}`,
              role: 'assistant',
              content: bodyContent,
              timestamp: Date.now(),
              toolCalls: savedToolCalls.length > 0 ? savedToolCalls : undefined,
              subagentTrace: savedSubagentTrace.length > 0 ? savedSubagentTrace : undefined,
              timeline: effectiveTimeline.length > 0 ? effectiveTimeline : undefined,
              metadata,
            }
            chat.upsertMessage(convId, partialMsg)
          } else if (bodyContent.trim().length > 0) {
            const finalMsg: Message = {
              id: serverMessageId || recovered?.id || `asst_${Date.now()}`,
              role: 'assistant',
              content: bodyContent,
              timestamp: Date.now(),
              toolCalls: savedToolCalls.length > 0 ? savedToolCalls : undefined,
              subagentTrace: savedSubagentTrace.length > 0 ? savedSubagentTrace : undefined,
              timeline: effectiveTimeline.length > 0 ? effectiveTimeline : undefined,
              metadata,
            }
            chat.upsertMessage(convId, finalMsg)
          } else if (effectiveTimeline.length > 0) {
            // 正文为空但产出执行过程的回合（纯工具调用 / 仅交付物 / 空答案）：仍落一条
            // assistant 消息承载 timeline。否则 chat.done 后 finalizeStreaming 清空
            // liveTimeline，reload 时该消息又因正文为空被 useSessionLoader 过滤丢弃，
            // 表现为「切换菜单再切回任务，执行过程不显示」。仅当 timeline 非空才落库，
            // 与 reload 过滤器（hasVisibleContent || timeline 非空）口径一致。
            const emptyMsg: Message = {
              id: serverMessageId || recovered?.id || `asst_${Date.now()}`,
              role: 'assistant',
              content: '',
              timestamp: Date.now(),
              toolCalls: savedToolCalls.length > 0 ? savedToolCalls : undefined,
              subagentTrace: savedSubagentTrace.length > 0 ? savedSubagentTrace : undefined,
              timeline: effectiveTimeline,
              metadata,
            }
            chat.upsertMessage(convId, emptyMsg)
          }
          // 本回合曾暂停澄清：把落库消息标记为已澄清，防止 chat.done 后回落到
          // messages 解析路径时卡片复现（双真相源问题）。
          if (wasAwaitingClarify && serverMessageId) {
            chat.markMsgClarified(convId, serverMessageId)
          }
        }
        // convId 为 null 时不需要清理（没有对应的流式状态）
        if (convId) {
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
        }
        break
      }

      case 'event:reference': {
        const sources = extractMetaList<string>(msg.data, 'sources', (s) => typeof s === 'string' && s.trim().length > 0)
        const targetId = convId ? resolveTargetMessageId(msg.data, convId) : null
        if (targetId && sources.length > 0) {
          chat.mergeMessageMetadata(convId!, targetId, { referenceSources: sources })
        }
        break
      }

      case 'event:questions': {
        const questions = extractMetaList<string>(msg.data, 'questions', (q) => typeof q === 'string' && q.trim().length > 0)
        const targetId = convId ? resolveTargetMessageId(msg.data, convId) : null
        if (targetId && questions.length > 0) {
          chat.mergeMessageMetadata(convId!, targetId, { followupQuestions: questions.slice(0, 3) })
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
        // P3：phase 窗口周期下发的全量运行态快照——整体替换 streaming 运行态，
        // 与 GET /run 重放共用同一 hydrateRun 路径（SSOT 单一重建入口）。
        if (convId) {
          chat.hydrateRun(convId, msg.data)
        }
        break

      case 'event:outputs': {
        const files = extractMetaList<string>(msg.data, 'files', (p) => typeof p === 'string' && p.trim().length > 0)
        const targetId = convId ? resolveTargetMessageId(msg.data, convId) : null
        if (targetId && files.length > 0) {
          chat.mergeMessageMetadata(convId!, targetId, { outputFiles: files })
        }
        break
      }

      case 'event:file_changes': {
        const changes = extractMetaList<any>(msg.data, 'changes', (c) => c && typeof c === 'object' && typeof c.path === 'string')
        const targetId = convId ? resolveTargetMessageId(msg.data, convId) : null
        if (targetId && changes.length > 0) {
          // Stamp message_id onto each change so the review UI can persist a
          // per-change decision (POST file-review-mark) without re-deriving it.
          const stamped = changes.map((c) => ({ ...c, message_id: targetId }))
          chat.mergeMessageMetadata(convId!, targetId, { fileChanges: stamped })
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
        if (convId) streamStartRef.current.delete(convId)
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
  }, [setConversations, emitNotification])

  // 按到达顺序处理本轮积压的全部帧。必须 drain 整批而非取最后一条：
  // 同一渲染周期内到达的多帧若只处理末尾一帧，中间的 chat.delta /
  // task.timeline / task.runtime 会被静默丢弃（见 useWebSocket 队列注释）。
  useEffect(() => {
    if (queueTick === 0) return
    for (const msg of drainMessages()) dispatchServerMessage(msg)
  }, [queueTick, drainMessages, dispatchServerMessage])

  // Safety net: force-clear streaming sessions that have been SILENT for >5min
  // without a chat.done. This handles the edge case where the backend's chat.done
  // event is lost over the WebSocket connection and the UI would otherwise remain
  // stuck indefinitely.
  //
  // 关键修正：判定基准是「静默时长」（lastFrameAtRef，由每次收到帧刷新），
  // 而非「任务启动后的绝对运行时长」（streamStartRef）。长任务只要持续收到帧
  // （chat.delta / task.timeline / task.runtime …）就永不被强收；否则原本逻辑会在
  // 任务跑满 5 分钟时调用 finalizeStreaming，把正在执行的正文+思考一次性清空，
  // 造成「一会没了一会又出来」的抖动（background 长任务必现）。
  useEffect(() => {
    const WATCHDOG_INTERVAL_MS = 30_000
    const STUCK_THRESHOLD_MS = 5 * 60 * 1000
    const timer = setInterval(() => {
      const state = useChatStore.getState()
      const now = Date.now()
      let cleared = false
      for (const [convId, conv] of Object.entries(state.convStates)) {
        if (!conv.isStreaming) continue
        // 以「最后收到帧的时刻」为基准；首次见到的会话先记一个起点。
        const lastFrameAt = lastFrameAtRef.current.get(convId)
        if (!lastFrameAt) {
          lastFrameAtRef.current.set(convId, now)
          continue
        }
        if (now - lastFrameAt > STUCK_THRESHOLD_MS) {
          // 与 chat.done / error 同口径：强收前捕获正文 + 执行时间线 + 工具调用/
          // 子代理轨迹。此前只留 streamingText、丢弃 timeline/toolCalls，静默兜底
          // 触发时同样会把执行过程清空（与 error 分支同一类丢失根因）。
          const runtimeBefore = conv.activeTaskRuntime
          const savedToolCalls = runtimeBefore?.tool_calls ?? []
          const savedSubagentTrace = runtimeBefore?.subagent_trace ?? []
          const { text, timeline } = state.finalizeStreaming(convId)
          if (text.trim() || timeline.length > 0) {
            state.appendMessage(convId, {
              id: `stream-watchdog-${now}`,
              role: 'assistant',
              content: text,
              timestamp: now,
              toolCalls: savedToolCalls.length > 0 ? savedToolCalls : undefined,
              subagentTrace: savedSubagentTrace.length > 0 ? savedSubagentTrace : undefined,
              timeline: timeline.length > 0 ? timeline : undefined,
              metadata: { interrupted: true },
            })
          }
          lastFrameAtRef.current.delete(convId)
          cleared = true
        }
      }
      if (cleared) {
        console.warn('watchdog: cleared stuck streaming sessions')
      }
    }, WATCHDOG_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [])

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

function buildMetadata(
  startedAt: number | null,
  data: {
    usage?: { input?: number; output?: number; cache_read?: number }
    model?: string
  },
): MessageMetadata | undefined {
  const meta: MessageMetadata = {}
  if (startedAt !== null) {
    meta.durationMs = Math.max(0, Date.now() - startedAt)
  }
  if (data.usage) {
    meta.usage = {
      input: data.usage.input,
      output: data.usage.output,
      cacheRead: data.usage.cache_read,
    }
  }
  if (data.model) {
    meta.model = data.model
  }
  if (
    meta.durationMs === undefined &&
    meta.usage === undefined &&
    meta.model === undefined
  ) {
    return undefined
  }
  return meta
}
