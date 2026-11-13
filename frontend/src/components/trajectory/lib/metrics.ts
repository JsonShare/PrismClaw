/**
 * metrics.ts — 每个 turn 的聚合统计，供 ledger Turn 标题使用。
 *
 * Wall-span duration: max(completed) − min(started)，即 turn 的墙钟时间
 * — 并行工具调用贡献的是它们的 span，而非各自时长之和。
 * Tool histogram: `bash×6` 风格的顶层工具调用计数
 * （kind === 'tool'；subtool 调用嵌套在父节点下）。
 */

import type { TrajectoryCell } from '../../../types'

export interface TrajectoryTurnMetrics {
  /** Turn 墙钟（max completed − min started）；无 span 时为 null。 */
  readonly wallMs: number | null
  /** Σ 自身 cell 时长（Σ completed − started）。 */
  readonly sumMs: number
  readonly inputTokens: number
  readonly outputTokens: number
  readonly cacheReadTokens: number
  readonly cacheWriteTokens: number
  readonly reasoningTokens: number
  /** 顶层工具调用按名称计数，按频次倒序。 */
  readonly toolHistogram: readonly { readonly name: string; readonly count: number }[]
  readonly errorCount: number
}

/** 工具显示名 — 优先 backend tool_name，否则从 tool_calls_summary 提取。 */
function toolNameOf(cell: TrajectoryCell): string {
  if (cell.tool_name) return cell.tool_name
  const summary = cell.tool_calls_summary[0]
  if (summary === undefined) return ''
  const match = summary.match(/^([^(]*)\(/)
  return match !== null ? match[1] ?? '' : summary
}

/**
 * 聚合一个 turn 的 cells 为 header 统计数据。
 * @param cells — 该 turn 的 cells（任意顺序）
 * @returns 吸附式 Turn header 所需的聚合统计
 */
export function trajectoryTurnMetrics(cells: readonly TrajectoryCell[]): TrajectoryTurnMetrics {
  let minStart: number | null = null
  let maxEnd: number | null = null
  let sumMs = 0
  let inputTokens = 0
  let outputTokens = 0
  let cacheReadTokens = 0
  let cacheWriteTokens = 0
  let reasoningTokens = 0
  let errorCount = 0
  const tools = new Map<string, number>()

  for (const cell of cells) {
    const start = cell.started_at_ms
    const end = cell.completed_at_ms
    if (Number.isFinite(start)) {
      minStart = minStart === null ? start : Math.min(minStart, start)
    }
    if (end !== null && Number.isFinite(end)) {
      maxEnd = maxEnd === null ? end : Math.max(maxEnd, end)
      if (Number.isFinite(start)) sumMs += Math.max(0, end - start)
    }
    if (cell.usage_input_tokens != null) inputTokens += cell.usage_input_tokens
    if (cell.usage_output_tokens != null) outputTokens += cell.usage_output_tokens
    if (cell.usage_cache_read_tokens != null) cacheReadTokens += cell.usage_cache_read_tokens
    if (cell.usage_cache_write_tokens != null) cacheWriteTokens += cell.usage_cache_write_tokens
    if (cell.usage_reasoning_tokens != null) reasoningTokens += cell.usage_reasoning_tokens
    if (cell.is_error || cell.error != null) errorCount += 1
    if (cell.kind === 'tool') {
      const name = toolNameOf(cell)
      if (name !== '') tools.set(name, (tools.get(name) ?? 0) + 1)
    }
  }

  const toolHistogram = [...tools.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((left, right) => right.count - left.count || left.name.localeCompare(right.name))

  return {
    wallMs: minStart !== null && maxEnd !== null ? Math.max(0, maxEnd - minStart) : null,
    sumMs,
    inputTokens,
    outputTokens,
    cacheReadTokens,
    cacheWriteTokens,
    reasoningTokens,
    toolHistogram,
    errorCount,
  }
}

/**
 * 格式化工具直方图后缀（`bash×6 ls grep`）。
 * @param histogram — 聚合后的工具调用计数
 * @returns 紧凑的直方图标签；无工具调用时返回空串
 */
export function formatToolHistogram(
  histogram: readonly { readonly name: string; readonly count: number }[],
): string {
  return histogram
    .map(({ name, count }) => (count > 1 ? `${name}×${count}` : name))
    .join(' ')
}
