import { create } from 'zustand'
import type {
  AttachmentBlock,
  HeartbeatPayload,
  Message,
  RunProjection,
  TaskRuntimeProjection,
  TimelineNode,
} from '../types'
import { nextTimelineState } from '../lib/chat/timelineSync'
import type { TimelineFrame } from '../lib/chat/timelineSync'

export interface OutboxItem {
  id: string
  text: string
  attachments: AttachmentBlock[]
}

const MAX_CACHED_CONVERSATIONS = 10
const UNREAD_STORAGE_KEY = 'prismclaw:unread'

// ── 单 conv 的完整状态（合并消息 + 流式 + outbox）──
export interface ConvState {
  messages: Message[]
  lastAccessAt: number
  // 流式状态（运行时，任务完成后由 finalizeStreaming 清空）
  streamingText: string
  // 当前 streamingText 属于哪个迭代段（segmentId）。chat.delta payload 带 segmentId —
  // 当收到的 segmentId 变了（进入新迭代轮次），先清 streamingText 再追加，让正文区
  // 只显示当前轮次吐字（上一轮已进 ExecutionTimeline）。这比 IterationBoundary 提前
  // 清空更准确 — 后者会在新轮次 TextChunk 到达前制造空白窗口。
  currentStreamingSegmentId: number
  // P2.4：执行心跳阶段（camelCase HeartbeatPayload），由 set_phase timeline patch 驱动。
  // 承载 executing / generating / waiting_llm 等进度语义。runGate 取代其 awaiting_* 语义。
  phase: HeartbeatPayload | null
  // 最近一次 set_phase patch 的到达时刻（Date.now()）。后端心跳帧只在相位切换/增量
  // 节点发出（elapsedS 是帧到达时的基线值），「已执行 Xs」的秒表由前端按该时刻推进
  // （usePhaseClock）。与 phase 同生命周期：phase 置空处同步置空。
  phaseAt: number | null
  // 本轮执行开始时刻（initStreaming 记下，finalizeStreaming 清空）。执行秒表的计时
  // 纪元——心跳帧到达前界面就应计数，若以 phaseAt 为纪元则该窗口内秒表恒为 0。
  runStartedAt: number | null
  // ════ HITL 人机交互状态 —— 单一真相源 ════
  // runGate: run 是否被阻塞等待用户输入。取代 phase.state 的 awaiting_clarification/
  // awaiting_confirmation 语义，clarifyRequest / confirmation 降级为对应 gate 的 payload。
  // 所有写入 clarifyRequest 的路径必须同步设置 runGate='awaiting_clarification'；
  // 所有清空 clarifyRequest 的路径必须同步设置 runGate=null。store 层保证一致性，
  // 消灭"清 clarifyRequest 忘清 phase"类补丁。
  runGate: 'awaiting_clarification' | 'awaiting_confirmation' | null
  // AskUserQuestion 人机交互：执行中途暂停时，后端经 tool.clarify_request
  // 注入的待回答提问与提交凭据。仅当 runGate === 'awaiting_clarification' 时有效。
  clarifyRequest: {
    approvalId: string
    toolCallId: string
    questions: Array<Record<string, unknown>>
  } | null
  // clarifySession: 用户在 ClarifyCard 内的选择态（selectedAnswers / currentQuestionIndex）。
  // 上提到 store 让刷新后能恢复半完成的选择流程。与 clarifyRequest 同生命周期——
  // runGate 切走时同步清空。
  clarifySession: {
    selectedAnswers: Record<number, Set<string>>
    currentQuestionIndex: number
  } | null
  // 已澄清（AskUserQuestion 已由用户回答/跳过）的 assistant 消息 id 集合。
  // 置于 conv 级而非组件局部状态，是因为回落解析路径（从持久化 messages 读取
  // AskUserQuestion）与 chat.done 落库发生在不同模块：落库时必须能标记消息，
  // 否则回合结束后卡片会因双真相源而复现。
  clarifiedMsgIds: string[]
  isStreaming: boolean
  isQueued: boolean
  queuePosition: number
  activeTaskRuntime: TaskRuntimeProjection | null
  activeTaskRuntimeEventSeq: number
  activeTaskRuntimeIdempotencyKey: string | null
  // 执行时间线：快照整体替换 + patch best-effort 合并（P3 起无序号光标，
  // 自愈由 task.runtime 快照帧 / run.snapshot 整体替换驱动）。
  timeline: TimelineNode[]
  // outbox
  outbox: OutboxItem[]
  // 发问置顶指令：用户经真实发送链路 append 一条 user 消息时记录其 id，
  // 由 ChatArea 在「消息已渲染且历史加载完成」后消费（执行置顶并清除）。
  // 置于 store 而非组件内 diff 探测的原因：landing 首次发问时 appendMessage
  // 必然落在 history fetch（isLoadingHistory）窗口内，任何「比较前后消息
  // 列表」的组件内启发式都无法区分「刚发送」与「历史回填」——但发送动作
  // 本身知道答案。历史回填走 setMessages（整体替换），不会产生该指令。
  pinRequest: { messageId: string } | null
  // 历史消息加载状态（per-conv）。此前该状态是 ChatPage 本地 useState
  // （loadingHistoryFor），与 messages（store）分离成两个真相源——组件只能
  // 猜测历史是否稳定，是置顶被吞 bug 的温床。唯一写入方是 loadMessagesFor。
  historyStatus: 'idle' | 'loading' | 'loaded'
  // 最近一次回合定稿时刻（chat.done → finalizeStreaming 记下）。用于识别
  // 收尾竞态中迟到的 run.snapshot 旧帧：快照在 finalize 前生成、入队后在
  // done 之后被处理，若无守卫会把已定稿的会话重新拉回流式态（执行过程
  // timeline 复活到已定稿 assistant 消息下方——层次错乱）。仅前端本地时钟，
  // 不做跨端时钟比较；窗口短（见 hydrateRun 守卫），误拦由后端周期快照自愈。
  lastFinalizedAt: number | null
}

// ── 从 localStorage 恢复未读状态 ──
function loadUnread(): Set<string> {
  try {
    const raw = localStorage.getItem(UNREAD_STORAGE_KEY)
    if (raw) return new Set(JSON.parse(raw))
  } catch { /* ignore */ }
  return new Set()
}

function saveUnread(unread: Set<string>): void {
  try {
    localStorage.setItem(UNREAD_STORAGE_KEY, JSON.stringify([...unread]))
  } catch { /* ignore */ }
}

// ── LRU 淘汰（操作 convStates）──
// 正在 streaming 的 conv 永不淘汰，防止流式状态丢失
function evictLeastRecentlyAccessed(
  convStates: Record<string, ConvState>,
  protectedConvId?: string,
): Record<string, ConvState> {
  const keys = Object.keys(convStates)
  if (keys.length <= MAX_CACHED_CONVERSATIONS) return convStates

  let lruKey: string | null = null
  let lruAt = Number.POSITIVE_INFINITY
  for (const key of keys) {
    if (key === protectedConvId) continue
    if (convStates[key]?.isStreaming) continue
    const at = convStates[key]?.lastAccessAt ?? 0
    if (at < lruAt) {
      lruAt = at
      lruKey = key
    }
  }
  if (lruKey === null) return convStates

  const next = { ...convStates }
  delete next[lruKey]
  return next
}

// ── 确保 convStates[convId] 存在 ──
function ensureConv(convStates: Record<string, ConvState>, convId: string): ConvState {
  return convStates[convId] ?? {
    messages: [],
    lastAccessAt: Date.now(),
    streamingText: '',
    currentStreamingSegmentId: 0,
    phase: null,
    phaseAt: null,
    runStartedAt: null,
    runGate: null,
    clarifyRequest: null,
    clarifySession: null,
    clarifiedMsgIds: [],
    isStreaming: false,
    isQueued: false,
    queuePosition: 0,
    activeTaskRuntime: null,
    activeTaskRuntimeEventSeq: 0,
    activeTaskRuntimeIdempotencyKey: null,
    timeline: [],
    outbox: [],
    pinRequest: null,
    historyStatus: 'idle',
    lastFinalizedAt: null,
  }
}


// ═══════════════════════════════════════════════════════════════
// Store 类型定义
// ═══════════════════════════════════════════════════════════════

interface ChatState {
  convStates: Record<string, ConvState>
  unreadConvIds: Set<string>
}

interface ChatActions {
  // ── 消息操作（convId 第一参数）──
  setMessages: (convId: string, messages: Message[]) => void
  setHistoryStatus: (convId: string, status: ConvState['historyStatus']) => void
  appendMessage: (convId: string, message: Message) => void
  // 按 id 归一：同 id 已存在则原地替换，否则追加。用于让权威消息（chat.done
  // 的最终正文）取代本地合成的占位消息，而不是并列成第二条重复回答。
  upsertMessage: (convId: string, message: Message) => void
  clearPinRequest: (convId: string) => void
  setMessageFeedback: (convId: string, messageId: string, vote: 'up' | 'down' | null) => void
  mergeMessageMetadata: (convId: string, messageId: string, patch: Partial<NonNullable<Message['metadata']>>) => void

  // ── 流式操作（convId 第一参数）──
  initStreaming: (convId: string) => void
  // 幂等全量重建：用后端 GET /run 的 RunProjection 覆盖 streaming 运行态，
  // 刷新/重连后的唯一重放入口（取代旧 restoreRunning 的「仅重挂内存中会话」）。
  // 整体替换而非 merge；ConvState.streaming* 自此被视为纯缓存，可随时丢弃重建。
  hydrateRun: (convId: string, run: RunProjection) => void
  // chat.start 事件：无条件清掉上一轮残留的 timeline / runtime / phase，
  // 并 set isStreaming=true。早于所有 task.timeline / task.runtime / chat.delta。
  resetExecutionState: (convId: string) => void
  appendDelta: (convId: string, delta: string, segmentId?: number) => void
  setClarifyRequest: (convId: string, req: ConvState['clarifyRequest']) => void
  setClarifySession: (convId: string, session: ConvState['clarifySession']) => void
  markMsgClarified: (convId: string, msgId: string) => void
  setQueued: (convId: string, queued: boolean, position?: number) => void
  setTaskRuntime: (
    convId: string,
    projection: TaskRuntimeProjection | null,
    eventSeq: number,
    idempotencyKey: string,
  ) => void
  // 应用一帧 task.timeline（快照整体替换 / patch best-effort 合并），由纯函数 nextTimelineState 承担。
  applyTimelineFrame: (convId: string, frame: TimelineFrame) => void
  finalizeStreaming: (convId: string) => { text: string; timeline: TimelineNode[] }

  // ── outbox（convId 第一参数）──
  enqueueOutbox: (convId: string, item: OutboxItem) => number
  dequeueOutbox: (convId: string) => OutboxItem | null
  takeOutbox: (convId: string, itemId: string) => OutboxItem | null
  removeOutbox: (convId: string, itemId: string) => number
  prioritizeOutbox: (convId: string, itemId: string) => number
  peekOutbox: (convId: string) => OutboxItem[]

  // ── 生命周期 ──
  touchConversation: (convId: string) => void
  dropConversation: (convId: string) => void

  // ── 未读状态 ──
  markConvAsRead: (convId: string) => void
  markConvAsUnread: (convId: string) => void
}

type ChatStore = ChatState & ChatActions

// ═══════════════════════════════════════════════════════════════
// Store 实现
// ═══════════════════════════════════════════════════════════════

export const useChatStore = create<ChatStore>((set, get) => ({
  convStates: {},
  unreadConvIds: loadUnread(),

  // ── 消息操作 ──

  setMessages: (convId, messages) =>
    set((state) => {
      const conv = ensureConv(state.convStates, convId)
      const nextConv: ConvState = { ...conv, messages, lastAccessAt: Date.now(), historyStatus: 'loaded' }
      const nextStates = { ...state.convStates, [convId]: nextConv }
      return {
        convStates: evictLeastRecentlyAccessed(nextStates, convId),
      }
    }),

  setHistoryStatus: (convId, status) =>
    set((state) => {
      const conv = state.convStates[convId]
      if (!conv || conv.historyStatus === status) return state
      return {
        convStates: {
          ...state.convStates,
          [convId]: { ...conv, historyStatus: status },
        },
      }
    }),

  appendMessage: (convId, message) =>
    set((state) => {
      const conv = ensureConv(state.convStates, convId)
      const nextConv: ConvState = {
        ...conv,
        messages: [...conv.messages, message],
        lastAccessAt: Date.now(),
        // 用户真实发问 → 记录置顶指令（历史回填/助手落库/error 消息不产生）
        ...(message.role === 'user' ? { pinRequest: { messageId: message.id } } : {}),
      }
      const nextStates = { ...state.convStates, [convId]: nextConv }
      return {
        convStates: evictLeastRecentlyAccessed(nextStates, convId),
      }
    }),

  upsertMessage: (convId, message) =>
    set((state) => {
      const conv = ensureConv(state.convStates, convId)
      const idx = conv.messages.findIndex((m) => m.id === message.id)
      const messages =
        idx >= 0
          ? conv.messages.map((m, i) => (i === idx ? message : m))
          : [...conv.messages, message]
      const nextConv: ConvState = {
        ...conv,
        messages,
        lastAccessAt: Date.now(),
        // 与 appendMessage 同口径：仅真实用户发问才记置顶指令。
        ...(message.role === 'user' ? { pinRequest: { messageId: message.id } } : {}),
      }
      const nextStates = { ...state.convStates, [convId]: nextConv }
      return {
        convStates: evictLeastRecentlyAccessed(nextStates, convId),
      }
    }),

  clearPinRequest: (convId) =>
    set((state) => {
      const conv = state.convStates[convId]
      if (!conv || !conv.pinRequest) return state
      return {
        convStates: {
          ...state.convStates,
          [convId]: { ...conv, pinRequest: null },
        },
      }
    }),

  setMessageFeedback: (convId, messageId, vote) =>
    set((state) => {
      const conv = state.convStates[convId]
      if (!conv) return state
      const nextMessages = conv.messages.map((m) => {
        if (m.id !== messageId) return m
        return { ...m, metadata: { ...(m.metadata ?? {}), feedback: vote } }
      })
      return {
        convStates: {
          ...state.convStates,
          [convId]: { ...conv, messages: nextMessages },
        },
      }
    }),

  mergeMessageMetadata: (convId, messageId, patch) =>
    set((state) => {
      const conv = state.convStates[convId]
      if (!conv) return state
      const nextMessages = conv.messages.map((m) => {
        if (m.id !== messageId) return m
        return { ...m, metadata: { ...(m.metadata ?? {}), ...patch } }
      })
      return {
        convStates: {
          ...state.convStates,
          [convId]: { ...conv, messages: nextMessages },
        },
      }
    }),

  // ── outbox ──

  enqueueOutbox: (convId, item) => {
    set((state) => {
      const conv = ensureConv(state.convStates, convId)
      const nextConv: ConvState = { ...conv, outbox: [...conv.outbox, item] }
      return { convStates: { ...state.convStates, [convId]: nextConv } }
    })
    const next = get().convStates[convId]?.outbox ?? []
    return next.length
  },

  dequeueOutbox: (convId) => {
    const conv = get().convStates[convId]
    if (!conv || conv.outbox.length === 0) return null
    const [head, ...rest] = conv.outbox
    set((state) => {
      const c = state.convStates[convId]
      if (!c) return state
      return { convStates: { ...state.convStates, [convId]: { ...c, outbox: rest } } }
    })
    return head ?? null
  },

  takeOutbox: (convId, itemId) => {
    const conv = get().convStates[convId]
    if (!conv || conv.outbox.length === 0) return null
    const target = conv.outbox.find((item) => item.id === itemId)
    if (!target) return null
    set((state) => {
      const c = state.convStates[convId]
      if (!c) return state
      return {
        convStates: {
          ...state.convStates,
          [convId]: { ...c, outbox: c.outbox.filter((item) => item.id !== itemId) },
        },
      }
    })
    return target
  },

  removeOutbox: (convId, itemId) => {
    set((state) => {
      const conv = state.convStates[convId]
      if (!conv) return state
      return {
        convStates: {
          ...state.convStates,
          [convId]: { ...conv, outbox: conv.outbox.filter((item) => item.id !== itemId) },
        },
      }
    })
    return get().convStates[convId]?.outbox.length ?? 0
  },

  prioritizeOutbox: (convId, itemId) => {
    set((state) => {
      const conv = state.convStates[convId]
      if (!conv) return state
      const index = conv.outbox.findIndex((item) => item.id === itemId)
      if (index <= 0) return state
      const target = conv.outbox[index]
      const nextOutbox = [target, ...conv.outbox.slice(0, index), ...conv.outbox.slice(index + 1)]
      return {
        convStates: {
          ...state.convStates,
          [convId]: { ...conv, outbox: nextOutbox },
        },
      }
    })
    return get().convStates[convId]?.outbox.length ?? 0
  },

  peekOutbox: (convId) => get().convStates[convId]?.outbox ?? [],

  // ── 生命周期 ──

  touchConversation: (convId) =>
    set((state) => {
      const conv = state.convStates[convId]
      if (!conv) return state
      return {
        convStates: {
          ...state.convStates,
          [convId]: { ...conv, lastAccessAt: Date.now() },
        },
      }
    }),

  dropConversation: (convId) =>
    set((state) => {
      const convStates = { ...state.convStates }
      delete convStates[convId]
      const unreadConvIds = new Set(state.unreadConvIds)
      unreadConvIds.delete(convId)
      saveUnread(unreadConvIds)
      return { convStates, unreadConvIds }
    }),

  // ── 流式操作 ──

  initStreaming: (convId) =>
    set((state) => {
      const conv = ensureConv(state.convStates, convId)
      // 已经处于 streaming 状态，不要再重置（防止多 delta 场景下反复清零）
      if (conv.isStreaming) return state
      // 只重置文本流式状态，保留 activeTaskRuntime 和 timeline。
      // 后端在 LLM 输出文本前会先发送 task.runtime 和 task.timeline
      // （推理、工具调用），如果在 chat.delta 时清空它们会导致执行过程丢失。
      // 这些字段在 finalizeStreaming（chat.done）时统一清理。
      // runGate 切换：进入 executing/generating 期 → HITL gate 必然已解除
      // （AskUserQuestion 续跑后 chat.start 不重发，续跑事件流的第一个 delta
      // 就应该触发 initStreaming，此时 runGate 仍可能卡在 await_clarification——
      // 所以必须在这里显式清）。
      return {
        convStates: {
          ...state.convStates,
          [convId]: {
            ...conv,
            streamingText: '',
            currentStreamingSegmentId: 0,
            phase: null,
            phaseAt: null,
            // 本轮计时纪元：只在进入 streaming 的那一次记下（上面的 isStreaming 守卫
            // 保证多 delta 场景不重置），供 usePhaseClock 从任务发起时刻起计数。
            runStartedAt: Date.now(),
            runGate: null,
            clarifyRequest: null,
            clarifySession: null,
            isStreaming: true,
            isQueued: false,
            queuePosition: 0,
          },
        },
      }
    }),

  // 幂等全量重建（P2）：用后端 RunProjection 整体替换 streaming 运行态。
  // 与 initStreaming 的关键差异——initStreaming 保留既有 timeline/runtime，
  // 而 hydrateRun 假定 ConvState 是「可随时丢弃的纯缓存」：把后端权威快照
  // （streaming_text / timeline_nodes / task_runtime / phase / phase_at）
  // 全量覆盖，绝不 merge，避免与内存残留互相污染。timeline 自愈由后端高频
  // 快照帧（task.runtime 携带 timeline_snapshot / phase 窗口的 run.snapshot）驱动。
  hydrateRun: (convId, run) =>
    set((state) => {
      const base = ensureConv(state.convStates, convId)
      // 防复活守卫：run.snapshot 帧在 finalize 前生成，可能在 chat.done 之后
      // 才被处理（WS 队列/在途）。若无此守卫，迟到的旧快照会把已定稿的会话
      // 重新拉回流式态——执行过程 timeline 作为独立流式块复活到已定稿
      // assistant 消息下方（执行过程与文字层次错乱的直接来源）。仅拦「定稿
      // 后极短窗口内」的帧；若 run 仍在活跃期（done 属误判/未送达），后端
      // 周期快照下一帧即自愈。
      if (
        !base.isStreaming &&
        base.lastFinalizedAt !== null &&
        Date.now() - base.lastFinalizedAt < 1500
      ) {
        return {}
      }
      const phase: HeartbeatPayload | null = run.phase
        ? { state: run.phase as HeartbeatPayload['state'], elapsedS: 0 }
        : null
      return {
        convStates: {
          ...state.convStates,
          [convId]: {
            ...base,
            isStreaming: true,
            streamingText: run.streaming_text ?? '',
            timeline: (run.timeline_nodes ?? []) as TimelineNode[],
            // 后端无有效 runtime 投影时返回空对象 {}，此处归一为 null 以信守
            // `TaskRuntimeProjection | null` 契约（消费方虽多为可选链，不因 {} 崩溃）。
            activeTaskRuntime:
              run.task_runtime && Object.keys(run.task_runtime).length > 0
                ? (run.task_runtime as TaskRuntimeProjection)
                : null,
            activeTaskRuntimeEventSeq: 0,
            activeTaskRuntimeIdempotencyKey: null,
            phase,
            phaseAt: run.phase_at ?? null,
            // 计时纪元：优先沿用本 conv 已有的 run-start（initStreaming 记下的真值）；
            // 仅在缺失时（刷新/重连后的全新 convState）才取后端 phase 起始 epoch 或当下。
            // 不能无条件取 phase_at——否则 phase 窗口周期下发的 run.snapshot 会把执行
            // 秒表每次都重置到 phase 起点（握手回归）。
            runStartedAt: base.runStartedAt ?? (run.phase_at ?? Date.now()),
            // 澄清快照重建：后端 pending_clarification 非空说明 run 正因
            // AskUserQuestion 暂停等待作答（等待无超时，仅用户提交恢复）。
            // 刷新/重连/切菜单回来后必须凭 approvalId/toolCallId 重建提交凭据，
            // 否则提问卡片消失、run 永久卡死在等待；已作答/作废时后端置 null。
            // runGate 同步重建：clarifyRequest 非空 → runGate='awaiting_clarification'。
            // clarifySession：同 approvalId 时保留用户半完成的选择态（run.snapshot 每 15s
            // 到达一次，不能每次都清）；新请求到达时（approvalId 变了）才重置。
            clarifyRequest: run.clarification
              ? {
                  approvalId: run.clarification.approval_id,
                  toolCallId: run.clarification.tool_call_id,
                  questions: Array.isArray(run.clarification.questions)
                    ? run.clarification.questions
                    : [],
                }
              : null,
            runGate: run.clarification ? 'awaiting_clarification' : null,
            clarifySession: run.clarification
              && base.clarifyRequest?.approvalId === run.clarification.approval_id
                ? base.clarifySession ?? { selectedAnswers: {}, currentQuestionIndex: 0 }
                : null,
            isQueued: false,
            queuePosition: 0,
            lastAccessAt: Date.now(),
          },
        },
      }
    }),

  // chat.start：无条件清掉上一轮残留的执行过程状态（timeline / runtime / phase），
  // 并 set isStreaming=true 记下游式纪元。后端保证 chat.start 在所有 task.timeline /
  // task.runtime / chat.delta 之前发出。
  //
  // 与 initStreaming 的关系：
  //   - chat.start（本方法）只清执行过程字段，不清 streamingText——因为新任务还没开始
  //     输出正文；清了也无妨，但保留历史文本对 UI 无影响。
  //   - initStreaming（chat.delta 触发）只在 isStreaming=false 时重置，保留 timeline
  //     等字段——怕清掉 chat.start 之后、chat.delta 之前已到达的 task.timeline 帧。
  //     chat.start 先于 task.timeline 到达，保证旧残留已被彻底清掉。
  resetExecutionState: (convId) =>
    set((state) => {
      const conv = ensureConv(state.convStates, convId)
      return {
        convStates: {
          ...state.convStates,
          [convId]: {
            ...conv,
            // 清执行过程残留
            timeline: [],
            activeTaskRuntime: null,
            activeTaskRuntimeEventSeq: 0,
            activeTaskRuntimeIdempotencyKey: null,
            phase: null,
            phaseAt: null,
            // 重置计时纪元
            runStartedAt: Date.now(),
            // chat.start = 新 run 启动 → HITL gate 必然关闭
            runGate: null,
            clarifyRequest: null,
            clarifySession: null,
            // 进入 streaming（chat.start 意味着新任务已启动）
            isStreaming: true,
            isQueued: false,
            queuePosition: 0,
            // 新回合启动：解除 finalize 防复活窗口，放行本回合的快照帧
            lastFinalizedAt: null,
          },
        },
      }
    }),

  appendDelta: (convId, delta, segmentId) =>
    set((state) => {
      const conv = state.convStates[convId]
      if (!conv) return state
      // segmentId 变了 → 进入新迭代轮次：上一轮正文已累积进 ExecutionTimeline，
      // 正文区清空让当前轮次吐字重新开始。这比 IterationBoundary 提前清空更准确 —
      // 后者会在新轮次 TextChunk 到达前制造空白窗口。
      const segChanged = segmentId !== undefined && segmentId !== conv.currentStreamingSegmentId
      return {
        convStates: {
          ...state.convStates,
          [convId]: {
            ...conv,
            streamingText: segChanged ? delta : conv.streamingText + delta,
            currentStreamingSegmentId: segChanged ? segmentId! : conv.currentStreamingSegmentId,
            isStreaming: true,
          },
        },
      }
    }),

  setClarifyRequest: (convId, req) =>
    set((state) => {
      const conv = state.convStates[convId]
      if (!conv) return state
      // runGate 是单一真相源：
      //   - 写入 clarifyRequest → runGate='awaiting_clarification'
      //   - 清空 clarifyRequest  → runGate=null（取代旧的 resetPhase 补丁）
      // clarifySession 同生命周期：新请求到达时同步初始化为空，
      // 用户选择态在 useClarifyFlow 内更新、runGate 切走时自动清。
      return {
        convStates: {
          ...state.convStates,
          [convId]: {
            ...conv,
            clarifyRequest: req,
            runGate: req ? 'awaiting_clarification' : null,
            clarifySession: req
              ? { selectedAnswers: {}, currentQuestionIndex: 0 }
              : null,
            // phase 保留——它承载 executing/generating 进度语义，不是 HITL gate。
          },
        },
      }
    }),

  // clarifySession: 用户在 ClarifyCard 内的选择态更新。与 clarifyRequest 独立
  // 写入（不需要清 runGate），但 clarifyRequest 变化时 setClarifyRequest 会重置它。
  setClarifySession: (
    convId: string,
    session: ConvState['clarifySession'],
  ) =>
    set((state) => {
      const conv = state.convStates[convId]
      if (!conv) return state
      return {
        convStates: {
          ...state.convStates,
          [convId]: { ...conv, clarifySession: session },
        },
      }
    }),

  markMsgClarified: (convId, msgId) =>
    set((state) => {
      const conv = state.convStates[convId]
      if (!conv || !msgId) return state
      if (conv.clarifiedMsgIds.includes(msgId)) return state
      return {
        convStates: {
          ...state.convStates,
          [convId]: { ...conv, clarifiedMsgIds: [...conv.clarifiedMsgIds, msgId] },
        },
      }
    }),

  setQueued: (convId, queued, position = 0) =>
    set((state) => {
      const conv = state.convStates[convId]
      if (!conv) return state
      return {
        convStates: {
          ...state.convStates,
          [convId]: {
            ...conv,
            isQueued: queued,
            queuePosition: position,
          },
        },
      }
    }),

  setTaskRuntime: (convId, projection, eventSeq, idempotencyKey) =>
    set((state) => {
      const conv = ensureConv(state.convStates, convId)
      if (eventSeq < conv.activeTaskRuntimeEventSeq) return state
      if (
        eventSeq === conv.activeTaskRuntimeEventSeq &&
        conv.activeTaskRuntimeIdempotencyKey === idempotencyKey
      ) {
        return state
      }
      return {
        convStates: {
          ...state.convStates,
          [convId]: {
            ...conv,
            activeTaskRuntime: projection,
            activeTaskRuntimeEventSeq: eventSeq,
            activeTaskRuntimeIdempotencyKey: idempotencyKey,
            lastAccessAt: Date.now(),
          },
        },
      }
    }),

  // 应用一帧 task.timeline：快照整体替换 / patch best-effort 合并，由纯函数 nextTimelineState 承担。
  applyTimelineFrame: (convId, frame: TimelineFrame) =>
    set((state) => {
      const conv = ensureConv(state.convStates, convId)
      const next = nextTimelineState({ nodes: conv.timeline }, frame)
      // 从 patch 流中抽取 set_phase 写入 conv.phase（单真相源）；
      // phaseAt 记录 patch 到达时刻，供 usePhaseClock 推进秒表。
      // IterationBoundary patch 不再触发 streamingText 清空 — 清空时机已迁移到
      // chat.delta 的 segmentId 切换（TextChunk 到达时），避免提前清空造成的空白窗口。
      let nextPhase = conv.phase
      let nextPhaseAt = conv.phaseAt
      if (frame.patch) {
        for (const p of frame.patch) {
          if (p.op === 'set_phase') {
            nextPhase = p.phase
            nextPhaseAt = Date.now()
          }
        }
      }
      return {
        convStates: {
          ...state.convStates,
          [convId]: {
            ...conv,
            timeline: next.nodes,
            phase: nextPhase,
            phaseAt: nextPhaseAt,
            lastAccessAt: Date.now(),
          },
        },
      }
    }),

  finalizeStreaming: (convId) => {
    const state = get()
    const conv = state.convStates[convId]
    if (!conv) return { text: '', timeline: [] }

    const result = {
      text: conv.streamingText,
      timeline: [...conv.timeline],
    }

    // 清空流式状态，保留消息/outbox/lastAccessAt
    set((s) => {
      const c = s.convStates[convId]
      if (!c) return s
      return {
        convStates: {
          ...s.convStates,
          [convId]: {
            ...c,
            streamingText: '',
            currentStreamingSegmentId: 0,
            phase: null,
            phaseAt: null,
            runStartedAt: null,
            runGate: null,
            clarifyRequest: null,
            clarifySession: null,
            isStreaming: false,
            isQueued: false,
            queuePosition: 0,
            activeTaskRuntime: null,
            activeTaskRuntimeEventSeq: 0,
            activeTaskRuntimeIdempotencyKey: null,
            timeline: [],
            lastFinalizedAt: Date.now(),
          },
        },
      }
    })

    return result
  },

  // ── 未读状态 ──

  markConvAsRead: (convId) =>
    set((state) => {
      if (!state.unreadConvIds.has(convId)) return state
      const unreadConvIds = new Set(state.unreadConvIds)
      unreadConvIds.delete(convId)
      saveUnread(unreadConvIds)
      return { unreadConvIds }
    }),

  markConvAsUnread: (convId) =>
    set((state) => {
      if (state.unreadConvIds.has(convId)) return state
      const unreadConvIds = new Set(state.unreadConvIds)
      unreadConvIds.add(convId)
      saveUnread(unreadConvIds)
      return { unreadConvIds }
    }),
}))
