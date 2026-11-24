import { create } from 'zustand'

import { nextTimelineState } from '../lib/chat/timelineSync'
import {
  fetchMessages,
  fetchRunningRun,
  getSession,
  listSessions,
  listSessionsAcross,
  setMessageFeedback,
} from '../services/sessions'
import type { SessionMeta } from '../services/sessions'
import { socket } from '../services/socket'
import { toast } from '../utils/toast'
import { useAppStore } from './useAppStore'
import type {
  AttachmentBlock,
  ContentBlock,
  Message,
  MessageMetadata,
  QuestionItem,
  RunProjection,
  TimelineNode,
  WSServerMessage,
} from '../types'

const MESSAGE_PAGE_SIZE = 100
/** chat.done 之后 GET /run 可能仍残留约 2s；此窗口内不再接受运行态快照 */
const FINALIZE_GUARD_MS = 2500
/** 流式会话静默多久后主动对账（长任务等待工具结果时可能长时间不吐帧） */
const STREAM_IDLE_SYNC_MS = 30_000
const STREAM_WATCHDOG_INTERVAL_MS = 15_000

/** 已定稿后极短窗口内不再接受运行态快照，否则会话会被迟到的旧快照拉回流式态 */
const finalizedRecently = (conv: ConvState) =>
  !conv.isStreaming &&
  conv.lastFinalizedAt !== null &&
  Date.now() - conv.lastFinalizedAt < FINALIZE_GUARD_MS

/** 「全部」视图需要具体工作区列表（后端 /api/sessions 不支持 all）。 */
async function allWorkspaceIds(): Promise<string[]> {
  if (!useAppStore.getState().workspaces.length) {
    await useAppStore.getState().loadWorkspaces(true).catch(() => undefined)
  }
  return useAppStore.getState().workspaces.map((item) => item.id)
}

export interface ClarifyRequest {
  approvalId: string
  toolCallId: string
  questions: QuestionItem[]
}

export interface ConvState {
  workspaceId: string
  messages: Message[]
  /** 流式正文（多轮拼接的兜底文本） */
  streamingText: string
  /**
   * 当前 streamingText 归属的迭代段。chat.delta 的 segmentId 变化即代表
   * 进入新迭代轮次：先清空再追加，避免上一轮草稿与本轮正文粘连。
   * 与 PC 端 chat.currentStreamingSegmentId 同口径。
   */
  currentStreamingSegmentId: number
  liveTimeline: TimelineNode[]
  liveOutputFiles: string[]
  /** 运行中收到的参考来源（event:reference 早于定稿时落到这里） */
  liveReferenceSources: string[]
  clarify: ClarifyRequest | null
  isStreaming: boolean
  queuedPosition: number | null
  hydrated: boolean
  hydration: 'idle' | 'loading' | 'loaded' | 'error'
  /** 已加载历史消息的起始 offset（0 表示已到最早） */
  loadedOffset: number
  hasMore: boolean
  loadingOlder: boolean
  /** 最近一次 chat.done 的时间戳，用于拦截迟到快照把已定稿会话拉回流式态 */
  lastFinalizedAt: number | null
}

/**
 * 当前轮正文的唯一归属判据：live 气泡是否正在渲染这一轮。
 *
 * 澄清态不渲染 live 气泡（弹层接管），因此此时不能把 in_progress 消息藏掉，
 * 否则用户回答完澄清问题前会看不到已经写出的正文。
 */
export const liveOwnsCurrentTurn = (conv: Pick<ConvState, 'isStreaming' | 'clarify'>): boolean =>
  conv.isStreaming && !conv.clarify

/**
 * 渲染用消息视图：当前轮由 live 气泡负责时，权威层里这一轮的分步落库消息让位。
 *
 * 后端 /messages 把运行中这一轮标 in_progress（消息已分步落库，正文仍由实时流
 * 续写）。两份同时渲染会出现同一段文字上下各一遍——这正是「回答被分割成两部分」
 * 的形态。裁决放在这里一处，各数据入口（open/sync/snapshot/delta/loadOlder）
 * 只做原样保存，不因运行态增删消息。
 */
export const visibleMessages = (conv: ConvState): Message[] =>
  liveOwnsCurrentTurn(conv)
    ? conv.messages.filter((message) => !message.inProgress)
    : conv.messages

interface ChatStore {
  conversations: SessionMeta[]
  listWorkspaceId: string
  listLoading: boolean
  listHasMore: boolean
  listOffset: number
  activeId: string
  convStates: Record<string, ConvState>

  loadConversations: (workspaceId: string, reset?: boolean) => Promise<void>
  /** 搜索前把会话列表翻到底（最多 10 页），避免只过滤首屏 */
  loadAllConversations: (workspaceId: string) => Promise<void>
  setActive: (sessionId: string) => void
  /** 为刚创建的会话预置空状态，避免 openConversation 覆盖乐观插入的首条消息 */
  primeConversation: (sessionId: string, workspaceId: string) => void
  openConversation: (sessionId: string) => Promise<void>
  syncConversation: (sessionId: string) => Promise<void>
  loadOlder: (sessionId: string) => Promise<void>
  renameConversation: (sessionId: string, title: string) => void
  removeConversation: (sessionId: string) => void
  /** 返回乐观插入的用户消息 id（无内容发送时返回 undefined） */
  sendMessage: (
    sessionId: string,
    content: string,
    options: { workspaceId: string; attachments?: AttachmentBlock[] },
  ) => string | undefined
  abort: (sessionId: string) => void
  answerClarify: (sessionId: string, answer: string) => void
  submitFeedback: (sessionId: string, messageId: string, vote: 'up' | 'down' | 'none') => void
}

function emptyConv(): ConvState {
  return {
    workspaceId: '',
    messages: [],
    streamingText: '',
    currentStreamingSegmentId: 0,
    liveTimeline: [],
    liveOutputFiles: [],
    liveReferenceSources: [],
    clarify: null,
    isStreaming: false,
    queuedPosition: null,
    hydrated: false,
    hydration: 'idle',
    loadedOffset: 0,
    hasMore: false,
    loadingOlder: false,
    lastFinalizedAt: null,
  }
}

function toOptions(raw: unknown): QuestionItem['options'] {
  if (!Array.isArray(raw)) return []
  const options: QuestionItem['options'] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const option = item as Record<string, unknown>
    const label = typeof option.label === 'string' ? option.label.trim() : ''
    if (!label) continue
    options.push({
      label,
      description: typeof option.description === 'string' ? option.description : '',
    })
  }
  return options
}

/** 后端问题契约是 snake_case（multi_select），前端统一归一成 QuestionItem 再消费。 */
function toQuestions(raw: unknown): QuestionItem[] {
  if (!Array.isArray(raw)) return []
  const questions: QuestionItem[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const question = item as Record<string, unknown>
    const text = typeof question.question === 'string' ? question.question.trim() : ''
    if (!text) continue
    questions.push({
      question: text,
      header: typeof question.header === 'string' ? question.header : '',
      multiSelect: question.multi_select === true || question.multiSelect === true,
      options: toOptions(question.options),
    })
  }
  return questions
}

function runToPatch(run: RunProjection): Partial<ConvState> {
  return {
    streamingText: run.streaming_text || '',
    liveTimeline: run.timeline_nodes ?? [],
    clarify: run.clarification
      ? {
          approvalId: run.clarification.approval_id,
          toolCallId: run.clarification.tool_call_id,
          questions: toQuestions(run.clarification.questions),
        }
      : null,
    isStreaming: true,
  }
}

export const useChatStore = create<ChatStore>((set, get) => {
  const patchConv = (sessionId: string, patch: Partial<ConvState>) => {
    const current = get().convStates[sessionId] ?? emptyConv()
    set({ convStates: { ...get().convStates, [sessionId]: { ...current, ...patch } } })
  }

  const updateConv = (sessionId: string, updater: (conv: ConvState) => Partial<ConvState>) => {
    const current = get().convStates[sessionId] ?? emptyConv()
    set({ convStates: { ...get().convStates, [sessionId]: { ...current, ...updater(current) } } })
  }

  /** 会话列表请求序号：快速切换工作区时只接受最后一次请求的结果 */
  let listSeq = 0
  /** 历史基线尚未建立时到达的 done：等 openConversation 完成后补一次对账 */
  const needsReconcile = new Set<string>()
  /** done 时被中止的 assistant 消息 id —— 投影落库后补上可见标记 */
  const abortedMessageIds = new Set<string>()
  /**
   * 定稿前的元数据帧（参考来源/交付物）按 message id 暂存：done 之后不再本地
   * 建消息，这些帧可能早于投影写入，必须等消息存在再合并。与 PC 端
   * pendingMetadataRef 同口径。
   */
  const pendingMetadata = new Map<string, MessageMetadata>()
  /**
   * 各会话最后收到帧的时刻。放在 store 外的 Map 里：每帧都写 store 会触发
   * 全量重渲染，而它只被 15s 周期的看门狗读取。与 PC 端 lastFrameAtRef 同口径。
   */
  const lastFrameAt = new Map<string, number>()

  const patchMessage = (sessionId: string, messageId: string, patch: MessageMetadata) => {
    updateConv(sessionId, (c) => ({
      messages: c.messages.map((m) =>
        m.id === messageId ? { ...m, metadata: { ...m.metadata, ...patch } } : m,
      ),
    }))
  }

  const mergeOrBufferMetadata = (sessionId: string, messageId: string, patch: MessageMetadata) => {
    if (!messageId) return
    const existing = (get().convStates[sessionId]?.messages ?? []).find((m) => m.id === messageId)
    if (existing) {
      patchMessage(sessionId, messageId, patch)
      return
    }
    pendingMetadata.set(messageId, { ...(pendingMetadata.get(messageId) ?? {}), ...patch })
  }

  const flushPendingMetadata = (sessionId: string, messages: Message[]) => {
    if (!pendingMetadata.size) return
    const ids = new Set(messages.map((m) => m.id))
    for (const [messageId, patch] of Array.from(pendingMetadata)) {
      if (!ids.has(messageId)) continue
      patchMessage(sessionId, messageId, patch)
      pendingMetadata.delete(messageId)
    }
  }

  const refreshConversationList = () => {
    const workspaceId = get().listWorkspaceId
    if (workspaceId) void get().loadConversations(workspaceId, true).catch(() => undefined)
  }

  /**
   * done 之后的对账：后端 /run 有约 2s 残留（见 FINALIZE_GUARD_MS），只触发一次
   * 会读到 active 而跳过定稿，要等下一个 30s 看门狗周期。这里在收尾窗口内短重试，
   * 直到投影落定（isStreaming 归位）或次数用尽。
   */
  const settleAfterDone = async (sessionId: string) => {
    for (let attempt = 0; attempt < 4; attempt += 1) {
      const before = get().convStates[sessionId]
      // 历史基线未建立：openConversation 完成后会依据 needsReconcile 补对账，
      // 这里重试没有意义（syncConversation 会直接返回）。
      if (!before || before.hydration !== 'loaded') return
      await get()
        .syncConversation(sessionId)
        .catch(() => undefined)
      const conv = get().convStates[sessionId]
      if (!conv || !conv.isStreaming) return
      await new Promise((resolve) => setTimeout(resolve, 800))
    }
  }

  /**
   * 把服务端投影写成权威消息，并清掉本轮 live 叠加层。
   * 正文只来自 /messages 投影：它按全轮拼接并整段脱敏，是唯一对用户可见的真相。
   */
  const settleFromProjection = (sessionId: string, messages: Message[], offset: number) => {
    const prev = get().convStates[sessionId] ?? emptyConv()
    // 无 message_id 的旁路帧（参考来源/交付物）只能落 live；run 已确认结束，
    // 此时挂到本轮最后一条 assistant 上没有归属歧义，不能直接丢弃。
    const liveMeta: MessageMetadata = {
      ...(prev.liveOutputFiles.length ? { outputFiles: prev.liveOutputFiles } : {}),
      ...(prev.liveReferenceSources.length
        ? { referenceSources: prev.liveReferenceSources }
        : {}),
    }
    const lastAssistantId = [...messages].reverse().find((m) => m.role === 'assistant')?.id
    const patched = messages.map((m) => {
      if (m.role !== 'assistant') return m
      const merged: MessageMetadata = { ...m.metadata }
      if (m.id === lastAssistantId && Object.keys(liveMeta).length) {
        merged.outputFiles = Array.from(
          new Set([...(m.metadata?.outputFiles ?? []), ...(liveMeta.outputFiles ?? [])]),
        )
        if (liveMeta.referenceSources?.length) {
          merged.referenceSources = liveMeta.referenceSources
        }
      }
      if (abortedMessageIds.has(m.id)) merged.interrupted = true
      return Object.keys(merged).length ? { ...m, metadata: merged } : m
    })
    for (const id of abortedMessageIds) {
      if (patched.some((m) => m.id === id)) abortedMessageIds.delete(id)
    }
    patchConv(sessionId, {
      messages: patched,
      hydration: 'loaded',
      loadedOffset: offset,
      hasMore: offset > 0,
      streamingText: '',
      currentStreamingSegmentId: 0,
      liveTimeline: [],
      liveOutputFiles: [],
      liveReferenceSources: [],
      clarify: null,
      isStreaming: false,
      queuedPosition: null,
      lastFinalizedAt: Date.now(),
    })
    flushPendingMetadata(sessionId, patched)
    lastFrameAt.delete(sessionId)
    refreshConversationList()
  }

  const handleFrame = (frame: WSServerMessage) => {
    const sessionId =
      'conversation_id' in frame && typeof frame.conversation_id === 'string'
        ? frame.conversation_id
        : ''
    if (!sessionId) return
    // 任何带会话 id 的帧都刷新「最后活动时刻」，供流式静默对账判定。
    lastFrameAt.set(sessionId, Date.now())
    const conv = get().convStates[sessionId] ?? emptyConv()

    switch (frame.type) {
      case 'chat.start':
        patchConv(sessionId, {
          streamingText: '',
          currentStreamingSegmentId: 0,
          liveTimeline: [],
          liveOutputFiles: [],
          liveReferenceSources: [],
          clarify: null,
          queuedPosition: null,
          isStreaming: true,
          hydrated: true,
          hydration: 'loaded',
          lastFinalizedAt: null,
        })
        break

      case 'chat.delta':
        // segmentId 变化代表进入新迭代轮次：先清空再追加，避免上一轮草稿与本轮
        // 正文粘连。非流式态收到 delta（HITL 续跑时后端不重发 chat.start，或
        // chat.start 丢失）同样视作新一轮。均与 PC 端 appendDelta/initStreaming 同口径。
        updateConv(sessionId, (c) => {
          const fresh = !c.isStreaming
          const segChanged =
            fresh ||
            (frame.data.segmentId !== undefined &&
              frame.data.segmentId !== c.currentStreamingSegmentId)
          return {
            isStreaming: true,
            streamingText: segChanged ? frame.data.text : c.streamingText + frame.data.text,
            currentStreamingSegmentId:
              frame.data.segmentId !== undefined && segChanged
                ? frame.data.segmentId
                : c.currentStreamingSegmentId,
          }
        })
        break

      case 'chat.queued':
        patchConv(sessionId, { queuedPosition: frame.data.position ?? null })
        break

      case 'task.timeline': {
        // 正文清空时机已迁移到 chat.delta 的 segmentId 切换：若在这里按
        // iteration_boundary 清空，新轮次首个 TextChunk 到达前会出现空白窗口。
        const state = nextTimelineState(
          { nodes: conv.liveTimeline },
          { nodes: frame.data.nodes, patch: frame.data.patch },
        )
        patchConv(sessionId, { liveTimeline: state.nodes })
        break
      }

      case 'run.snapshot':
        // 迟到的快照不能把已定稿会话重新拉回流式态
        if (!finalizedRecently(conv)) patchConv(sessionId, runToPatch(frame.data))
        break

      case 'event:outbox': {
        const files = (frame.data.files ?? []).filter(
          (file): file is string => typeof file === 'string' && file.trim().length > 0,
        )
        if (!files.length) break
        if (frame.data.message_id) {
          // 有显式 id：消息可能尚未由投影写入，先暂存再由 settle 补挂
          mergeOrBufferMetadata(sessionId, frame.data.message_id, { outputFiles: files })
          break
        }
        // 无 id 且已收尾：宁可不显示也不挂到别的回答上（投影侧已持久化交付物）
        if (conv.isStreaming) patchConv(sessionId, { liveOutputFiles: files })
        break
      }

      // 参考来源在定稿前后都可能到达：有 id 走暂存/合并，无 id 仅在本轮运行中落 live，
      // 绝不回退到「最后一条 assistant」——那会把本轮资料挂到上一轮回答上。
      case 'event:reference': {
        const sources = (frame.data.sources ?? []).filter(
          (source) => typeof source === 'string' && source.trim().length > 0,
        )
        if (!sources.length) break
        if (frame.data.message_id) {
          mergeOrBufferMetadata(sessionId, frame.data.message_id, { referenceSources: sources })
          break
        }
        if (conv.isStreaming) patchConv(sessionId, { liveReferenceSources: sources })
        break
      }

      // 引导问题在 chat.done 之后异步下发（后端最多等 3s 生成），按 id 挂到对应回答
      case 'event:questions': {
        const questions = (frame.data.questions ?? [])
          .filter((q): q is string => typeof q === 'string' && q.trim().length > 0)
          .slice(0, 3)
        if (!questions.length || !frame.data.message_id) break
        mergeOrBufferMetadata(sessionId, frame.data.message_id, { followupQuestions: questions })
        break
      }

      case 'tool.clarify_request':
        patchConv(sessionId, {
          clarify: {
            approvalId: frame.data.approval_id,
            toolCallId: frame.data.tool_call_id,
            questions: toQuestions(frame.data.questions),
          },
        })
        break

      case 'chat.done': {
        // 与 PC 端同口径：done 只是「去服务端对账」的信号，不在本地合成正文。
        // 后端 final_message 仅用于审计与非 Web 通道——它只保留最后一轮迭代，
        // 且实时链路的按 chunk 脱敏会在分片边界留下残缺路径。用户可见正文一律
        // 取 /messages 的展示投影（全轮拼接 + 整段脱敏）。
        const finalMessage = frame.data.final_message
        const messageId =
          typeof frame.data.message_id === 'string' && frame.data.message_id
            ? frame.data.message_id
            : finalMessage && typeof finalMessage === 'object' && typeof finalMessage.id === 'string'
              ? finalMessage.id
              : ''
        if (frame.data.aborted && messageId) abortedMessageIds.add(messageId)
        needsReconcile.add(sessionId)
        // 先标记收尾状态，避免 sync 在途期间 run.snapshot 把会话重新拉回流式态；
        // 但 streamingText / liveTimeline 保留到投影返回后再清，防止收尾闪烁。
        patchConv(sessionId, { lastFinalizedAt: Date.now() })
        void settleAfterDone(sessionId)
        break
      }

      case 'error':
        updateConv(sessionId, (c) => ({
          ...emptyConv(),
          messages: c.messages.concat({
            id: `err_${Date.now()}`,
            role: 'error' as const,
            content: frame.data.message || '任务执行失败',
            timestamp: Date.now(),
          }),
          hydrated: true,
          hydration: 'loaded',
          loadedOffset: c.loadedOffset,
          hasMore: c.hasMore,
        }))
        break

      default:
        break
    }
  }

  socket.subscribeFrames(handleFrame)

  /**
   * 流式静默对账：与 PC 端看门狗同口径——只负责「怀疑」，不负责「判决」。
   * 长任务等待工具结果时可能长时间不吐帧，此时向服务端确认 run_state，
   * 是继续等待还是收尾由服务端决定；本地绝不清空正文或合成中断消息。
   */
  setInterval(() => {
    const now = Date.now()
    const states = get().convStates
    for (const [sessionId, conv] of Object.entries(states)) {
      if (!conv.isStreaming || conv.hydration !== 'loaded') continue
      // 尚未收到任何帧的会话先记一个起点，否则 chat.start 丢失时会永远不触发
      const last = lastFrameAt.get(sessionId)
      if (!last) {
        lastFrameAt.set(sessionId, now)
        continue
      }
      if (now - last > STREAM_IDLE_SYNC_MS) {
        void get().syncConversation(sessionId).catch(() => undefined)
      }
    }
  }, STREAM_WATCHDOG_INTERVAL_MS)

  return {
    conversations: [],
    listWorkspaceId: '',
    listLoading: false,
    listHasMore: false,
    listOffset: 0,
    activeId: '',
    convStates: {},

    loadConversations: async (workspaceId, reset = false) => {
      const state = get()
      if (!reset && state.listWorkspaceId === workspaceId && !state.listHasMore) return
      const offset = reset ? 0 : state.listOffset
      const requestId = ++listSeq
      set({ listLoading: true, listWorkspaceId: workspaceId })
      try {
        const page =
          workspaceId === 'all'
            ? await listSessionsAcross(await allWorkspaceIds(), 20, offset)
            : await listSessions(workspaceId, 20, offset)
        if (requestId !== listSeq) return
        // 自动翻页时后端按时间排序，新增会话会让分页边界漂移，按 id 去重避免重复行
        const prev = reset ? [] : get().conversations
        const seen = new Set(prev.map((item) => item.id))
        const items = prev.concat(page.items.filter((item) => !seen.has(item.id)))
        set({
          conversations: items,
          listHasMore: page.has_more,
          listOffset: offset + page.items.length,
        })
      } finally {
        if (requestId === listSeq) set({ listLoading: false })
      }
    },

    loadAllConversations: async (workspaceId) => {
      if (get().listWorkspaceId !== workspaceId) await get().loadConversations(workspaceId, true)
      for (let page = 0; page < 10; page += 1) {
        if (!get().listHasMore) return
        const offset = get().listOffset
        await get().loadConversations(workspaceId, false)
        if (get().listOffset === offset) return
      }
    },

    setActive: (sessionId) => {
      set({ activeId: sessionId })
    },

    primeConversation: (sessionId, workspaceId) => {
      patchConv(sessionId, {
        ...emptyConv(),
        workspaceId,
        hydration: 'loaded',
        hydrated: true,
      })
    },

    openConversation: async (sessionId) => {
      get().setActive(sessionId)
      const existing = get().convStates[sessionId]
      if (existing?.hydration === 'loading' || existing?.hydration === 'loaded') return
      patchConv(sessionId, { hydration: 'loading' })
      try {
        const detail = await getSession(sessionId)
        const count = detail.message_count ?? 0
        const offset = Math.max(0, count - MESSAGE_PAGE_SIZE)
        const [messages, run] = await Promise.all([
          fetchMessages(sessionId, MESSAGE_PAGE_SIZE, offset),
          fetchRunningRun(sessionId, detail.workspace_id),
        ])
        const latest = get().convStates[sessionId]
        patchConv(sessionId, {
          workspaceId: detail.workspace_id,
          messages,
          hydration: 'loaded',
          hydrated: true,
          loadedOffset: offset,
          hasMore: offset > 0,
          ...(run && !(latest && finalizedRecently(latest)) ? runToPatch(run) : {}),
        })
        flushPendingMetadata(sessionId, messages)
        // 基线建立前到达的 done：此时才第一次拿到投影，直接在此定稿，
        // 避免再发一次 sync 请求。
        if (!run && needsReconcile.has(sessionId)) {
          settleFromProjection(sessionId, messages, offset)
          needsReconcile.delete(sessionId)
        }
      } catch {
        patchConv(sessionId, { hydration: 'error' })
      }
    },

    /** 断线重连后补齐消息与运行态，避免错过 chat.done 一直停留在流式态 */
    syncConversation: async (sessionId) => {
      const conv = get().convStates[sessionId]
      if (!conv || conv.hydration !== 'loaded') return
      try {
        const detail = await getSession(sessionId)
        const count = detail.message_count ?? 0
        const offset = Math.max(0, count - MESSAGE_PAGE_SIZE)
        const [messages, run] = await Promise.all([
          fetchMessages(sessionId, MESSAGE_PAGE_SIZE, offset),
          fetchRunningRun(sessionId, detail.workspace_id),
        ])
        const latest = get().convStates[sessionId] ?? conv
        if (run) {
          // 服务端确认仍在运行：只把运行态写回叠加层，不动已有正文。
          // 同时刷新 lastFrameAt——这是「刚确认过状态」，不必每 15s 反复请求。
          if (!finalizedRecently(latest)) {
            patchConv(sessionId, {
              messages,
              hydration: 'loaded',
              loadedOffset: offset,
              hasMore: offset > 0,
              ...runToPatch(run),
            })
            lastFrameAt.set(sessionId, Date.now())
            flushPendingMetadata(sessionId, messages)
          }
          return
        }
        // 服务端确认收尾：定稿正文来自投影（全轮拼接 + 整段脱敏），
        // 而不是实时流里被分片切断的那份文本。
        settleFromProjection(sessionId, messages, offset)
        needsReconcile.delete(sessionId)
      } catch {
        // 同步失败保持现状，等待下一次重连/看门狗重试
      }
    },

    loadOlder: async (sessionId) => {
      const conv = get().convStates[sessionId]
      if (!conv || !conv.hasMore || conv.loadingOlder || conv.hydration === 'loading') return
      const offset = Math.max(0, conv.loadedOffset - MESSAGE_PAGE_SIZE)
      patchConv(sessionId, { loadingOlder: true })
      try {
        const older = await fetchMessages(sessionId, conv.loadedOffset - offset, offset)
        const latest = get().convStates[sessionId] ?? conv
        patchConv(sessionId, {
          messages: older.concat(latest.messages),
          loadedOffset: offset,
          hasMore: offset > 0,
          loadingOlder: false,
        })
      } catch {
        patchConv(sessionId, { loadingOlder: false })
        toast('更早的消息加载失败')
      }
    },

    renameConversation: (sessionId, title) => {
      set({
        conversations: get().conversations.map((c) => (c.id === sessionId ? { ...c, title } : c)),
      })
    },

    removeConversation: (sessionId) => {
      const convStates = { ...get().convStates }
      delete convStates[sessionId]
      set({
        conversations: get().conversations.filter((c) => c.id !== sessionId),
        convStates,
        activeId: get().activeId === sessionId ? '' : get().activeId,
      })
    },

    sendMessage: (sessionId, content, options) => {
      const text = content.trim()
      const attachments = options.attachments ?? []
      // 只发附件、不写文字时也应正常发送
      if (!text && !attachments.length) return undefined
      const conv = get().convStates[sessionId] ?? emptyConv()
      // 乐观消息保留原始标记：气泡里按 chip 渲染技能/专家，与刷新后的历史气泡一致
      const blocks: ContentBlock[] = []
      if (text) blocks.push({ type: 'text', text })
      blocks.push(...attachments)
      const userMessage: Message = {
        id: `user_${Date.now()}`,
        role: 'user',
        content: attachments.length ? blocks : text,
        timestamp: Date.now(),
      }
      const workspaceId = conv.workspaceId || options.workspaceId
      patchConv(sessionId, {
        workspaceId,
        messages: conv.messages.concat(userMessage),
        isStreaming: true,
        hydrated: true,
        hydration: 'loaded',
      })
      socket.send({
        type: 'chat.send',
        conversation_id: sessionId,
        workspace_id: workspaceId,
        content: text,
        ...(attachments.length ? { attachments } : {}),
      })
      return userMessage.id
    },

    abort: (sessionId) => {
      socket.send({ type: 'chat.abort', conversation_id: sessionId })
    },

    answerClarify: (sessionId, answer) => {
      const clarify = get().convStates[sessionId]?.clarify
      if (!clarify) return
      socket.send({
        type: 'tool.clarify',
        conversation_id: sessionId,
        approval_id: clarify.approvalId,
        tool_call_id: clarify.toolCallId,
        answer,
      })
      patchConv(sessionId, { clarify: null })
    },

    submitFeedback: (sessionId, messageId, vote) => {
      const previous =
        get().convStates[sessionId]?.messages.find((message) => message.id === messageId)
          ?.metadata?.feedback ?? null
      const apply = (value: 'up' | 'down' | null) =>
        updateConv(sessionId, (c) => ({
          messages: c.messages.map((message) => {
            if (message.id !== messageId) return message
            const metadata = { ...(message.metadata ?? {}) }
            if (value === null) delete metadata.feedback
            else metadata.feedback = value
            return { ...message, metadata }
          }),
        }))
      apply(vote === 'none' ? null : vote)
      void setMessageFeedback(sessionId, messageId, vote).catch(() => {
        apply(previous)
        toast('反馈提交失败')
      })
    },
  }
})
