import { useCallback, useEffect, useRef, useState } from 'react'
import { useSessionStore } from '../../stores/session'
import { useChatStore } from '../../stores/chat'
import { apiUrl } from '../../lib/system-adapter'
import { isKnownTimelineNode } from '../../lib/chat/timelineSync'
import type { ContentBlock, Conversation, FileChange, Message, TimelineNode, ToolCallInfo, WSState } from '../../types'

interface SessionListResponse {
  id: string
  created_at?: string
  message_count?: number
  title?: string | null
  last_interaction_at?: string | null
  type?: 'normal' | 'scheduled'
}

interface MessageHistoryEntry {
  role: string
  content?: string | ContentBlock[]
  id?: string
  timestamp?: string
  feedback?: string | null
  output_files?: string[]
  file_changes?: FileChange[]
  timeline?: TimelineNode[]
  /** 后端 _build_display_messages 回填的 metadata（durationMs 等） */
  metadata?: Record<string, unknown>
  tool_calls?: Array<{
    id?: string
    name?: string
    arguments?: unknown
    result?: string
    status?: 'running' | 'done' | 'error'
  }>
}

function normalizeContent(raw: unknown): string | ContentBlock[] {
  if (typeof raw === 'string') return raw
  if (!Array.isArray(raw)) return ''
  const blocks: ContentBlock[] = []
  for (const item of raw) {
    if (item && typeof item === 'object') {
      const block = item as Record<string, unknown>
      if (block.type === 'text' && typeof block.text === 'string') {
        blocks.push({ type: 'text', text: block.text })
      } else if (block.type === 'file' && typeof block.path === 'string') {
        blocks.push({
          type: 'file',
          path: block.path,
          name: typeof block.name === 'string' ? block.name : undefined,
          mime_type: typeof block.mime_type === 'string' ? block.mime_type : undefined,
          size_bytes: typeof block.size_bytes === 'number' ? block.size_bytes : undefined,
          // 后端产出的 HTTP 访问地址（存储无关），前端优先消费；缺失回退到 path 拼装。
          file_url: typeof block.file_url === 'string' ? block.file_url : undefined,
          // 标记「这是被分解展示的源文档本身」，前端据此把源文档卡渲染在内容之前。
          is_source_document:
            block.is_source_document === true ? true : undefined,
        })
      } else if (
        (block.type === 'image' || block.type === 'image_url') &&
        typeof block.mime_type === 'string' &&
        // 刷新重放场景：后端 _sanitize_content 会丢弃内联 data（省带宽），
        // 仅保留 source_path（及后端附加的 file_url），由前端回退到 /api/files/raw 拉原图。
        // 因此不能要求 data 必填，否则重放的消息里所有图片块都会被静默丢弃。
        (typeof block.data === 'string' || typeof block.source_path === 'string')
      ) {
        blocks.push({
          type: 'image',
          data: typeof block.data === 'string' ? block.data : '',
          mime_type: block.mime_type,
          source_path: typeof block.source_path === 'string' ? block.source_path : undefined,
          // 后端产出的 HTTP 访问地址（存储无关），前端 ImageGrid 优先消费。
          file_url: typeof block.file_url === 'string' ? block.file_url : undefined,
        })
      }
    }
  }
  return blocks
}

function hasVisibleContent(content: string | ContentBlock[]): boolean {
  if (typeof content === 'string') return content.trim().length > 0
  for (const b of content) {
    if (b.type === 'text' && b.text.trim()) return true
    if (b.type === 'image') return true
    if (b.type === 'file' && b.path.trim()) return true
  }
  return false
}

function toToolCalls(raw: MessageHistoryEntry['tool_calls']): ToolCallInfo[] | undefined {
  if (!Array.isArray(raw) || raw.length === 0) return undefined
  const out: ToolCallInfo[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const id = typeof item.id === 'string' && item.id ? item.id : null
    const name = typeof item.name === 'string' && item.name ? item.name : null
    if (!id || !name) continue
    const argsRaw = (item as any).arguments
    let args: Record<string, unknown> = {}
    if (argsRaw && typeof argsRaw === 'object' && !Array.isArray(argsRaw)) args = argsRaw as Record<string, unknown>
    else if (typeof argsRaw === 'string') {
      try {
        const parsed = JSON.parse(argsRaw) as unknown
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) args = parsed as Record<string, unknown>
        else args = { _raw: parsed }
      } catch {
        args = { _raw: argsRaw }
      }
    }
    const status: ToolCallInfo['status'] =
      item.status === 'running' || item.status === 'error' || item.status === 'done' ? item.status : 'done'
    out.push({
      id,
      name,
      arguments: args,
      result: typeof item.result === 'string' ? item.result : undefined,
      status,
    })
  }
  return out.length > 0 ? out : undefined
}

// 会话在后端已不存在（多为 Redis TTL 过期、历史索引被惰性 GC 清除）：若该
// 会话恰是当前激活会话，清空激活态并抹掉 localStorage 中的陈旧 id，打断
// 「恢复死 id → ensureHistory → 404」的死循环，前端回退到空/新建会话。
function clearStaleActiveConv(convId: string): void {
  if (convId && convId === useSessionStore.getState().activeConvId) {
    useSessionStore.getState().setActiveConvId(null)
  }
}

export function useSessionLoader(token: string | null, wsState: WSState, workspaceIds: string[]) {
  const setConversations = useSessionStore((s) => s.setConversations)
  const setMessages = useChatStore((s) => s.setMessages)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasMore, setHasMore] = useState(true)
  const pageSize = 10

  // 每个工作区各自的分页状态：offset 记录已加载条数，hasMoreMap 记录是否还有更多。
  const offsetsRef = useRef<Record<string, number>>({})
  const hasMoreMapRef = useRef<Record<string, boolean>>({})

  // 用拼接字符串作为稳定依赖，避免每次渲染传入新数组导致重复拉取。
  const workspaceIdsKey = workspaceIds.join(',')

  useEffect(() => {
    if (wsState !== 'ready' || !token) return
    const ids = workspaceIdsKey ? workspaceIdsKey.split(',') : ['default']
    if (ids.length === 0) return
    let stopped = false

    const mapItem = (s: SessionListResponse, wsId: string): Conversation => ({
      id: s.id,
      title: s.title || '新对话',
      updatedAt: s.last_interaction_at
        ? new Date(s.last_interaction_at).getTime()
        : s.created_at
          ? new Date(s.created_at).getTime()
          : Date.now(),
      active: true,
      type: s.type ?? 'normal',
      workspaceId: wsId,
    })

    const fetchPage = async (wsId: string, offset: number): Promise<{ items: Conversation[]; more: boolean; ok: boolean }> => {
      const res = await fetch(
        apiUrl(`/api/sessions?workspace_id=${encodeURIComponent(wsId)}&limit=${pageSize}&offset=${offset}`),
        { headers: { Authorization: `Bearer ${token}` } },
      )
      if (res.status === 401 || !res.ok) return { items: [], more: false, ok: false }
      const data = await res.json()
      if (!data || typeof data !== 'object') return { items: [], more: false, ok: false }
      const raw = Array.isArray(data.items) ? data.items : []
      return { items: raw.map((s: SessionListResponse) => mapItem(s, wsId)), more: Boolean(data.has_more), ok: true }
    }

    const run = async (attempt: number): Promise<void> => {
      if (stopped) return
      try {
        const results = await Promise.all(ids.map((wsId) => fetchPage(wsId, 0)))
        if (stopped) return
        const merged: Conversation[] = []
        const offsets: Record<string, number> = {}
        const moreMap: Record<string, boolean> = {}
        results.forEach((r, i) => {
          const wsId = ids[i]
          merged.push(...r.items)
          offsets[wsId] = r.items.length
          moreMap[wsId] = r.more
        })
        merged.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))
        offsetsRef.current = offsets
        hasMoreMapRef.current = moreMap
        setConversations(merged)
        // 仅在所有工作区都成功返回后置位：任一工作区失败时列表对该工作区不完整，
        // 不能据此判定某个会话不存在，否则会误清空该工作区下恢复的 activeConvId。
        if (results.length > 0 && results.every((r) => r.ok)) {
          useSessionStore.getState().markSessionsLoaded()
        }
        const anyMore = Object.values(moreMap).some(Boolean)
        useSessionStore.getState().setSessionsHasMore(anyMore)
        setHasMore(anyMore)
      } catch {
        if (stopped) return
        if (attempt >= 2) return
        const delay = 800 * (attempt + 1)
        window.setTimeout(() => void run(attempt + 1), delay)
      }
    }

    void run(0)
    return () => {
      stopped = true
    }
  }, [wsState, token, workspaceIdsKey, setConversations])

  const loadMore = useCallback(async (): Promise<void> => {
    if (loadingMore || !hasMore || !token) return
    const ids = workspaceIdsKey ? workspaceIdsKey.split(',') : ['default']
    const moreIds = ids.filter((wsId) => hasMoreMapRef.current[wsId])
    if (moreIds.length === 0) return
    setLoadingMore(true)
    try {
      const results = await Promise.all(
        moreIds.map((wsId) =>
          (async () => {
            const offset = offsetsRef.current[wsId] ?? 0
            const res = await fetch(
              apiUrl(`/api/sessions?workspace_id=${encodeURIComponent(wsId)}&limit=${pageSize}&offset=${offset}`),
              { headers: { Authorization: `Bearer ${token}` } },
            )
            if (!res.ok) return { wsId, items: [] as Conversation[], more: false }
            const data = await res.json()
            const raw = Array.isArray(data?.items) ? data.items : []
            const items: Conversation[] = raw.map((s: SessionListResponse) => ({
              id: s.id,
              title: s.title || '新对话',
              updatedAt: s.last_interaction_at
                ? new Date(s.last_interaction_at).getTime()
                : s.created_at
                  ? new Date(s.created_at).getTime()
                  : Date.now(),
              active: true,
              type: s.type ?? 'normal',
              workspaceId: wsId,
            }))
            return { wsId, items, more: Boolean(data.has_more) }
          })(),
        ),
      )
      const appended: Conversation[] = []
      for (const r of results) {
        offsetsRef.current[r.wsId] = (offsetsRef.current[r.wsId] ?? 0) + r.items.length
        hasMoreMapRef.current[r.wsId] = r.more
        appended.push(...r.items)
      }
      const existing = useSessionStore.getState().conversations
      const existingIds = new Set(existing.map((c) => c.id))
      const merged = [...existing, ...appended.filter((c) => !existingIds.has(c.id))]
      merged.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))
      setConversations(merged)
      const anyMore = Object.values(hasMoreMapRef.current).some(Boolean)
      useSessionStore.getState().setSessionsHasMore(anyMore)
      setHasMore(anyMore)
    } finally {
      setLoadingMore(false)
    }
  }, [loadingMore, hasMore, token, workspaceIdsKey, setConversations])

  const loadMessagesFor = useCallback(
    async (convId: string): Promise<void> => {
      // historyStatus 生命周期的唯一写入点：loading → loaded（含保留本地消息）
      // 或失败回 idle（允许下次重试）。
      useChatStore.getState().setHistoryStatus(convId, 'loading')
      try {
        const headers = { Authorization: `Bearer ${token}` }
        let offset = 0
        let sessionMissing = false
        try {
          const metaRes = await fetch(apiUrl(`/api/sessions/${encodeURIComponent(convId)}`), { headers })
          if (metaRes.status === 404) {
            // 会话在后端已不存在（多为 TTL 过期/被 GC）：标记缺失，跳过后续拉取。
            sessionMissing = true
          } else if (metaRes.ok) {
            const meta = (await metaRes.json()) as unknown
            if (meta && typeof meta === 'object') {
              const mc = (meta as { message_count?: unknown }).message_count
              if (typeof mc === 'number' && Number.isFinite(mc) && mc > 0) {
                offset = Math.max(0, Math.floor(mc) - 100)
              }
            }
          }
        } catch {}

        if (sessionMissing) {
          // 会话在后端已不存在：若该会话恰是当前激活会话，清空激活态打断 404 死循环。
          clearStaleActiveConv(convId)
          useChatStore.getState().setHistoryStatus(convId, 'idle')
          return
        }

        const res = await fetch(
          apiUrl(
            `/api/sessions/${encodeURIComponent(convId)}/messages?limit=100&offset=${encodeURIComponent(
              String(offset),
            )}`,
          ),
          { headers },
        )
        if (res.status === 404) {
          // 会话在后端已不存在（与 meta 404 同源）：清空激活态打断死循环。
          clearStaleActiveConv(convId)
          useChatStore.getState().setHistoryStatus(convId, 'idle')
          return
        }
        if (!res.ok) {
          useChatStore.getState().setHistoryStatus(convId, 'idle')
          return
        }
        const entries: MessageHistoryEntry[] = await res.json()
        if (!Array.isArray(entries)) {
          useChatStore.getState().setHistoryStatus(convId, 'idle')
          return
        }
        const serverMsgs: Message[] = entries
          .map((e) => {
            const content = normalizeContent(e.content)
            const role: Message['role'] = e.role === 'user' ? 'user' : 'assistant'
            let metadata: Message['metadata'] = undefined
            // 后端刷新重放回填的 durationMs（_build_display_messages 计算）：直接透传。
            const backendMeta = e.metadata && typeof e.metadata === 'object' ? (e.metadata as Record<string, unknown>) : undefined
            if (backendMeta && typeof backendMeta.durationMs === 'number' && backendMeta.durationMs > 0) {
              metadata = { durationMs: backendMeta.durationMs }
            }
            if (e.feedback === 'up' || e.feedback === 'down') metadata = { ...(metadata ?? {}), feedback: e.feedback }
            else if (e.feedback === 'none') metadata = { ...(metadata ?? {}), feedback: null }
            const outputFiles = Array.isArray(e.output_files)
              ? e.output_files.filter((p): p is string => typeof p === 'string' && p.trim().length > 0)
              : []
            if (outputFiles.length > 0) metadata = { ...(metadata ?? {}), outputFiles }
            const fileChanges = Array.isArray(e.file_changes)
              ? e.file_changes.filter(
                  (c): c is FileChange => !!c && typeof c === 'object' && typeof (c as any).path === 'string',
                )
              : []
            if (fileChanges.length > 0) metadata = { ...(metadata ?? {}), fileChanges }
            // 执行过程持久化：后端把 timeline 终态落库（CustomEntry: execution_timeline），
            // GET messages 时附着到 item.timeline（见 routes.py:2346）。这里恢复到
            // message.timeline，供 MessageBubble 的 ExecutionTimeline 历史回放——刷新
            // 页面后仍能看到完整执行过程，不依赖被 finalizeStreaming 清空的 liveTimeline。
            //
            // 水合过滤：持久化数据进入前端的唯一边界，挡掉类型不在
            // TimelineNode 联合里的未知节点。渲染层（buildStepRows / timelineSync）
            // 按当前类型契约直接读字段，不做未知类型兜底——否则真实数据缺陷会被
            // 静默吞掉；换成这里的单点过滤，既安全又不污染渲染层。
            const timeline = Array.isArray(e.timeline) ? e.timeline.filter(isKnownTimelineNode) : []
            return {
              id: e.id ?? `msg_${Math.random().toString(36).slice(2)}`,
              role,
              content,
              timestamp: e.timestamp ? new Date(e.timestamp).getTime() : Date.now(),
              toolCalls: toToolCalls(e.tool_calls),
              metadata,
              timeline: timeline.length > 0 ? timeline : undefined,
            }
          })
          // 水合过滤：只保留「有可见正文」或「携带执行过程(timeline)」的消息。
          // 注意：agent 的某些回合正文为空（纯工具调用 / 仅交付物 / 空答案），但
          // 仍产出了有价值的执行过程 timeline。若仅按 hasVisibleContent 过滤，这类
          // 消息会被整条丢弃，连带其 timeline 一起丢失——表现为「切换菜单再切回任务，
          // 执行过程不显示」。后端已在 GET /messages 把 timeline 附着到对应消息
          // （routes.py:602），这里必须放行 timeline 非空的消息，否则历史回放拿不到。
          .filter((m) => hasVisibleContent(m.content) || (m.timeline != null && m.timeline.length > 0))
        const local = useChatStore.getState().convStates[convId]?.messages ?? []
        if (serverMsgs.length === 0 && local.length > 0) {
          // 服务端无消息但本地有（刚发送的乐观消息）：保留本地，视为已加载。
          useChatStore.getState().setHistoryStatus(convId, 'loaded')
          return
        }
        setMessages(convId, serverMsgs) // setMessages 内部置 historyStatus='loaded'
      } catch {
        useChatStore.getState().setHistoryStatus(convId, 'idle')
      }
    },
    [setMessages, token],
  )

  return { loadMessagesFor, loadMore, hasMore, loadingMore }
}
