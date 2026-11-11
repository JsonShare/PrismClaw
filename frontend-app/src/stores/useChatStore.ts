import { create } from 'zustand'

import { markersToReadable } from '../lib/chat/markers'
import { nextTimelineState } from '../lib/chat/timelineSync'
import {
  contentToText,
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
  Mode,
  QuestionItem,
  RunProjection,
  TimelineNode,
  WSServerMessage,
} from '../types'

const MESSAGE_PAGE_SIZE = 100
/** chat.done 之后 GET /run 可能仍残留约 2s；此窗口内不再接受运行态快照 */
const FINALIZE_GUARD_MS = 2500

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
  liveTimeline: TimelineNode[]
  liveOutputFiles: string[]
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
  sendMessage: (
    sessionId: string,
    content: string,
    options: { workspaceId: string; mode: Mode; attachments?: AttachmentBlock[] },
  ) => void
  abort: (sessionId: string) => void
  answerClarify: (sessionId: string, answer: string) => void
  submitFeedback: (sessionId: string, messageId: string, vote: 'up' | 'down' | 'none') => void
}

function emptyConv(): ConvState {
  return {
    workspaceId: '',
    messages: [],
    streamingText: '',
    liveTimeline: [],
    liveOutputFiles: [],
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

  const finishTurn = (sessionId: string, message?: Message) => {
    updateConv(sessionId, (conv) => {
      let messages = conv.messages
      if (message) {
        const index = messages.findIndex((m) => m.id === message.id)
        messages = index >= 0 ? messages.map((m, i) => (i === index ? message : m)) : messages.concat(message)
      }
      return {
        messages,
        streamingText: '',
        liveTimeline: [],
        liveOutputFiles: [],
        clarify: null,
        isStreaming: false,
        queuedPosition: null,
        lastFinalizedAt: Date.now(),
      }
    })
    const workspaceId = get().listWorkspaceId
    if (workspaceId) void get().loadConversations(workspaceId, true).catch(() => undefined)
  }

  const handleFrame = (frame: WSServerMessage) => {
    const sessionId =
      'conversation_id' in frame && typeof frame.conversation_id === 'string'
        ? frame.conversation_id
        : ''
    if (!sessionId) return
    const conv = get().convStates[sessionId] ?? emptyConv()

    switch (frame.type) {
      case 'chat.start':
        patchConv(sessionId, {
          streamingText: '',
          liveTimeline: [],
          liveOutputFiles: [],
          clarify: null,
          queuedPosition: null,
          isStreaming: true,
          hydrated: true,
          hydration: 'loaded',
          lastFinalizedAt: null,
        })
        break

      case 'chat.delta':
        updateConv(sessionId, (c) => ({
          isStreaming: true,
          streamingText: c.streamingText + frame.data.text,
        }))
        break

      case 'chat.queued':
        patchConv(sessionId, { queuedPosition: frame.data.position ?? null })
        break

      case 'task.timeline': {
        const state = nextTimelineState(
          { nodes: conv.liveTimeline },
          { nodes: frame.data.nodes, patch: frame.data.patch },
        )
        let streamingText = conv.streamingText
        for (const patch of frame.data.patch ?? []) {
          if (patch.op === 'iteration_boundary') streamingText = ''
        }
        patchConv(sessionId, { liveTimeline: state.nodes, streamingText })
        break
      }

      case 'run.snapshot':
        // 迟到的快照不能把已定稿会话重新拉回流式态
        if (!finalizedRecently(conv)) patchConv(sessionId, runToPatch(frame.data))
        break

      case 'event:outputs': {
        const files = (frame.data.files ?? []).filter(
          (file): file is string => typeof file === 'string' && file.trim().length > 0,
        )
        if (!files.length) break
        // 后端先发 chat.done、再发 event:outputs，此时本轮回答已定稿落库；
        // 迟到的那一帧必须补挂到对应消息上，否则交付物只在 live 态存在、随后被丢弃。
        const targetId =
          frame.data.message_id ||
          [...conv.messages].reverse().find((m) => m.role === 'assistant')?.id
        const target = targetId ? conv.messages.find((m) => m.id === targetId) : undefined
        if (!target) {
          // 消息尚未定稿（或本轮没有正文）：先留在 live 态，由 chat.done 兜底挂载
          patchConv(sessionId, { liveOutputFiles: files })
          break
        }
        updateConv(sessionId, (c) => ({
          liveOutputFiles: files,
          messages: c.messages.map((m) =>
            m.id === target.id
              ? {
                  ...m,
                  metadata: {
                    ...m.metadata,
                    outputFiles: Array.from(
                      new Set([...(m.metadata?.outputFiles ?? []), ...files]),
                    ),
                  },
                }
              : m,
          ),
        }))
        break
      }

      // 引导问题在 chat.done 之后异步下发（后端最多等 3s 生成），挂到对应回答上
      case 'event:questions': {
        const questions = (frame.data.questions ?? [])
          .filter((q): q is string => typeof q === 'string' && q.trim().length > 0)
          .slice(0, 3)
        if (!questions.length) break
        const targetId =
          frame.data.message_id ||
          [...conv.messages].reverse().find((m) => m.role === 'assistant')?.id
        if (!targetId) break
        updateConv(sessionId, (c) => ({
          messages: c.messages.map((m) =>
            m.id === targetId
              ? { ...m, metadata: { ...m.metadata, followupQuestions: questions } }
              : m,
          ),
        }))
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
        const finalMessage = frame.data.final_message
        const serverId =
          finalMessage && typeof finalMessage === 'object' && typeof finalMessage.id === 'string'
            ? finalMessage.id
            : typeof frame.data.message_id === 'string'
              ? frame.data.message_id
              : `asst_${Date.now()}`
        const serverText =
          typeof finalMessage === 'string'
            ? finalMessage
            : finalMessage && typeof finalMessage === 'object'
              ? contentToText(finalMessage.content)
              : ''
        const body = serverText.trim() ? serverText : conv.streamingText
        const timeline =
          conv.liveTimeline.length > 0
            ? conv.liveTimeline
            : finalMessage && typeof finalMessage === 'object' && Array.isArray(finalMessage.timeline)
              ? finalMessage.timeline
              : []
        const metadata = {
          ...(finalMessage && typeof finalMessage === 'object' ? finalMessage.metadata : {}),
          ...(conv.liveOutputFiles.length ? { outputFiles: conv.liveOutputFiles } : {}),
          // 用户主动停止：本轮回复不完整，给消息打上可见标记
          ...(frame.data.aborted ? { interrupted: true } : {}),
        }
        const message: Message | undefined =
          body.trim() || timeline.length > 0
            ? {
                id: serverId,
                role: 'assistant' as const,
                content: body,
                timestamp: Date.now(),
                timeline: timeline.length ? timeline : undefined,
                metadata: Object.keys(metadata).length ? metadata : undefined,
              }
            : undefined
        finishTurn(sessionId, message)
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
        if (run && !finalizedRecently(latest)) {
          patchConv(sessionId, {
            messages,
            hydration: 'loaded',
            loadedOffset: offset,
            hasMore: offset > 0,
            ...runToPatch(run),
          })
          return
        }
        if (!run) {
          patchConv(sessionId, {
            messages,
            hydration: 'loaded',
            loadedOffset: offset,
            hasMore: offset > 0,
            streamingText: '',
            liveTimeline: [],
            clarify: null,
            isStreaming: false,
            queuedPosition: null,
            lastFinalizedAt: Date.now(),
          })
        }
      } catch {
        // 同步失败保持现状，等待下一次重连
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
      if (!text) return
      const attachments = options.attachments ?? []
      const conv = get().convStates[sessionId] ?? emptyConv()
      const readable = markersToReadable(text)
      // 乐观消息按用户可读形态展示：正文去标记 + 附件块
      const blocks: ContentBlock[] = []
      if (readable.trim()) blocks.push({ type: 'text', text: readable })
      blocks.push(...attachments)
      const userMessage: Message = {
        id: `user_${Date.now()}`,
        role: 'user',
        content: attachments.length ? blocks : readable,
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
        mode: options.mode,
        ...(attachments.length ? { attachments } : {}),
      })
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
