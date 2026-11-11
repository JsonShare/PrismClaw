import type { ToolCallInfo, SubagentProgressItem } from './chat'

/* ─── Execution Timeline（P1.2 / §5.3 wire 契约；冻结点：P1.5，字段形状不得变更） ─── */

export interface OperationDetail {
  callId: string
  /** 归属 iteration 轮次（segmentId）。用于前端按「思考 → 操作」逐轮时序展示 */
  segmentId: number
  status: 'running' | 'done' | 'error'
  startedAt: string
  endedAt: string | null
  error: string | null
  /** 真实工具名（Read/Bash/Write/Skill…） */
  toolName?: string | null
  /** 格式化参数文本（key: value 行，长文本后端已截断保护） */
  argsText?: string | null
  /** 工具结果文本（成功与失败均携带，后端已截断保护） */
  output?: string | null
}

/** 单个 iteration 轮：一段思考 + 该轮自然语言输出 + 该轮触发的扁平操作列表。
 *  UI 按 segmentId 升序渲染：每轮先 reasoning → 再 text → 再 operations。
 *  operations 是扁平 OperationDetail[]（按 startedAt 升序），不再有 OperationGroup
 *  折叠层——后端折叠后前端 buildStepRows 又拆回 OperationDetail 列表，
 *  形成「折叠 → 展开」的无效往返。扁平化后：单一排序基准、patch 协议简化。
 *  text 是该轮的自然语言输出（中间草稿/迭代过话语），与正文区（final_message
 *  只保留最后一轮最终答案）分离，完整迭代过程在执行时间线内展示。 */
export interface IterationUnit {
  segmentId: number
  reasoning: string
  /** 该轮自然语言输出（TextChunk 累积，不含工具参数/结果）。 */
  text?: string
  /** 扁平操作列表，按 startedAt 升序。 */
  operations: OperationDetail[]
}

export type TimelineNode =
  | { type: 'reasoning'; segmentId?: number; content: string }
  | {
      type: 'step'
      stepId: string | null
      index: number
      title: string
      status: 'completed' | 'current' | 'pending' | 'failed' | 'superseded'
      /** 步骤卡片的迭代时序单元（按 segmentId 升序，每轮先思考再操作） */
      iterations: IterationUnit[]
      revisionId: string | null
      /** 批判性评估摘要 */
      criticalAssessment?: string | null
      /** 风险点列表 */
      riskPoints?: string[]
      /** 未解决项列表 */
      unresolvedItems?: string[]
      /** 与预期的偏差 */
      deviationFromExpected?: string | null
    }
  | {
      type: 'orphan_operations'
      /** Orphan 卡片的迭代时序单元（按 segmentId 升序，每轮先思考再操作） */
      iterations: IterationUnit[]
    }

export interface HeartbeatPayload {
  state: 'generating' | 'executing' | 'compacting' | 'waiting_llm' | 'awaiting_clarification'
  partialToolName?: string | null
  charsSoFar?: number | null
  elapsedS: number
}

export type TimelinePatch =
  | { op: 'upsert_node'; node: TimelineNode }
  | { op: 'set_phase'; phase: HeartbeatPayload }
  /** 后端 IterationBoundary(llm_start) 时发：上一轮已完整推完，前端清空 streamingText */
  | { op: 'iteration_boundary'; segmentId: number }

export interface TaskTimelinePayload {
  conversationId: string
  eventSeq: number
  nodes?: TimelineNode[]
  messageId?: string | null
  patch?: TimelinePatch[]
}

export interface ProbeSignal {
  code: string
  severity: 'info' | 'warning' | 'high'
  summary: string
}

export interface PreflightProbeResult {
  probe_id: string
  workspace_root?: string | null
  mentioned_paths: string[]
  existing_paths: string[]
  recovery_targets: string[]
  touched_roots: string[]
  workspace_file_count: number
  workspace_dir_count: number
  code_file_count: number
  git_dirty_paths: string[]
  critical_workspace_paths: string[]
  baseline_risk: 'normal' | 'high'
  signals: ProbeSignal[]
  summary: string
}

export interface RecoveryProbeResult {
  probe_id: string
  workspace_root?: string | null
  baseline_source: 'preflight_probe' | 'none'
  status: 'ready' | 'degraded' | 'blocked'
  checked_paths: string[]
  missing_paths: string[]
  signals: ProbeSignal[]
  summary: string
}

export interface ExecutionEvidenceRecord {
  evidence_id: string
  task_id: string
  tool_name: string
  status: 'ok' | 'error'
  wrote_paths: string[]
  origin: 'main' | 'subagent' | 'system'
  created_at_epoch_ms: number
}

export interface AcceptanceEvaluationRecord {
  criterion: string
  status: 'met' | 'unmet' | 'unknown'
  evidence_ids: string[]
  note?: string | null
}

export interface ReviewDecisionRecord {
  decision_id: string
  task_id: string
  verdict: 'continue' | 'repair' | 'replan' | 'complete' | 'blocked'
  reason: string
  evidence_ids: string[]
  acceptance_evaluation: AcceptanceEvaluationRecord[]
  created_at_epoch_ms: number
}

export interface TaskRuntimeStateRecord {
  task_id: string
  session_id: string
  workspace_id?: string | null
  state:
    | 'intaking'
    | 'clarifying'
    | 'awaiting_confirmation'
    | 'awaiting_risk_confirmation'
    | 'executing'
    | 'reviewing'
    | 'resuming'
    | 'completed'
    | 'blocked'
    | 'cancelled'
  state_version: number
  latest_review_decision_id?: string | null
  risk_level?: string | null
  confirmation_required: boolean
  pending_confirmation_reason?: string | null
  checkpoint_cursor?: string | null
  last_mutation_id?: string | null
  created_at_epoch_ms: number
  updated_at_epoch_ms: number
}

export interface TaskRuntimeProjection {
  task: TaskRuntimeStateRecord | null
  phase?: HeartbeatPayload
  latest_review?: ReviewDecisionRecord | null
  recent_evidence: ExecutionEvidenceRecord[]
  preflight_probe?: PreflightProbeResult | null
  recovery_probe?: RecoveryProbeResult | null
  loading_active: boolean
  tool_calls: ToolCallInfo[]
  subagent_trace: SubagentProgressItem[]
  approval_pending_tool_call_ids: string[]
}

/**
 * 后端「进行中 run」运行态快照的 wire 契约（snake_case）。
 * GET /api/conversations/{id}/run 从后端内存投影实时构建（内存权威源）；
 * 前端 hydrateRun 据此全量重建 ConvState，取代旧 restoreRunning 的增量重挂。
 */
export interface RunProjection {
  conversation_id: string
  /** 恒 "active"：非运行中会话 GET /run 直接返回 null（不进入本类型） */
  state: string
  streaming_text: string
  timeline_nodes: TimelineNode[]
  task_runtime: TaskRuntimeProjection | null
  phase: string | null
  /** phase 开始的 Unix epoch ms */
  phase_at: number | null
  /** AskUserQuestion 待回答澄清态。非空表示 run 正因提问暂停——
   *  刷新/重连后前端据此重建提问卡片（approvalId/toolCallId 为提交凭据，
   *  用户作答前 run 永不推进）；已作答/作废时为 null。 */
  clarification: {
    approval_id: string
    tool_call_id: string
    tool_name: string
    questions: Array<Record<string, unknown>>
    created_at_ms: number
  } | null
  updated_at: number
}

/* ─── Trajectory Audit (Phase 2) ─── */

export type TrajectoryStatus = 'running' | 'ok' | 'error' | 'aborted' | 'timeout' | 'orphaned'

/** 单次 LLM 请求的四层消息并排快照 (projector / assemble / pipeline / packer). */
export interface RequestAuditRecord {
  schema: number
  session_id: string
  iteration: number
  turn: number | null
  started_at_ms: number
  completed_at_ms: number | null
  status: TrajectoryStatus

  // layer 0.5: projector input (tree.order)
  projector_input_order_count: number
  projector_input_entry_types: string[]
  projector_input_head_seq: number[]
  projector_input_tail_seq: number[]
  projector_input_tail_types: string[]

  // layer ①: projector output
  projector_msg_count: number
  projector_appended_msg_count: number | null
  projector_msg_roles: string[]
  projector_head: string[]
  projector_tail: string[]
  // DSH parity: actual content previews (not just 80-char snippets)
  projector_user_message_preview: string | null
  projector_tool_results_preview: string[]

  // layer ②: assemble entry
  assemble_entry_msg_count: number
  assemble_entry_roles: string[]
  assemble_entry_head: string[]
  assemble_entry_tail: string[]
  assemble_entry_meta_keys: string[]
  assemble_entry_meta_values_hash: string

  // layer ③: pipeline out
  pipeline_out_msg_count: number
  pipeline_out_roles: string[]
  pipeline_out_head: string[]
  pipeline_out_tail: string[]
  pipeline_out_budget_summary: Record<string, number>
  pipeline_out_compressors_used: string[]
  pipeline_out_compaction_happened: boolean
  // DSH parity: compaction content preview (up to 500 chars)
  pipeline_out_compaction_summary: string | null

  // layer ④: packer final
  packer_final_msg_count: number
  packer_final_roles: string[]
  packer_final_head: string[]
  packer_final_tail: string[]
  packer_final_tokens: number | null
  packer_final_model: string
  packer_final_system_prompt_len: number
  packer_final_system_prompt_sha1_16: string
  packer_final_system_prompt_text: string | null
  packer_final_system_prompt_budget: number | null
  packer_final_tool_schema_total_chars: number | null
  packer_final_response_format_text: string | null
  // DSH parity: actual tool schema JSON text (up to 1000 chars)
  packer_final_tool_schema_text: string | null

  // LLM response side
  llm_provider: string | null
  llm_model: string | null
  finish_reason: string | null
  usage_input_tokens: number | null
  usage_output_tokens: number | null
  usage_cache_read_tokens: number | null
  usage_cache_write_tokens: number | null
  usage_reasoning_tokens: number | null
  ttft_ms: number | null
  total_ms: number | null
  // DSH parity: actual LLM response content previews
  assistant_text_preview: string | null
  tool_calls_summary: string[]

  // error
  error: string | null
  error_stack: string | null
}

export interface TrajectorySnapshotMeta {
  record_count: number
  turn_count_estimate: number
  gap_count: number
  gap_ratio: number
  orphan_count: number
  gap_warning: boolean
  zstd_compressed_record_count: number
}

export interface TrajectorySnapshot {
  session_id: string
  records: RequestAuditRecord[]
  meta: TrajectorySnapshotMeta
}

// ── DSH-aligned per-conversation-event cell model ─────────────────
// One cell = one conversation-tree node.  This is the DSH trajectory data
// model — timeline + ledger render cells, NOT iterations.  Iterations stay
// as a separate "LLM call audit" panel for debugging the 4-layer pipeline.
export type TrajectoryCellKind =
  | 'system'    // system prompt          (lane 0 / Input)
  | 'user'      // user message           (lane 0 / Input)
  | 'context'   // context injection      (lane 0 / Input)
  | 'compacted' // compaction result     (lane 1 / Model)
  | 'message'   // assistant response    (lane 1 / Model)
  | 'tool'      // tool call / result     (lane 2 / Tools)
  | 'subtool'   // nested sub-call        (lane 2 / Tools)

export interface TrajectoryCell {
  cell_id: string
  session_id: string
  entry_id: string
  seq: number              // position in tree.order (1-based)
  turn: number             // 1-based user-message counter
  kind: TrajectoryCellKind
  role: string             // raw entry.role
  content_preview: string | null
  tool_calls_summary: string[]
  tool_call_id: string | null
  tool_name: string | null     // tool/subtool cell → tool name (backend field)
  started_at_ms: number
  completed_at_ms: number | null
  iteration: number | null  // assistant cell → LLM iteration
  error: string | null

  // DSH parity: full-content + metric fields (DSH TrajectoryCellProps).
  // Populated by backend bootstrap._append (input/output/thinking detail +
  // is_error + schema_detail) and enriched for assistant cells via
  // LLMResponseReceived → update_cell_metrics_pg (model / finish_reason /
  // usage_* / ttft_ms / decoding_ms / output_detail / completed_at_ms).
  input_detail: string | null         // full input content (user msg / tool args)
  output_detail: string | null        // full output content (assistant response / tool result)
  thinking_detail: string | null      // reasoning text (assistant thinking / compaction)
  model: string | null                // LLM model name (assistant only)
  finish_reason: string | null         // LLM finish reason (assistant only)
  usage_input_tokens: number | null
  usage_output_tokens: number | null
  usage_cache_read_tokens: number | null
  usage_cache_write_tokens: number | null
  usage_reasoning_tokens: number | null
  ttft_ms: number | null              // time to first token (assistant only)
  decoding_ms: number | null          // decoding time = total - ttft (assistant only)
  is_error: boolean                   // tool result failure flag
  schema_detail: string | null        // tool schema JSON (tool only)
}

export interface TrajectoryCellSnapshot {
  session_id: string
  cells: TrajectoryCell[]
  /** Highest seq among the returned cells — the incremental poll cursor. */
  latest_seq?: number
}

/* ─── Trajectory Redesign Helpers ─── */

export type TrajectoryInspectorTab = 'summary' | 'tokens' | 'timing' | 'layers' | 'system'

export interface TrajectoryTimelineSpan {
  index: number
  leftPct: number
  widthPct: number
  lane: 'request' | 'duration'
  isError: boolean
  status: TrajectoryStatus
}

export interface TrajectoryTurnGroup {
  turn: number | null
  records: RequestAuditRecord[]
}
