import { useCallback, useEffect } from 'react'
import { useChatStore, usePermissionStore, useSessionStore } from '../../stores'
import type { OutboxItem } from '../../stores/chat'
import { apiUrl } from '../../lib/system-adapter'
import { splitAttachmentContent } from '../../lib/chat/attachments'
import { markersToReadable } from '../../lib/chat/inputMarkers'
import type { AttachmentBlock, ContentBlock, Message, WSClientMessage, WSState } from '../../types'

/**
 * 咨询页会话控制器：发送/队列/历史/确认/澄清/重试/中止的唯一入口。
 *
 * 架构原则：业务事件一等公民。此前这些逻辑是 ChatPage 里四份抄写的
 * 「appendMessage + initStreaming + WS send」三连（handleSend / executeOutboxItem /
 * handleConfirmationDecision / handleRetryMessage），加两份抄写的历史加载
 * （handleSelectSession / auto-load effect）——任何行为调整都要多点同步修改，
 * 是补丁丛生的结构性根源。现统一收口：
 *   - sendMessage      用户发问（landing 建会话 / 会话内 / 流式排队 全分支）
 *   - submitConfirmation 风险确认（确认继续/取消任务）
 *   - retryMessage     失败重试
 *   - runQueued/deleteQueued  队列管理
 *   - ensureHistory    历史加载的唯一判定逻辑（cached/status 幂等）
 *   - clarifySubmit / abort
 * 组件（ChatArea 等）只渲染 + 调用本控制器，不再经 ChatPage/ChatSection 层层转发。
 */

export interface ConversationController {
  wsState: WSState
  sendMessage: (text: string, attachments?: AttachmentBlock[]) => Promise<void>
  retryMessage: (errorMessageId: string) => void
  submitConfirmation: (decision: 'approve' | 'deny') => void
  clarifySubmit: (approvalId: string, toolCallId: string, answer: string) => void
  abort: () => void
  runQueued: (itemId: string) => { message: string; tone: 'success' | 'error' } | undefined
  deleteQueued: (itemId: string) => { message: string; tone: 'success' } | undefined
  selectConversation: (convId: string) => Promise<void>
  ensureHistory: (convId: string) => Promise<void>
  optimizePrompt: (text: string) => Promise<string>
}

interface ControllerDeps {
  token: string | null
  backendWorkspaceId: string
  wsState: WSState
  send: (msg: WSClientMessage) => void
  loadMessagesFor: (convId: string) => Promise<void>
}

export function useConversationController({
  token,
  backendWorkspaceId,
  wsState,
  send,
  loadMessagesFor,
}: ControllerDeps): ConversationController {
  const activeConvId = useSessionStore((s) => s.activeConvId)
  const isStreaming = useChatStore((s) =>
    activeConvId ? s.convStates[activeConvId]?.isStreaming ?? false : false,
  )

  // ── 唯一 WS 发送原语 ──
  const wsSendChat = useCallback(
    (convId: string, text: string, attachments: AttachmentBlock[], extra: Record<string, unknown> = {}) => {
      const mode = usePermissionStore.getState().mode
      send({
        type: 'chat.send',
        conversation_id: convId,
        workspace_id: backendWorkspaceId,
        content: text,
        mode,
        trajectory: 'full',
        ...(attachments.length > 0 ? { attachments } : {}),
        ...extra,
      } as WSClientMessage)
    },
    [backendWorkspaceId, send],
  )

  // ── 落一条新用户消息并发起执行（outbox 执行、确认决策、发问共用） ──
  const dispatchUserMessage = useCallback(
    (convId: string, msgId: string, text: string, attachments: AttachmentBlock[], extra: Record<string, unknown> = {}) => {
      // 正文里的内联卡片标记是传输契约，也是气泡渲染卡片的唯一真相源：
      // 本地消息原样保留标记串，气泡按标记分段渲染；复制/标题等派生展示再
      // 转成可读文本（markersToReadable）。
      const userContent: string | ContentBlock[] =
        attachments.length > 0
          ? [...attachments, ...(text.length > 0 ? [{ type: 'text' as const, text }] : [])]
          : text
      useChatStore.getState().appendMessage(convId, {
        id: msgId,
        role: 'user',
        content: userContent,
        timestamp: Date.now(),
      })
      useChatStore.getState().initStreaming(convId)
      wsSendChat(convId, text, attachments, extra)
    },
    [wsSendChat],
  )

  // ── 历史加载的唯一判定逻辑（幂等：已加载/加载中/有本地消息均不重取） ──
  const ensureHistory = useCallback(
    async (convId: string) => {
      const conv = useChatStore.getState().convStates[convId]
      if (conv?.historyStatus === 'loading' || conv?.historyStatus === 'loaded') return
      if (conv && conv.messages.length > 0) {
        useChatStore.getState().setHistoryStatus(convId, 'loaded')
        return
      }
      await loadMessagesFor(convId)
    },
    [loadMessagesFor],
  )

  const selectConversation = useCallback(
    async (convId: string) => {
      useSessionStore.getState().setActiveConvId(convId)
      useChatStore.getState().markConvAsRead(convId)
      await ensureHistory(convId)
    },
    [ensureHistory],
  )

  // ── 用户发问（统一 landing 建会话 / 会话内 / 流式排队 全分支） ──
  const sendMessage = useCallback(
    async (text: string, attachments: AttachmentBlock[] = []) => {
      // 标题必须用可读文本：正文里的卡片标记是高熵串，不能进标题
      const title = markersToReadable(text).trim()
      let convId = useSessionStore.getState().activeConvId
      if (!convId) {
        try {
          const res = await fetch(apiUrl('/api/sessions'), {
            method: 'POST',
            headers: {
              Authorization: `Bearer ${token}`,
              'Content-Type': 'application/json',
            },
            // 后端标题上限 200 字符（routes.py create_session 400）：超长任务文本
            // 必须截断，否则 POST 400 落入本地 fallback id，服务端永远查无此会话。
            body: JSON.stringify({ workspace_id: backendWorkspaceId, title: title.slice(0, 200) }),
          })
          if (res.ok) {
            const data = await res.json()
            convId = data.session_id
          }
        } catch {}
        if (!convId) {
          convId = `conv_${Date.now()}`
        }
        useSessionStore.getState().setActiveConvId(convId)
        // 新会话立即出现在侧边栏，必须带上 workspaceId，否则分组函数
        // (groupedHistoryConversations) 读 c.workspaceId || 'default' 会
        // 把刚创建的会话错放到"默认"分组下，直到下次 list_sessions 刷新才纠正。
        useSessionStore.getState().prependConversation({
          id: convId,
          title: title || '新对话',
          updatedAt: Date.now(),
          active: true,
          workspaceId: backendWorkspaceId,
        })
        // 新会话历史为空是定义使然，直接标记已加载。否则 ensureHistory 会立即
        // 拉取历史：若 POST 失败走了本地 fallback id，GET 404 会触发
        // clearStaleActiveConv 把刚激活的会话置空——表现为「发送后闪一下退回
        // landing、无气泡」，而消息实际已进入后台会话执行。
        useChatStore.getState().setHistoryStatus(convId, 'loaded')
      }

      const maybeTitle = title
      if (maybeTitle) {
        const hasUserMessage =
          (useChatStore.getState().convStates[convId]?.messages ?? []).some((m) => m.role === 'user')
        // Always persist the first user message as the session title.
        // The optimistic local title may already be the message text, but
        // the backend title must be PATCHed explicitly or it will fall
        // back to "新对话" after the next session list reload.
        if (!hasUserMessage) {
          useSessionStore
            .getState()
            .updateConversations((list) => list.map((c) => (c.id === convId ? { ...c, title: maybeTitle } : c)))
          try {
            const res = await fetch(apiUrl(`/api/sessions/${encodeURIComponent(convId)}`), {
              method: 'PATCH',
              headers: {
                Authorization: `Bearer ${token}`,
                'Content-Type': 'application/json',
              },
              body: JSON.stringify({ title: maybeTitle.slice(0, 200) }),
            })
            if (!res.ok) throw new Error(`HTTP ${res.status}`)
          } catch {}
        }
      }

      // 流式中 → 排队（outbox），由下方 drain effect 在上一条完成后自动执行
      if (useChatStore.getState().convStates[convId]?.isStreaming) {
        const queuedId = `usr_${Date.now()}`
        const pos = useChatStore.getState().enqueueOutbox(convId, {
          id: queuedId,
          text,
          attachments,
        })
        useChatStore.getState().setQueued(convId, true, pos)
        return
      }

      // WS 未就绪：落本地消息 + 错误提示，不发执行
      if (wsState !== 'ready') {
        useChatStore.getState().appendMessage(convId, {
          id: `usr_${Date.now()}`,
          role: 'user',
          content:
            attachments.length > 0
              ? [...attachments, ...(text.length > 0 ? [{ type: 'text' as const, text }] : [])]
              : text,
          timestamp: Date.now(),
        })
        useChatStore.getState().appendMessage(convId, {
          id: `err_${Date.now()}`,
          role: 'error',
          content: '连接未就绪，消息未发送。请等待连接恢复后重试。',
          timestamp: Date.now(),
        })
        return
      }

      dispatchUserMessage(convId, `usr_${Date.now()}`, text, attachments)
    },
    [backendWorkspaceId, dispatchUserMessage, token, wsState],
  )

  // ── outbox drain：上一条执行完毕后自动取下一条执行 ──
  const executeOutboxItem = useCallback(
    (convId: string, item: OutboxItem) => {
      dispatchUserMessage(convId, item.id, item.text, item.attachments)
    },
    [dispatchUserMessage],
  )

  useEffect(() => {
    if (!activeConvId) return
    if (isStreaming) return
    if (wsState !== 'ready') return
    const next = useChatStore.getState().dequeueOutbox(activeConvId)
    if (!next) {
      useChatStore.getState().setQueued(activeConvId, false, 0)
      return
    }
    const remaining = useChatStore.getState().peekOutbox(activeConvId).length
    useChatStore.getState().setQueued(activeConvId, remaining > 0, remaining)
    executeOutboxItem(activeConvId, next)
  }, [activeConvId, executeOutboxItem, isStreaming, wsState])

  // ── 队列管理 ──
  const runQueued = useCallback(
    (itemId: string) => {
      if (!activeConvId) return
      if (isStreaming) {
        const remaining = useChatStore.getState().prioritizeOutbox(activeConvId, itemId)
        useChatStore.getState().setQueued(activeConvId, remaining > 0, remaining)
        return { message: '已调整到下一位执行', tone: 'success' as const }
      }
      if (wsState !== 'ready') {
        return { message: '连接未就绪，暂时无法执行队列任务', tone: 'error' as const }
      }
      const next = useChatStore.getState().takeOutbox(activeConvId, itemId)
      if (!next) return
      const remaining = useChatStore.getState().peekOutbox(activeConvId).length
      useChatStore.getState().setQueued(activeConvId, remaining > 0, remaining)
      executeOutboxItem(activeConvId, next)
    },
    [activeConvId, executeOutboxItem, isStreaming, wsState],
  )

  const deleteQueued = useCallback(
    (itemId: string) => {
      if (!activeConvId) return
      const remaining = useChatStore.getState().removeOutbox(activeConvId, itemId)
      useChatStore.getState().setQueued(activeConvId, remaining > 0, remaining)
      return { message: '已从队列中删除', tone: 'success' as const }
    },
    [activeConvId],
  )

  // ── 风险确认（确认继续/取消任务） ──
  const submitConfirmation = useCallback(
    (decision: 'approve' | 'deny') => {
      if (!activeConvId || wsState !== 'ready' || isStreaming) return
      const text = decision === 'approve' ? '确认继续' : '取消任务'
      dispatchUserMessage(activeConvId, `usr_${Date.now()}`, text, [], { confirmation_decision: decision })
    },
    [activeConvId, dispatchUserMessage, isStreaming, wsState],
  )

  // ── 失败重试：复用最后一条用户消息的内容重新发起（不重复落消息） ──
  const retryMessage = useCallback(
    (errorMessageId: string) => {
      if (!activeConvId) return
      const all = useChatStore.getState().convStates[activeConvId]?.messages ?? []
      const errIdx = all.findIndex((m) => m.id === errorMessageId)
      if (errIdx < 0) return
      let lastUser: Message | null = null
      for (let i = errIdx - 1; i >= 0; i -= 1) {
        if (all[i].role === 'user') {
          lastUser = all[i]
          break
        }
      }
      if (!lastUser) return
      const { text, attachments } = splitAttachmentContent(lastUser.content)
      if (wsState !== 'ready') {
        useChatStore.getState().appendMessage(activeConvId, {
          id: `err_${Date.now()}`,
          role: 'error',
          content: '连接未就绪，无法重试发送。请等待连接恢复后再试。',
          timestamp: Date.now(),
        })
        return
      }
      useChatStore.getState().initStreaming(activeConvId)
      wsSendChat(activeConvId, text, attachments)
    },
    [activeConvId, wsSendChat, wsState],
  )

  // ── AskUserQuestion 澄清提交：后端断点续跑 ──
  const clarifySubmit = useCallback(
    (approvalId: string, toolCallId: string, answer: string) => {
      if (!activeConvId) return
      send({
        type: 'tool.clarify',
        conversation_id: activeConvId,
        approval_id: approvalId,
        tool_call_id: toolCallId,
        answer,
      } as WSClientMessage)
      // 答案已回传，后端将解除 await 续跑；立即清除暂停态，让提问卡片消失。
      useChatStore.getState().setClarifyRequest(activeConvId, null)
    },
    [activeConvId, send],
  )

  const abort = useCallback(() => {
    if (activeConvId) {
      send({ type: 'chat.abort', conversation_id: activeConvId } as WSClientMessage)
    }
  }, [activeConvId, send])

  const optimizePrompt = useCallback(
    async (text: string): Promise<string> => {
      const res = await fetch(apiUrl('/api/optimize-prompt'), {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ prompt: text }),
      })
      if (!res.ok) {
        const body = await res.text().catch(() => '')
        throw new Error(body || `优化失败（HTTP ${res.status}）`)
      }
      const data = (await res.json()) as { optimized: string }
      return data.optimized
    },
    [token],
  )

  return {
    wsState,
    sendMessage,
    retryMessage,
    submitConfirmation,
    clarifySubmit,
    abort,
    runQueued,
    deleteQueued,
    selectConversation,
    ensureHistory,
    optimizePrompt,
  }
}
