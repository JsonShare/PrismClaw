import type { FileChange } from './file'
import type { DomainTaskEventPayload } from './tasks'
import type { RunProjection, TimelineNode, TaskRuntimeProjection, TaskTimelinePayload } from './trajectory'

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
  // 服务端事件 seq（历史/增量消息才有）。sync 用水位判断补差，本地乐观消息无此字段。
  seq?: number
  // 服务端 user entry 携带的客户端标识：sync 首次回传该 user 消息时，客户端把
  // 本地乐观气泡（id === clientMessageId）原地 re-key 为服务端 id + seq。
  clientMessageId?: string
  toolCalls?: ToolCallInfo[]
  metadata?: MessageMetadata
  timeline?: TimelineNode[]
}

export interface ToolCallInfo {
  id: string
  name: string
  arguments: Record<string, unknown>
  result?: string
  status: 'running' | 'done' | 'error'
  progressPhase?: 'running' | 'waiting_approval' | 'finalizing'
  elapsedSeconds?: number
  lastProgressAt?: number
  startedAt?: number
  endedAt?: number
}

/** 来源形态：external web link 或知识库（RAG）检索结果 */
export type ReferenceOrigin = 'web' | 'rag'

/** RAG 文档的结构化小节（如药品说明书的「主要成分」「用法用量」） */
export interface ReferenceSection {
  title: string
  content: string
}

export interface ReferenceSource {
  /** 标题（无标题时退化为 URL） */
  title: string
  url?: string | null
  /** 发布机构 / 数据库 / 期刊 / 药品厂商 */
  publisher?: string | null
  /** 年份/日期，字符串以兼容 "2024" 与 "2024-03-01" */
  year?: string | null
  /** guideline | paper | drug | database | news | web | book | report */
  kind?: string
  /** web：右侧栏 iframe 打开 url；rag：右侧栏展示检索片段/摘要 */
  origin?: ReferenceOrigin
  /** ---- 以下为 RAG 来源预留字段 ---- */
  authors?: string | null
  impactFactor?: string | null
  doi?: string | null
  /** 检索命中片段（可能被截断） */
  snippet?: string | null
  abstract?: string | null
  sections?: ReferenceSection[] | null
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
  followupQuestions?: string[]
  /** AskUserQuestion 人机交互：后端经 tool.clarify_request 注入的待回答标识 */
  clarifyRequest?: { approvalId: string; toolCallId: string } | null
  referenceSources?: ReferenceSource[]
  outputFiles?: string[]
  /** 后端经 event:file_changes 注入的文件变更审阅数据（来自 ToolResult.metadata["file_change"]） */
  fileChanges?: FileChange[]
  interrupted?: boolean
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

/** 任务挂起后的确认决策，与后端 ConfirmationDecision 对齐。 */
export type ConfirmationDecision = 'approve' | 'deny'

export interface WSClientChatSend {
  type: 'chat.send'
  conversation_id: string
  workspace_id?: string
  content: string
  confirmation_decision?: ConfirmationDecision
  attachments?: AttachmentBlock[]
  trajectory?: string  // 'full', 'dry', or undefined (use env default)
  // 技能/插件/引用文件不单独下发：输入框卡片序列化为正文标记，正文是唯一真相源。
  // 客户端生成的消息标识：后端写入 user entry，sync 回传后用于本地气泡 re-key。
  client_message_id?: string
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
  data: { message_id?: string; sources: ReferenceSource[] }
}

export interface WSServerQuestions {
  type: 'event:questions'
  conversation_id: string
  data: { message_id?: string; questions: string[] }
}

export interface WSServerOutbox {
  type: 'event:outbox'
  conversation_id: string
  data: { message_id?: string; files: string[] }
}

export interface WSServerFileChanges {
  type: 'event:file_changes'
  conversation_id: string
  data: { message_id?: string; changes: FileChange[] }
}

export interface WSServerTaskRuntime {
  type: 'task.runtime'
  conversation_id: string
  data: {
    event_seq: number
    idempotency_key: string
    projection: TaskRuntimeProjection | null
  }
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

export interface SubagentProgressItem {
  sub_session_id: string
  subagent_name: string
  turn: number
  max_turns: number
  partial_status:
    | 'thinking'
    | 'tool_calling'
    | 'tool_running'
    | 'completed'
    | 'failed'
    | 'aborted'
  current_tool: string | null
  active_skill_name?: string | null
  invoked_skill_names?: string[]
  duration_so_far: number
  handoff_chain_id?: string | null
  from_subagent?: string | null
  /** 当前工具的紧凑参数摘要（如 "file_path=src/app.ts"），仅展示用。 */
  current_tool_summary?: string | null
  /** 有界最近动作（≤5 条），由后端 emitter 在工具切换时追加。 */
  recent_activity?: string[]
}

/** 右栏展示用：在进度项上补全 sub_agent 调用参数里的任务描述 / 模型覆盖。 */
export interface SubagentPanelItem extends SubagentProgressItem {
  task_hint: string | null
  model_hint: string | null
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

/** 域任务事件：无 conversation_id，帧只承载状态语义，App 级任务 store 消费。 */
export interface WSDomainTaskUpdated {
  type: 'domain_task.updated'
  data: DomainTaskEventPayload
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
  | WSServerOutbox
  | WSServerFileChanges
  | WSServerTaskRuntime
  | WSServerTaskTimeline
  | WSServerRunSnapshot
  | WSServerRunKeepalive
  | WSServerToolClarifyRequest
  | WSServerPing
  | WSServerError
  | WSDomainTaskUpdated

/* ─── Auth ─── */

export interface AuthTokenResponse {
  token: string
  user_id: string
  nickname?: string | null
}

/* ─── Theme ─── */

export type Theme = 'dark' | 'light' | 'system'
