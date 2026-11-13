/**
 * TrajectoryTimeline — 3 轨道时间线概览，支持拖拽选区。
 *
 * 投影逻辑见 ./lib/timeline.ts，支持 4 种模式：
 * sequence（等宽）、duration（压缩空闲的真实时长）、time（等宽瞬时）、actual（完整真实时间）。
 *
 *   • 50px 绘图区 · 44px 标签列 · 3 轨道 × 14px · 8px 柱
 *   • kind 驱动的实色填充（data-timeline-span='kind'）
 *   • assistant 柱线性渐变 TTFT（浅）→ Decoding（深/红）
 *     （data-assistant-timing='true' + --trajectory-assistant-ttft）
 *   • error cell 覆盖为红色（data-error='true'）
 *   • turn 边界为 1px 竖线，按模式重新计算
 *   • 富文本 tooltip：kind 标签 + 时间范围 + Total + TTFT · Decoding
 *
 * 交互：
 *   • 拖拽选区（pointer down/move/up）→ onRangeChange
 *   • 选区变暗（data-selected='false' → opacity 0.2）
 *   • 搜索匹配变暗（data-search-match='false' → opacity 0.14）
 *   • 空白处显示竖直指示线
 *   • 点击柱 → onRecordSelect(index)；点击空白 → onRecordFocus(nearest)
 *   • Escape / 双击清空选区
 *
 * 数据：TrajectoryCell[]（每个 conversation-tree 节点一个 cell）。
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
  /** 当前投影模式下的活跃选区；无选区时为 null。 */
  range: { start: number; end: number } | null
  onRangeChange: (range: { start: number; end: number } | null) => void
  /** 匹配 ledger 搜索的 cell 索引集合；无查询时为 null。 */
  searchMatchIndexes: ReadonlySet<number> | null
  /** 时间线投影模式。 */
  mode: TrajectoryTimelineMode
}

// ── 7 种 cell kind 标签与配色 ────────────────────────────────────
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

// Assistant 耗时渐变配色
const ASSISTANT_DECODING_COLOR = 'hsl(15, 82%, 48%)'
const ASSISTANT_TTFT_COLOR = 'hsl(15, 48%, 72%)'

// ── 布局常量 ────────────────────────────────────────────────
const PLOT_HEIGHT = 50
const LABEL_WIDTH = 44
const LANE_TOP = 7
const LANE_HEIGHT = 14
const BAR_HEIGHT = 8
const BAR_GAP_Y = (LANE_HEIGHT - BAR_HEIGHT) / 2

// 交互阈值
const MINIMUM_DRAG_PX = 3

// 'time' 模式下等时长柱的固定像素宽度（[data-equal-duration='true'] → width: 8px）
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

/** 封装投影模块，为每个 span 附加源 cell（模块本身保持 spans 最小化；
 * 渲染器需要 cell 来获取颜色 / TTFT 渐变 / tooltip）。 */
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
    // 点击柱 → 选中该记录
    if (click && drag.recordIndex !== null) {
      onRangeChange(null)
      onSpanClick(drag.recordIndex)
      return
    }
    // 拖拽过短 → 居中最小选区（= min(domainDuration, fullDuration / spans.length)，一个平均单位）
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
    // 点击空白 → 聚焦最近的 span
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

  // 选区比例（确认或草稿）
  const visibleRange = draft ?? range
  const selLeft = visibleRange != null ? percent(visibleRange.start - domainStart) : null
  const selWidth = visibleRange != null ? percent(visibleRange.end - visibleRange.start) : null

  // Domain 摘要随模式变化：sequence 计数记录数；timed 模式报告（可能压缩的）span
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

      {/* 50px 绘图区：44px 标签列 + 1fr 轨道 */}
      <div
        className="grid overflow-hidden rounded-[var(--radius-control)] bg-[var(--c-surface-2)]"
        style={{ gridTemplateColumns: `${LABEL_WIDTH}px minmax(0, 1fr)`, height: PLOT_HEIGHT }}
      >
        {/* 轨道标签 — Input / Model / Tools */}
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

        {/* 轨道 — 交互区域 */}
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
          {/* 悬停指示线 — 仅在空白处且无草稿选区时显示 */}
          {hover !== null && hover.recordIndex === null && draft === null && (
            <div
              className="absolute top-0 bottom-0 w-[2px] bg-[var(--c-accent)] pointer-events-none z-[4]"
              style={{ left: `clamp(0px, calc(${hover.fraction * 100}% - 1px), calc(100% - 2px))` }}
              aria-hidden="true"
            />
          )}

          {/* 选区框 */}
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
              {/* 选区边缘 */}
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

          {/* turn 边界竖线 */}
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

          {/* Cells — 每个 TrajectoryCell 渲染一条柱 */}
          {model.spans.map((span) => {
            const leftPct = percent(span.start - domainStart)
            // 'time' 模式下 span 宽度坍缩为零，渲染时给它一个固定像素宽度
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

            // 变暗状态：选区 + 搜索匹配
            const inRange = activeRange != null
              && span.start <= activeRange.end && span.end >= activeRange.start
            const isSearchMatch = searchMatchIndexes == null ? null
              : searchMatchIndexes.has(span.index)

            // 不透明度：消息/错误柱默认 1，其它 0.78；选中外 0.2，搜索不匹配 0.14
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
                {/* 富文本悬停 tooltip */}
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
