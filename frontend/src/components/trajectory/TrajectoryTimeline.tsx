/**
 * TrajectoryTimeline — DSH-aligned 3-lane overview with drag-to-select.
 *
 * Projection logic lives in ./trajectory/timeline.ts (ported from DSH
 * timeline.ts): 4 modes — sequence (equal-width), duration (idle-compressed
 * real time), time (equal-duration instants), actual (full real time).
 *
 *   • 50px plot · 44px label column · 3 lanes × 14px · 8px bar (DSH dims)
 *   • kind-driven solid colour per cell (data-timeline-span='kind')
 *   • assistant bar linear-gradient TTFT (pale) → Decoding (deep/red)
 *     (data-assistant-timing='true' + --trajectory-assistant-ttft)
 *   • error cells override to red (data-error='true')
 *   • turn boundaries as 1px vertical lines, recomputed per mode
 *   • DSH-style rich tooltip: kind label + time range + Total + TTFT · Decoding
 *
 * Interaction (ported from DSH):
 *   • drag-to-select range (pointer down/move/up) → onRangeChange
 *   • selection dimming (data-selected='false' → opacity 0.2)
 *   • search match dimming (data-search-match='false' → opacity 0.14)
 *   • hover line (vertical cursor line on whitespace)
 *   • click span → onRecordSelect(index); click whitespace → onRecordFocus(nearest)
 *   • Escape / double-click clears range
 *
 * Data: TrajectoryCell[] (one cell per conversation-tree node).
 */
import { useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type KeyboardEvent } from 'react'
import type { TrajectoryCell, TrajectoryCellKind } from '../../types'
import {
  deriveTrajectoryTimeline,
  formatDurationMillis,
  type TrajectoryTimelineMode,
} from './lib/timeline'

interface TrajectoryTimelineProps {
  cells: TrajectoryCell[]
  onSpanClick: (cellIndex: number) => void
  selectedIndex: number | null
  /** Active selection range in the current mode's domain; null when none. */
  range: { start: number; end: number } | null
  onRangeChange: (range: { start: number; end: number } | null) => void
  /** Cell indexes matching the active ledger search; null without a query. */
  searchMatchIndexes: ReadonlySet<number> | null
  /** Timeline projection mode (DSH TrajectoryTimelineMode). */
  mode: TrajectoryTimelineMode
}

// ── DSH 7-kind model ────────────────────────────────────────────
const KIND_LABEL: Record<TrajectoryCellKind, string> = {
  system: 'SYSTEM', user: 'USER', context: 'CONTEXT',
  compacted: 'COMPACTED', message: 'ASSISTANT',
  tool: 'TOOL', subtool: 'SUBTOOL',
}

const KIND_COLOR: Record<TrajectoryCellKind, string> = {
  system:    'hsl(210, 8%, 55%)',
  user:      'hsl(210, 70%, 50%)',
  context:   'hsl(165, 50%, 42%)',
  compacted: 'hsl(280, 30%, 50%)',
  message:   'hsl(15, 75%, 55%)',
  tool:      'hsl(38, 80%, 50%)',
  subtool:   'hsl(38, 65%, 60%)',
}

// Assistant timing gradient colours (DSH color-mix approximation).
const ASSISTANT_DECODING_COLOR = 'hsl(15, 82%, 48%)'
const ASSISTANT_TTFT_COLOR = 'hsl(15, 48%, 72%)'

// ── Layout constants — 1:1 with DSH ────────────────────────────
const PLOT_HEIGHT = 50
const LABEL_WIDTH = 44
const LANE_TOP = 7
const LANE_HEIGHT = 14
const BAR_HEIGHT = 8
const BAR_GAP_Y = (LANE_HEIGHT - BAR_HEIGHT) / 2

// DSH interaction thresholds
const MINIMUM_DRAG_PX = 3

// Equal-duration bars in 'time' mode get a fixed pixel width (DSH
// [data-equal-duration='true'] → width: 8px).
const EQUAL_DURATION_BAR_PX = 8

function formatMs(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.round(ms / 1000)}s`
}

function formatRecordedTime(timestamp: number): string {
  return new Date(timestamp).toLocaleTimeString(undefined, {
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    fractionalSecondDigits: 3,
  })
}

interface TimelineRecordDetail {
  decodingMs?: number
  durationMs?: number
  startedAt?: number
  ttftMs?: number
}

function timelineRecordDetail(cell: TrajectoryCell): TimelineRecordDetail {
  const durationMs = cell.completed_at_ms != null
    ? Math.max(0, cell.completed_at_ms - cell.started_at_ms)
    : undefined
  const startedAt = Number.isFinite(cell.started_at_ms) ? cell.started_at_ms : undefined
  const detail: TimelineRecordDetail = {}
  if (durationMs !== undefined) detail.durationMs = durationMs
  if (startedAt !== undefined) detail.startedAt = startedAt
  if (cell.ttft_ms != null && Number.isFinite(cell.ttft_ms)) detail.ttftMs = cell.ttft_ms
  if (cell.decoding_ms != null && Number.isFinite(cell.decoding_ms)) detail.decodingMs = cell.decoding_ms
  return detail
}

function timelineTooltipLabel(
  kind: TrajectoryCellKind,
  detail: TimelineRecordDetail | undefined,
): string {
  const heading = KIND_LABEL[kind]
  if (detail === undefined) return heading
  const duration = detail.durationMs === undefined
    ? null
    : `Total ${formatDurationMillis(detail.durationMs)}`
  const range = detail.startedAt === undefined
    ? null
    : detail.durationMs === undefined
      ? `Started ${formatRecordedTime(detail.startedAt)}`
      : `${formatRecordedTime(detail.startedAt)} → ${formatRecordedTime(detail.startedAt + detail.durationMs)}`
  const segments = detail.ttftMs === undefined || detail.decodingMs === undefined
    ? null
    : `TTFT ${formatDurationMillis(detail.ttftMs)} · Decoding ${formatDurationMillis(detail.decodingMs)}`
  const timing = [duration, segments].filter(v => v !== null).join(' · ')
  return [heading, range, timing].filter(v => v !== null && v !== '').join('\n')
}

interface TimelineSpan {
  index: number
  start: number   // domain units (sequence = index, timed = ms)
  end: number
  cell: TrajectoryCell
  lane: number
  isError: boolean
}

interface TimelineModel {
  start: number
  end: number
  spans: TimelineSpan[]
  turnBoundaries: { turn: number | null; time: number }[]
}

/** Wrap the ported DSH projection, re-attaching the source cell per span
 * (the module keeps spans minimal; the renderer needs the cell for colour /
 * TTFT gradient / tooltip). */
function deriveTimelineModel(
  cells: TrajectoryCell[],
  mode: TrajectoryTimelineMode,
): TimelineModel | null {
  const model = deriveTrajectoryTimeline(cells, mode)
  if (model == null) return null
  return {
    start: model.start,
    end: model.end,
    spans: model.spans.map(span => ({
      ...span,
      cell: cells[span.index],
    })),
    turnBoundaries: [...model.turnBoundaries],
  }
}

function orderedRange(a: number, b: number) {
  return a <= b ? { start: a, end: b } : { start: b, end: a }
}
function clampFraction(v: number) { return Math.min(1, Math.max(0, v)) }

interface DragGesture {
  pointerId: number
  anchorTime: number
  anchorClientX: number
  recordIndex: number | null
}

// ── Public component ─────────────────────────────────────────────
export function TrajectoryTimeline({
  cells, onSpanClick, selectedIndex, range, onRangeChange, searchMatchIndexes, mode,
}: TrajectoryTimelineProps) {

  const model = useMemo(() => deriveTimelineModel(cells, mode), [cells, mode])
  const detailByIndex = useMemo(
    () => new Map(cells.map((cell, i) => [i, timelineRecordDetail(cell)] as const)),
    [cells],
  )

  const trackRef = useRef<HTMLDivElement | null>(null)
  const dragRef = useRef<DragGesture | null>(null)
  const [draft, setDraft] = useState<{ start: number; end: number } | null>(null)
  const [hover, setHover] = useState<{ fraction: number; recordIndex: number | null } | null>(null)

  if (model == null) {
    return (
      <div className="px-3 py-2 border-b border-[var(--c-border)] bg-[var(--c-surface)]">
        <div className="text-[length:var(--fs-sm)] font-semibold text-[var(--c-text)] mb-2">
          执行时间线
        </div>
        <div className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)] text-center py-4">
          暂无时间线数据
        </div>
      </div>
    )
  }

  const domainStart = model.start
  const domainDuration = Math.max(1, model.end - model.start)

  const percent = (msFromStart: number) =>
    Math.min(100, Math.max(0, (msFromStart / domainDuration) * 100))

  const activeRange = draft ?? range

  const fractionAt = (event: ReactPointerEvent<HTMLDivElement>): number => {
    const rect = event.currentTarget.getBoundingClientRect()
    return clampFraction((event.clientX - rect.left) / Math.max(1, rect.width))
  }

  const recordIndexAt = (event: ReactPointerEvent<HTMLDivElement>): number | null => {
    const target = event.target instanceof HTMLElement ? event.target : null
    const value = target?.closest<HTMLElement>('[data-timeline-record-index]')
      ?.dataset.timelineRecordIndex
    if (value === undefined) return null
    const index = Number(value)
    return Number.isFinite(index) ? index : null
  }

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return
    const anchor = fractionAt(event)
    const anchorTime = domainStart + anchor * domainDuration
    const recordIndex = recordIndexAt(event)
    setHover({ fraction: anchor, recordIndex })
    dragRef.current = { pointerId: event.pointerId, anchorTime, anchorClientX: event.clientX, recordIndex }
    if (typeof event.currentTarget.setPointerCapture === 'function') {
      event.currentTarget.setPointerCapture(event.pointerId)
    }
    setDraft({ start: anchorTime, end: anchorTime })
  }

  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const fraction = fractionAt(event)
    setHover({ fraction, recordIndex: recordIndexAt(event) })
    const drag = dragRef.current
    if (drag === null || drag.pointerId !== event.pointerId) return
    const pointTime = domainStart + fraction * domainDuration
    setDraft(orderedRange(drag.anchorTime, pointTime))
  }

  const onPointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (drag === null || drag.pointerId !== event.pointerId) return
    const fraction = fractionAt(event)
    const pointTime = domainStart + fraction * domainDuration
    const selected = orderedRange(drag.anchorTime, pointTime)
    setHover({ fraction, recordIndex: recordIndexAt(event) })
    dragRef.current = null
    setDraft(null)
    const click = Math.abs(event.clientX - drag.anchorClientX) < MINIMUM_DRAG_PX
    // Click on a span → select that record
    if (click && drag.recordIndex !== null) {
      onRangeChange(null)
      onSpanClick(drag.recordIndex)
      return
    }
    // Drag too small → centered minimum selection (DSH minimumSelectionDuration
    // = min(domainDuration, fullDuration / spans.length) — one average unit)
    const minimumSelectionDuration = Math.min(domainDuration, domainDuration / model.spans.length)
    let committedRange = selected
    if (selected.end - selected.start < minimumSelectionDuration) {
      const center = (selected.start + selected.end) / 2
      let start = center - minimumSelectionDuration / 2
      if (start < model.start) start = model.start
      if (start > model.end - minimumSelectionDuration) start = model.end - minimumSelectionDuration
      committedRange = { start, end: start + minimumSelectionDuration }
    }
    onRangeChange(committedRange)
    // Click on whitespace → focus nearest span (DSH onRecordFocus)
    if (click) {
      const point = selected.start
      let nearest = model.spans[0]
      let nearestDist = Infinity
      for (const span of model.spans) {
        const dist = point < span.start ? span.start - point
          : point > span.end ? point - span.end : 0
        if (dist < nearestDist) { nearestDist = dist; nearest = span }
      }
      if (nearest) onSpanClick(nearest.index)
    }
  }

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Escape' || range === null) return
    event.preventDefault()
    onRangeChange(null)
  }

  const onPointerCancel = () => {
    dragRef.current = null
    setDraft(null)
    setHover(null)
  }

  // Selection fraction (committed or draft)
  const visibleRange = draft ?? range
  const selLeft = visibleRange != null ? percent(visibleRange.start - domainStart) : null
  const selWidth = visibleRange != null ? percent(visibleRange.end - visibleRange.start) : null

  // Domain summary varies by mode: sequence counts records; timed modes
  // report (possibly compressed) span.
  const modeLabel = mode === 'sequence' ? '顺序'
    : mode === 'duration' ? '时长·压缩空闲'
    : mode === 'time' ? '时刻·等宽' : '实时'
  const domainSummary = mode === 'sequence'
    ? `${cells.length} 条记录`
    : `跨度 ${formatMs(domainDuration)}${mode === 'duration' ? '（空闲已压缩）' : ''}`

  return (
    <div className="px-3 py-2 border-b border-[var(--c-border)] bg-[var(--c-surface)]">
      {/* Header */}
      <div className="flex items-baseline gap-1.5 mb-2 flex-wrap">
        <span className="text-[length:var(--fs-sm)] font-semibold text-[var(--c-text)]">
          执行时间线
        </span>
        <span className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]/70">
          · {modeLabel} · {domainSummary}
          {activeRange != null && <span className="ml-1 text-[var(--c-accent)]">· 已选区</span>}
        </span>
      </div>

      {/* DSH-style 50px plot: 44px label column + 1fr track */}
      <div
        className="grid overflow-hidden rounded-[var(--radius-control)] bg-[var(--c-surface-2)]"
        style={{ gridTemplateColumns: `${LABEL_WIDTH}px minmax(0, 1fr)`, height: PLOT_HEIGHT }}
      >
        {/* Lane labels — Input / Model / Tools (DSH LaneLabels) */}
        <div className="relative border-r border-[var(--c-border)]" aria-hidden="true">
          {['Input', 'Model', 'Tools'].map((label, i) => (
            <span
              key={label}
              className="absolute right-1.5 text-[length:var(--fs-xs)] leading-1 text-[var(--c-text-tertiary)] flex items-center justify-end"
              style={{ top: LANE_TOP + i * LANE_HEIGHT, height: 8 }}
            >
              {label}
            </span>
          ))}
        </div>

        {/* Track — DSH interactive area */}
        <div
          ref={trackRef}
          className="relative overflow-hidden cursor-crosshair touch-none"
          tabIndex={0}
          role="toolbar"
          aria-label="时间线概览，水平拖拽聚焦事件"
          onKeyDown={onKeyDown}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerCancel}
          onPointerLeave={() => { if (dragRef.current === null) setHover(null) }}
          onDoubleClick={(e) => { e.preventDefault(); onRangeChange(null) }}
        >
          {/* Hover line — DSH hoverLine (only on whitespace, no draft) */}
          {hover !== null && hover.recordIndex === null && draft === null && (
            <div
              className="absolute top-0 bottom-0 w-[2px] bg-[var(--c-accent)] pointer-events-none z-[4]"
              style={{ left: `clamp(0px, calc(${hover.fraction * 100}% - 1px), calc(100% - 2px))` }}
              aria-hidden="true"
            />
          )}

          {/* Selection box — DSH selection */}
          {selLeft != null && selWidth != null && (
            <>
              <div
                className="absolute top-0 bottom-0 z-[1] pointer-events-none"
                style={{
                  left: `${selLeft}%`, width: `max(1px, ${selWidth}%)`,
                  background: 'color-mix(in srgb, var(--c-accent) 12%, transparent)',
                }}
                aria-hidden="true"
              />
              {/* Selection edges — DSH selectionEdges */}
              <div
                className="absolute top-0 bottom-0 z-[4] pointer-events-none"
                style={{
                  left: `${selLeft}%`, width: `max(1px, ${selWidth}%)`,
                  boxShadow: 'inset 0 0 0 0 var(--c-accent)',
                  borderLeft: '2px solid var(--c-accent)',
                  borderRight: '2px solid var(--c-accent)',
                }}
                aria-hidden="true"
              />
            </>
          )}

          {/* Turn boundary vertical lines */}
          {model.turnBoundaries.map((b, i) => {
            const left = percent(b.time - domainStart)
            return (
              <div
                key={`tb-${i}`}
                className="absolute top-0 bottom-0 pointer-events-none"
                style={{ left: `${left}%`, width: '1px', backgroundColor: 'var(--c-border)' }}
                aria-hidden="true"
              />
            )
          })}

          {/* Cells — one bar per TrajectoryCell (DSH spans) */}
          {model.spans.map((span) => {
            const leftPct = percent(span.start - domainStart)
            // 'time' mode collapses spans to zero width — DSH gives them a
            // fixed pixel width ([data-equal-duration] → 8px).
            const widthPct = mode === 'time'
              ? 0
              : Math.max(0.5, percent(span.end - span.start))
            const gap = Math.min(widthPct * 0.08, 0.5)
            const cell = span.cell
            const baseColor = KIND_COLOR[cell.kind]
            const color = span.isError ? 'hsl(0, 72%, 50%)' : baseColor
            const isSelected = selectedIndex === span.index
            const detail = detailByIndex.get(span.index)
            const ttftMs = detail?.ttftMs
            const decodingMs = detail?.decodingMs
            const hasTiming = cell.kind === 'message'
              && !span.isError
              && ttftMs !== undefined && decodingMs !== undefined
              && ttftMs + decodingMs > 0
            const ttftFraction = hasTiming ? ttftMs! / (ttftMs! + decodingMs!) : null
            const barBackground = hasTiming && ttftFraction !== null
              ? `linear-gradient(to right,
                  ${ASSISTANT_TTFT_COLOR} 0%,
                  ${ASSISTANT_TTFT_COLOR} ${ttftFraction * 100}%,
                  ${ASSISTANT_DECODING_COLOR} ${ttftFraction * 100}%,
                  ${ASSISTANT_DECODING_COLOR} 100%)`
              : color

            const tooltipText = timelineTooltipLabel(cell.kind, detail)

            // DSH dimming states
            const inRange = activeRange != null
              && span.start <= activeRange.end && span.end >= activeRange.start
            const isSearchMatch = searchMatchIndexes == null ? null
              : searchMatchIndexes.has(span.index)

            // opacity: DSH uses CSS [data-selected='false']→0.2, [data-search-match='false']→0.14
            let opacity = (cell.kind === 'message' || span.isError) ? 1 : 0.78
            if (activeRange != null && !inRange) opacity = 0.2
            else if (searchMatchIndexes != null && isSearchMatch === false) opacity = 0.14

            return (
              <span
                key={span.index}
                role="button"
                tabIndex={-1}
                aria-hidden="true"
                data-timeline-span={cell.kind}
                data-timeline-record-index={span.index}
                data-assistant-timing={hasTiming || undefined}
                data-error={span.isError || undefined}
                data-equal-duration={mode === 'time' || undefined}
                data-selected={activeRange != null ? (inRange ? 'true' : 'false') : undefined}
                data-search-match={searchMatchIndexes != null ? (isSearchMatch ? 'true' : 'false') : undefined}
                data-current={isSelected || undefined}
                title={tooltipText}
                className="absolute rounded-[1px] transition-opacity"
                style={{
                  left: mode === 'time'
                    ? `calc(${leftPct}% - ${EQUAL_DURATION_BAR_PX / 2}px)`
                    : `calc(${leftPct}% + ${gap}%)`,
                  width: mode === 'time'
                    ? `${EQUAL_DURATION_BAR_PX}px`
                    : `max(2px, calc(${widthPct - 2 * gap}%))`,
                  top: LANE_TOP + span.lane * LANE_HEIGHT + BAR_GAP_Y,
                  height: BAR_HEIGHT,
                  minWidth: '2px',
                  background: barBackground,
                  opacity,
                  zIndex: isSelected ? 3 : 2,
                  boxShadow: isSelected
                    ? `0 0 0 1px var(--c-surface-2), 0 0 0 2px var(--c-accent)`
                    : undefined,
                  cursor: 'pointer',
                } as CSSProperties}
              >
                {/* Rich hover tooltip — DSH-style multiline */}
                {hover?.recordIndex === span.index && (
                  <span
                    className="absolute left-1/2 -translate-x-1/2 rounded-[var(--radius-control)] px-2 py-1.5 text-[length:var(--fs-xs)] leading-[14px] z-40 whitespace-pre pointer-events-none"
                    style={{
                      bottom: 'calc(100% + 6px)',
                      background: 'var(--c-surface)',
                      color: 'var(--c-text)',
                      border: '1px solid var(--c-border)',
                      boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
                    }}
                  >
                    {tooltipText.split('\n').map((l, k) => (
                      <span key={k} className="block tabular-nums">{l}</span>
                    ))}
                  </span>
                )}
              </span>
            )
          })}
        </div>
      </div>
    </div>
  )
}
