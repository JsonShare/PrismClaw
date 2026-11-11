import type { RunProjection, TimelineNode, TaskTimelinePayload } from './trajectory'

/* ─── Domain Models ─── */

export interface TextBlock {
  type: 'text'
  text: string
}

export interface ImageBlock {
  type: 'image'
  data: string
  mime_type: string
  source_path?: string | null
  // 后端 /api/files/raw 的 HTTP 访问地址（存储无关）。刷新重放场景下后端
  // _sanitize_content 会主动附加，前端优先消费它，缺失时回退到 source_path 拼装。
  file_url?: string | null
}

export interface FileBlock {
  type: 'file'
  path: string
  name?: string
  mime_type?: string | null
  size_bytes?: number | null
  // 同 ImageBlock.file_url：后端产出的 HTTP 访问地址，前端优先消费。
  file_url?: string | null
  // 标记「这是被分解展示的源文档本身」（如上传的 Word 经抽取后内联了图片+文字，
  // 但仍保留此文件卡作为「来源记录」）。前端据此把源文档卡渲染在内容之前作为表头。
  is_source_document?: boolean
}

export type AttachmentBlock = ImageBlock | FileBlock

export type ContentBlock = TextBlock | AttachmentBlock

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system' | 'error'
  content: string | ContentBlock[]
  timestamp: number
  metadata?: MessageMetadata
  timeline?: TimelineNode[]
}

export interface MessageMetadata {
  durationMs?: number
  usage?: {
    input?: number
    output?: number
    cacheRead?: number
  }
  model?: string
  feedback?: 'up' | 'down' | null
  /** AskUserQuestion 人机交互：后端经 tool.clarify_request 注入的待回答标识 */
  clarifyRequest?: { approvalId: string; toolCallId: string } | null
  referenceSources?: string[]
  outputFiles?: string[]
  interrupted?: boolean
  /** 回答结束后后端经 event:questions 下发的引导问题（至多 3 条，不持久化） */
  followupQuestions?: string[]
}

export interface QuestionOption {
  label: string
  description: string
}

export interface QuestionItem {
  question: string
  header: string
  multiSelect: boolean
  options: QuestionOption[]
}

export interface Conversation {
  id: string
  title: string
  updatedAt: number
  active: boolean
  type?: 'normal' | 'scheduled'
  workspaceId?: string
}

export interface Workspace {
  id: string
  name: string
  trusted?: boolean
}

/* ─── WebSocket Protocol ─── */

export type WSState = 'connecting' | 'identifying' | 'ready' | 'disconnected'

// Client → Server
export interface WSClientIdentify {
  type: 'identify'
  token: string
  protocol_version: number
}

export interface WSClientChatSend {
  type: 'chat.send'
  conversation_id: string
  workspace_id?: string
  content: string
  attachments?: AttachmentBlock[]
  mode?: 'chat' | 'work'
}

export interface WSClientChatAbort {
  type: 'chat.abort'
  conversation_id: string
}

export interface WSClientToolClarify {
  type: 'tool.clarify'
  conversation_id: string
  approval_id: string
  tool_call_id: string
  answer: string
}

export interface WSClientPong {
  type: 'pong'
}

export type WSClientMessage =
  | WSClientIdentify
  | WSClientChatSend
  | WSClientChatAbort
  | WSClientToolClarify
  | WSClientPong

// Server → Client
export interface WSServerHello {
  type: 'hello'
  data: { heartbeat_interval: number }
}

export interface WSServerReady {
  type: 'ready'
  data: {
    user_id: string
    ws_session_id: string
    conversations: Conversation[]
    protocol_version: number
  }
}

export interface WSServerChatStart {
  type: 'chat.start'
  conversation_id: string
  data: { conversation_id: string }
}

export interface WSServerChatDelta {
  type: 'chat.delta'
  conversation_id: string
  data: { text: string; segmentId?: number }
}

export interface WSServerChatDone {
  type: 'chat.done'
  conversation_id: string
  data: {
    final_message: Message | string
    message_id?: string
    aborted?: boolean
    usage?: { input?: number; output?: number; cache_read?: number }
    model?: string
  }
}

export interface WSServerChatQueued {
  type: 'chat.queued'
  conversation_id: string
  data: { position: number }
}

export interface WSServerReference {
  type: 'event:reference'
  conversation_id: string
  data: { message_id?: string; sources: string[] }
}

export interface WSServerQuestions {
  type: 'event:questions'
  conversation_id: string
  data: { message_id?: string; questions: string[] }
}

export interface WSServerOutputs {
  type: 'event:outputs'
  conversation_id: string
  data: { message_id?: string; files: string[] }
}

export interface WSServerTaskTimeline {
  type: 'task.timeline'
  conversation_id: string
  data: TaskTimelinePayload
}

/** P3：后端生命周期 broadcaster 在 phase 窗口周期下发的全量运行态快照。
 *  前端据此整体替换 streaming 运行态（hydrateRun），取代序号增量同步的自愈手段。 */
export interface WSServerRunSnapshot {
  type: 'run.snapshot'
  conversation_id: string
  data: RunProjection
}

export interface WSServerToolClarifyRequest {
  type: 'tool.clarify_request'
  conversation_id: string
  data: {
    approval_id: string
    run_id: string
    turn_id: string
    tool_call_id: string
    tool_name: string
    questions: Array<Record<string, unknown>>
  }
}

export interface WSServerPing {
  type: 'ping'
}

export interface WSServerError {
  type: 'error'
  data: { code?: string; message: string }
}

/** executing 期心跳：仅刷新前端看门狗静默计时，不携带数据（broadcaster 每 15s 推一帧）。 */
export interface WSServerRunKeepalive {
  type: 'run.keepalive'
  conversation_id: string
  data: { state?: string }
}

export type WSServerMessage =
  | WSServerHello
  | WSServerReady
  | WSServerChatStart
  | WSServerChatDelta
  | WSServerChatDone
  | WSServerChatQueued
  | WSServerReference
  | WSServerQuestions
  | WSServerOutputs
  | WSServerTaskTimeline
  | WSServerRunSnapshot
  | WSServerRunKeepalive
  | WSServerToolClarifyRequest
  | WSServerPing
  | WSServerError

/* ─── Auth ─── */

export interface AuthTokenResponse {
  token: string
  user_id: string
  nickname?: string | null
}

/* ─── Theme ─── */

export type Theme = 'dark' | 'light' | 'system'
