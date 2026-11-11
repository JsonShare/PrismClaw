/**
 * timeline.ts — Operation-sequence and recorded-time projections for the
 * trajectory overview.
 *
 * Ported from DSH:
 *   packages/client/ui-trajectory/src/client/timeline.ts (200 lines)
 *
 * Adaptations to PrismClaw's model (documented per function):
 *   - Input is a FLAT TrajectoryCell[] (grouped by turn internally) instead
 *     of DSH's turns[].groups[].cells nested layout.
 *   - `cellRange` reads the stored endpoints directly
 *     (started_at_ms / completed_at_ms) instead of DSH's
 *     startedAt + timeSeconds*1000 computation.
 *   - `requestOnly` filter branch deleted (no such concept here).
 *   - span.index = position in the flat cells array (caller indexes back
 *     into cells[span.index] for rendering).
 */

import type { TrajectoryCell, TrajectoryCellKind } from '../../../types'

/** Horizontal projection used by the trajectory timeline. */
export type TrajectoryTimelineMode = 'sequence' | 'duration' | 'time' | 'actual'

/** Inclusive selection in the active timeline projection's domain. */
export interface TrajectoryTimeRange {
  start: number
  end: number
}

/** One ledger record projected into the active timeline domain. */
export interface TrajectoryTimelineSpan extends TrajectoryTimeRange {
  index: number
  isError: boolean
  kind: TrajectoryCellKind
  label: string
  lane: number
}

/** One turn boundary in the active timeline domain. */
export interface TrajectoryTimelineTurnBoundary {
  turn: number | null
  time: number
}

/** Full-domain model used by the overview. */
export interface TrajectoryTimelineModel extends TrajectoryTimeRange {
  spans: readonly TrajectoryTimelineSpan[]
  turnBoundaries: readonly TrajectoryTimelineTurnBoundary[]
}

/** Format a timeline duration as an integer-millisecond label.
 * Ported from DSH trajectory-record.ts formatDurationMillis. */
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
  // Adapted: we persist both endpoints; clamp inverted data to zero width.
  const end = cell.completed_at_ms != null && Number.isFinite(cell.completed_at_ms)
    ? Math.max(cell.started_at_ms, cell.completed_at_ms)
    : cell.started_at_ms
  return { start: cell.started_at_ms, end }
}

/** Flat cells grouped into consecutive runs of the same turn. */
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
  // ≥ DSH: DSH checks isError only; we also treat a populated error
  // message as an error state (server-side error marking sets both).
  return cell.is_error === true || cell.error != null
}

/**
 * Project every visible record into a stable three-lane timeline.
 * @param cells - Unfiltered flat trajectory cells.
 * @param mode - Independent equal/recorded duration and compressed/complete time projection.
 * @returns Timeline model, or `null` when no record is visible.
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

  // Idle compression: scan spans in start order and accumulate the removed
  // idle gap per span (duration mode folds inter-span whitespace).
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
        // time mode (equal-duration): spans collapse to zero width at their
        // start instant — the renderer gives them a fixed pixel width.
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
 * Identify records active at any point inside an inclusive selected interval.
 * @param cells - Unfiltered flat trajectory cells.
 * @param range - Selected interval in the active projection.
 * @param mode - Independent equal/recorded duration and compressed/complete time projection.
 * @returns Record indexes inside the focus interval.
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
