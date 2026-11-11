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
