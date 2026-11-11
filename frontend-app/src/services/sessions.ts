import type { ContentBlock, Message, RunProjection, TimelineNode } from '../types'
import { markersToReadable } from '../lib/chat/markers'
import { isKnownTimelineNode } from '../lib/chat/timelineSync'
import { http } from './request'

export interface SessionMeta {
  id: string
  created_at?: string
  message_count?: number
  title?: string | null
  last_interaction_at?: string | null
  parent_session_id?: string | null
  type?: 'normal' | 'scheduled'
}

export interface SessionsPage {
  items: SessionMeta[]
  has_more: boolean
}

export interface SessionDetail {
  id: string
  workspace_id: string
  created_at: string
  last_interaction_at?: string | null
  message_count: number
}

interface RawMessage {
  id?: string
  role: string
  content?: string | ContentBlock[]
  timestamp?: string
  feedback?: string | null
  output_files?: string[]
  timeline?: unknown[]
  metadata?: Message['metadata'] | null
}

function normalizeContent(raw: unknown): string | ContentBlock[] {
  if (typeof raw === 'string') return raw
  if (!Array.isArray(raw)) return ''
  const blocks: ContentBlock[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
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
        file_url: typeof block.file_url === 'string' ? block.file_url : undefined,
        is_source_document: block.is_source_document === true ? true : undefined,
      })
    } else if (
      (block.type === 'image' || block.type === 'image_url') &&
      typeof block.mime_type === 'string' &&
      (typeof block.data === 'string' || typeof block.source_path === 'string')
    ) {
      blocks.push({
        type: 'image',
        data: typeof block.data === 'string' ? block.data : '',
        mime_type: block.mime_type,
        source_path: typeof block.source_path === 'string' ? block.source_path : undefined,
        file_url: typeof block.file_url === 'string' ? block.file_url : undefined,
      })
    }
  }
  return blocks
}

export function hasVisibleContent(content: string | ContentBlock[]): boolean {
  if (typeof content === 'string') return content.trim().length > 0
  for (const block of content) {
    if (block.type === 'text' && block.text.trim()) return true
    if (block.type === 'image') return true
    if (block.type === 'file' && block.path.trim()) return true
  }
  return false
}

export function contentToText(content: string | ContentBlock[]): string {
  if (typeof content === 'string') return markersToReadable(content)
  return markersToReadable(
    content
      .map((block) => (block.type === 'text' ? block.text : block.type === 'file' ? block.name || block.path : ''))
      .filter(Boolean)
      .join('\n'),
  )
}

function normalizeMessage(raw: RawMessage): Message {
  const content = normalizeContent(raw.content)
  let metadata: Message['metadata']
  if (raw.feedback === 'up' || raw.feedback === 'down') metadata = { feedback: raw.feedback }
  else if (raw.feedback === 'none') metadata = { feedback: null }

  // 后端 replay 时带回 durationMs / interrupted，直接合并，避免刷新后信息丢失
  if (raw.metadata && typeof raw.metadata === 'object') {
    metadata = { ...(metadata ?? {}), ...raw.metadata }
  }

  const outputFiles = Array.isArray(raw.output_files)
    ? raw.output_files.filter((p): p is string => typeof p === 'string' && p.trim().length > 0)
    : []
  if (outputFiles.length) metadata = { ...(metadata ?? {}), outputFiles }

  const timeline: TimelineNode[] = Array.isArray(raw.timeline) ? raw.timeline.filter(isKnownTimelineNode) : []

  return {
    id: raw.id || `msg_${Math.random().toString(36).slice(2)}`,
    role: raw.role === 'user' ? 'user' : 'assistant',
    content,
    timestamp: raw.timestamp ? new Date(raw.timestamp).getTime() : Date.now(),
    metadata,
    timeline: timeline.length ? timeline : undefined,
  }
}

export async function listSessions(workspaceId: string, limit = 20, offset = 0): Promise<SessionsPage> {
  return http.get<SessionsPage>('/api/sessions', { workspace_id: workspaceId, limit, offset })
}

/**
 * 跨工作区会话列表：后端 /api/sessions 只接受具体 workspace_id，
 * 因此按工作区并行拉取足够深度后在本地归并，保证「全部」视图真实可用。
 */
export async function listSessionsAcross(
  workspaceIds: string[],
  limit = 20,
  offset = 0,
): Promise<SessionsPage> {
  if (!workspaceIds.length) return { items: [], has_more: false }
  const depth = offset + limit
  const pages = await Promise.all(
    workspaceIds.map((id) => listSessions(id, depth, 0).catch(() => ({ items: [], has_more: false }))),
  )
  const timeOf = (iso: string | null) => {
    const t = iso ? Date.parse(iso) : NaN
    return Number.isFinite(t) ? t : 0
  }
  const merged = pages
    .flatMap((page) => page.items)
    .sort((a, b) => timeOf(b.last_interaction_at) - timeOf(a.last_interaction_at))
  return {
    items: merged.slice(offset, offset + limit),
    has_more: merged.length > offset + limit || pages.some((page) => page.has_more),
  }
}

export async function createSession(workspaceId: string, title?: string): Promise<{ session_id: string }> {
  return http.post<{ session_id: string }>('/api/sessions', { workspace_id: workspaceId, title })
}

export async function getSession(sessionId: string): Promise<SessionDetail> {
  return http.get<SessionDetail>(`/api/sessions/${encodeURIComponent(sessionId)}`)
}

export async function renameSession(sessionId: string, title: string): Promise<void> {
  await http.patch(`/api/sessions/${encodeURIComponent(sessionId)}`, { title })
}

export async function deleteSession(sessionId: string): Promise<void> {
  await http.delete(`/api/sessions/${encodeURIComponent(sessionId)}`)
}

export async function fetchMessages(sessionId: string, limit = 100, offset = 0): Promise<Message[]> {
  const raw = await http.get<RawMessage[]>(`/api/sessions/${encodeURIComponent(sessionId)}/messages`, {
    limit,
    offset,
  })
  return (Array.isArray(raw) ? raw : [])
    .map(normalizeMessage)
    .filter((message) => hasVisibleContent(message.content) || (message.timeline?.length ?? 0) > 0)
}

export async function fetchRunningRun(sessionId: string, workspaceId: string): Promise<RunProjection | null> {
  const res = await http.get<{ run: RunProjection | null }>(
    `/api/conversations/${encodeURIComponent(sessionId)}/run`,
    { workspace_id: workspaceId },
  )
  return res?.run ?? null
}

export async function setMessageFeedback(sessionId: string, messageId: string, vote: 'up' | 'down' | 'none') {
  await http.post(`/api/sessions/${encodeURIComponent(sessionId)}/feedback`, {
    message_id: messageId,
    vote,
  })
}
