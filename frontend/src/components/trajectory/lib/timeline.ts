/**
 * timeline.ts — 执行序列与记录时间投影，供轨迹概览使用。
 *
 * 适配 PrismClaw 数据模型：
 *   - 输入是扁平 TrajectoryCell[]（内部按 turn 分组）。
 *   - cell 直接携带 started_at_ms / completed_at_ms 端点，无需二次计算。
 *   - 没有 requestOnly 过滤分支。
 *   - span.index 是扁平 cells 数组中的位置，调用方用它反向索引 cells[]。
 */

import type { TrajectoryCell, TrajectoryCellKind } from '../../../types'

/** 时间线的水平投影模式。 */
export type TrajectoryTimelineMode = 'sequence' | 'duration' | 'time' | 'actual'

/** 当前投影域内的闭区间选区。 */
export interface TrajectoryTimeRange {
  start: number
  end: number
}

/** ledger 记录在当前投影域的一条 span。 */
export interface TrajectoryTimelineSpan extends TrajectoryTimeRange {
  index: number
  isError: boolean
  kind: TrajectoryCellKind
  label: string
  lane: number
}

/** 当前投影域内的一个 turn 边界。 */
export interface TrajectoryTimelineTurnBoundary {
  turn: number | null
  time: number
}

/** 概览所需的完整域模型。 */
export interface TrajectoryTimelineModel extends TrajectoryTimeRange {
  spans: readonly TrajectoryTimelineSpan[]
  turnBoundaries: readonly TrajectoryTimelineTurnBoundary[]
}

/** 将 timeline duration 格式化为整数毫秒标签。 */
export function formatDurationMillis(milliseconds: number): string {
  const integer = String(Math.round(milliseconds))
  return `${integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',')} ms`
}


function laneFor(kind: TrajectoryCellKind): number {
  if (kind === 'tool' || kind === 'subtool') return 2
  if (kind === 'message' || kind === 'compacted') return 1
  return 0
}

function cellRange(cell: TrajectoryCell): TrajectoryTimeRange | null {
  if (!Number.isFinite(cell.started_at_ms)) return null
  // 直接读取持久化的端点；倒置数据钳制为零宽度
  const end = cell.completed_at_ms != null && Number.isFinite(cell.completed_at_ms)
    ? Math.max(cell.started_at_ms, cell.completed_at_ms)
    : cell.started_at_ms
  return { start: cell.started_at_ms, end }
}

/** 扁平 cells 按 turn 分组成连续块。 */
interface IndexedCell {
  cell: TrajectoryCell
  index: number
}

interface TurnGroup {
  turn: number | null
  items: IndexedCell[]
}

function groupByTurn(cells: readonly TrajectoryCell[]): TurnGroup[] {
  const groups: TurnGroup[] = []
  for (let i = 0; i < cells.length; i++) {
    const cell = cells[i]
    const turn = cell.turn ?? null
    const last = groups.at(-1)
    if (last !== undefined && last.turn === turn) {
      last.items.push({ cell, index: i })
    } else {
      groups.push({ turn, items: [{ cell, index: i }] })
    }
  }
  return groups
}

function isErrorOf(cell: TrajectoryCell): boolean {
  // 除了 is_error 标记外，也把非空 error 消息视为错误态（服务端标记两者）
  return cell.is_error === true || cell.error != null
}

/**
 * 将每条可见记录投影到稳定的 3 轨道时间线上。
 * @param cells — 未过滤的扁平轨迹 cells
 * @param mode — 等宽/记录时长与压缩/完整时间投影的独立选择
 * @returns 时间线模型，无可见记录时返回 null
 */
export function deriveTrajectoryTimeline(
  cells: readonly TrajectoryCell[],
  mode: TrajectoryTimelineMode = 'sequence',
): TrajectoryTimelineModel | null {
  if (mode !== 'sequence') {
    return deriveTimedTimeline(
      cells,
      mode === 'duration' || mode === 'actual',
      mode === 'duration',
    )
  }
  const spans: TrajectoryTimelineSpan[] = []
  const turnBoundaries: TrajectoryTimelineTurnBoundary[] = []

  for (const group of groupByTurn(cells)) {
    if (group.items.length === 0) continue
    if (group.turn !== null) {
      turnBoundaries.push({
        turn: group.turn,
        time: spans.length,
      })
    }
    spans.push(...group.items.map((item, offset): TrajectoryTimelineSpan => ({
      start: spans.length + offset,
      end: spans.length + offset + 1,
      index: item.index,
      isError: isErrorOf(item.cell),
      kind: item.cell.kind,
      label: item.cell.content_preview ?? '',
      lane: laneFor(item.cell.kind),
    })))
  }

  if (spans.length === 0) return null
  return {
    start: 0,
    end: spans.length,
    spans,
    turnBoundaries,
  }
}

function deriveTimedTimeline(
  cells: readonly TrajectoryCell[],
  actualDuration: boolean,
  compressIdle: boolean,
): TrajectoryTimelineModel | null {
  const timedTurns = groupByTurn(cells).flatMap((group) => {
    const rawSpans = group.items.flatMap((item): TrajectoryTimelineSpan[] => {
      const range = cellRange(item.cell)
      return range === null
        ? []
        : [{
          ...range,
          index: item.index,
          isError: isErrorOf(item.cell),
          kind: item.cell.kind,
          label: item.cell.content_preview ?? '',
          lane: laneFor(item.cell.kind),
        }]
    })
    return rawSpans.length === 0 ? [] : [{ turn: group.turn, rawSpans }]
  })
  const rawSpans = timedTurns.flatMap(turn => turn.rawSpans)
  if (rawSpans.length === 0) return null

  // 空闲压缩：按 start 排序扫描 spans，累积每个 span 前被消除的空闲间隙
  //（duration 模式折叠 span 之间的空白）
  const removedIdleBySpan = new Map<TrajectoryTimelineSpan, number>()
  let removedIdle = 0
  let coveredUntil: number | null = null
  for (const span of [...rawSpans].sort((left, right) =>
    left.start - right.start || left.end - right.end)) {
    if (compressIdle && coveredUntil !== null && span.start > coveredUntil) {
      removedIdle += span.start - coveredUntil
    }
    removedIdleBySpan.set(span, removedIdle)
    coveredUntil = coveredUntil === null ? span.end : Math.max(coveredUntil, span.end)
  }

  const spans: TrajectoryTimelineSpan[] = []
  const turnBoundaries: TrajectoryTimelineTurnBoundary[] = []
  for (const turn of timedTurns) {
    const projected = turn.rawSpans.map((span): TrajectoryTimelineSpan => {
      const offset = removedIdleBySpan.get(span) ?? 0
      return {
        ...span,
        start: span.start - offset,
        // time 模式（等时长）：spans 在起始瞬时坍缩为零宽度，渲染器给固定像素宽
        end: (actualDuration ? span.end : span.start) - offset,
      }
    })
    spans.push(...projected)
    if (turn.turn !== null) {
      turnBoundaries.push({
        turn: turn.turn,
        time: Math.min(...projected.map(span => span.start)),
      })
    }
  }

  return {
    start: Math.min(...spans.map(span => span.start)),
    end: Math.max(...spans.map(span => span.end)),
    spans,
    turnBoundaries,
  }
}

/**
 * 找出活跃于闭区间选区内的记录索引。
 * @param cells — 未过滤的扁平轨迹 cells
 * @param range — 当前投影域内的选区区间
 * @param mode — 等宽/记录时长与压缩/完整时间投影的独立选择
 * @returns 选区范围内的记录索引集合
 */
export function trajectoryTimelineFocusIndexes(
  cells: readonly TrajectoryCell[],
  range: TrajectoryTimeRange,
  mode: TrajectoryTimelineMode = 'sequence',
): ReadonlySet<number> {
  const model = deriveTrajectoryTimeline(cells, mode)
  return new Set(
    model?.spans
      .filter(span => span.start <= range.end && span.end >= range.start)
      .map(span => span.index),
  )
}
