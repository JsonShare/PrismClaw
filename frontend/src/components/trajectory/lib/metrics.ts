/**
 * metrics.ts — Per-turn aggregate statistics for the ledger Turn header.
 *
 * Ported from DSH layout.ts groupDescription, extended for PrismClaw's
 * richer per-cell metrics (tokens / cache / reasoning — PrismClaw ≥ DSH):
 *   - Wall-span duration: max(completed) − min(started), i.e. the turn's
 *     wall clock — parallel tool calls contribute their span, not their
 *     summed durations (DSH groupDescription semantics).
 *   - Tool histogram: `bash×6`-style counts of top-level tool calls
 *     (kind === 'tool'; subtool calls stay nested under their parent).
 */

import type { TrajectoryCell } from '../../../types'

export interface TrajectoryTurnMetrics {
  /** Turn wall clock (max completed − min started), null without any span. */
  readonly wallMs: number | null
  /** Σ own cell durations (Σ completed − started). */
  readonly sumMs: number
  readonly inputTokens: number
  readonly outputTokens: number
  readonly cacheReadTokens: number
  readonly cacheWriteTokens: number
  readonly reasoningTokens: number
  /** Top-level tool call counts by name, most-frequent first. */
  readonly toolHistogram: readonly { readonly name: string; readonly count: number }[]
  readonly errorCount: number
}

/** Tool display name — backend tool_name first, tool_calls_summary fallback. */
function toolNameOf(cell: TrajectoryCell): string {
  if (cell.tool_name) return cell.tool_name
  const summary = cell.tool_calls_summary[0]
  if (summary === undefined) return ''
  const match = summary.match(/^([^(]*)\(/)
  return match !== null ? match[1] ?? '' : summary
}

/**
 * Aggregate one turn's cells into header metrics.
 * @param cells - The turn's cells (any order).
 * @returns Aggregate statistics for the sticky Turn header.
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
 * Format the DSH-style tool histogram suffix (`bash×6 ls grep`).
 * @param histogram - Aggregated tool call counts.
 * @returns Compact histogram label, or `''` with no tool calls.
 */
export function formatToolHistogram(
  histogram: readonly { readonly name: string; readonly count: number }[],
): string {
  return histogram
    .map(({ name, count }) => (count > 1 ? `${name}×${count}` : name))
    .join(' ')
}
