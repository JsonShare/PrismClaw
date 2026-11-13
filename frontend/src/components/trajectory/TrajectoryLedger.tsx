/**
 * TrajectoryLedger — 紧凑 2 列 cell ledger。
 *
 *   • 2 列表：`event`（kind tag + turn rail）| `content`（单行文本；
 *     工具显示 `name(args) → result`）
 *   • 吸附式可折叠 Turn 标题（wall-span duration · tokens · tool histogram，
 *     见 ./trajectory/metrics.ts）
 *   • 5 折叠规则：summarizeTurn / collapseTurnRecords /
 *     assistantToolCalls / summarizeAssistantTools / collapseAssistantRecords
 *     — 折叠的 turn 和 assistant tool-call run 渲染为
 *     "… summary" 行（点击或双击展开）
 *   • 双击折叠：collapsed row → expand；带 tool calls 的 message row →
 *     折叠它的 tools；turn-start row → 折叠整个 turn
 *   • 时间线选区外的行 opacity 0.24 变暗（无 hover 恢复）
 *   • 多词 AND 搜索过滤 via searchMatchIds（Panel 的 TrajectorySearchIndex）；
 *     搜索时挂起所有折叠
 *
 * 数据：TrajectoryCell[]（每个 conversation-tree 节点一个 cell）。
 */
import { useMemo, useRef, type ReactNode } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import {
  ChevronDown, ChevronRight, AlertTriangle,
  Settings as SettingsIcon, User, Info, Sparkles, Wrench, Cpu,
} from 'lucide-react'
import type { TrajectoryCell, TrajectoryCellKind } from '../../types'
import { trajectoryTurnMetrics, formatToolHistogram } from './lib/metrics'

// 虚拟化常量
const VIRTUALIZATION_THRESHOLD = 100
const VIRTUAL_OVERSCAN_ROWS = 12
const HEADER_ROW_HEIGHT = 33
const CELL_ROW_HEIGHT = 30
const COLLAPSED_ROW_HEIGHT = 26

interface TrajectoryLedgerProps {
  cells: TrajectoryCell[]
  selectedIndex: number | null
  onSelect: (index: number) => void
  searchQuery: string
  /** 折叠状态由 Panel 持有 — toolbar 的全折叠派生按钮需要。 */
  collapsedTurns: ReadonlySet<number>
  collapsedAssistants: ReadonlySet<string>
  onToggleTurn: (turn: number) => void
  onToggleAssistant: (id: string) => void
  /** 活跃 timeline 选区内的 cell 索引；无选区时为 null。 */
  focusIndexes?: ReadonlySet<number> | null
  /** Panel 的 TrajectorySearchIndex 匹配的 cell id（多词 AND）；无查询时为 null。 */
  searchMatchIds?: ReadonlySet<string> | null
}

// 与 TrajectoryTimeline 一致的 7 种 cell kind 配色
const KIND_META: Record<TrajectoryCellKind, { label: string; bg: string; color: string }> = {
  system:    { label: 'SYSTEM',    bg: 'hsla(210, 8%, 55%, 0.16)',  color: 'hsl(210, 8%, 30%)' },
  user:      { label: 'USER',      bg: 'hsla(210, 70%, 50%, 0.16)', color: 'hsl(210, 65%, 30%)' },
  context:   { label: 'CONTEXT',   bg: 'hsla(165, 50%, 42%, 0.16)', color: 'hsl(165, 50%, 25%)' },
  compacted: { label: 'COMPACTED', bg: 'hsla(280, 30%, 50%, 0.16)', color: 'hsl(280, 30%, 28%)' },
  message:   { label: 'ASSISTANT', bg: 'hsla(15, 75%, 55%, 0.16)',  color: 'hsl(15, 70%, 32%)' },
  tool:      { label: 'TOOL',      bg: 'hsla(38, 80%, 50%, 0.16)',  color: 'hsl(38, 75%, 28%)' },
  subtool:   { label: 'SUB',       bg: 'hsla(38, 65%, 60%, 0.16)',  color: 'hsl(38, 60%, 32%)' },
}

function KindIcon({ kind }: { kind: TrajectoryCellKind }) {
  switch (kind) {
    case 'system':    return <SettingsIcon size={12} />
    case 'user':      return <User size={12} />
    case 'context':   return <Info size={12} />
    case 'compacted': return <AlertTriangle size={12} />
    case 'message':   return <Sparkles size={12} />
    case 'tool':
    case 'subtool':   return <Wrench size={12} />
  }
}

function formatMs(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.round(ms / 1000)}s`
}

function formatTokens(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—'
  if (n < 1000) return String(n)
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`
  return `${(n / 1_000_000).toFixed(2)}M`
}

function truncate(s: string, n: number): string {
  return s.length <= n ? s : s.slice(0, n) + '…'
}

/** 工具显示名 — 优先 backend tool_name，否则从 tool_calls_summary 提取。 */
function toolName(cell: TrajectoryCell): string {
  if (cell.tool_name) return cell.tool_name
  if (cell.tool_calls_summary.length > 0) {
    const m = cell.tool_calls_summary[0].match(/^([^(]*)\(/)
    return m ? m[1] : cell.tool_calls_summary[0]
  }
  return cell.kind.toUpperCase()
}

function highlightMatch(text: string, query: string): ReactNode {
  const term = query.trim().split(/\s+/).filter(Boolean)[0]
  if (!term) return text
  const idx = text.toLowerCase().indexOf(term.toLowerCase())
  if (idx === -1) return text
  return (
    <>
      {text.slice(0, idx)}
      <mark className="bg-[var(--c-warning, #d97706)]/20 text-[var(--c-warning, #d97706)] rounded-[var(--radius-control)] px-0.5">
        {text.slice(idx, idx + term.length)}
      </mark>
      {text.slice(idx + term.length)}
    </>
  )
}

const PREVIEW_MAX = 120

interface CellWithIndex {
  cell: TrajectoryCell
  originalIndex: number
}

/** 折叠规则 #3：assistant record 后紧跟的 tool/subtool records。 */
function assistantToolCalls(
  items: readonly CellWithIndex[],
  assistantOriginalIndex: number,
): readonly CellWithIndex[] {
  const at = items.findIndex(item => item.originalIndex === assistantOriginalIndex)
  if (at === -1 || items[at]?.cell.kind !== 'message') return []
  const calls: CellWithIndex[] = []
  for (let i = at + 1; i < items.length; i++) {
    const item = items[i]
    if (item === undefined) break
    if (item.cell.kind !== 'tool' && item.cell.kind !== 'subtool') break
    calls.push(item)
  }
  return calls
}

/** 折叠规则 #1（迭代计数替代 Step groups）。 */
function summarizeTurn(items: readonly CellWithIndex[]): string {
  const iterations = items.filter(item => item.cell.kind === 'message').length
  const toolCalls = items.filter(item =>
    item.cell.kind === 'tool' || item.cell.kind === 'subtool',
  ).length
  return `${iterations} 次迭代 · ${toolCalls} 次工具调用`
}

/** 折叠规则 #4。 */
function summarizeAssistantTools(items: readonly CellWithIndex[]): string {
  const names = [...new Set(items.map(item => toolName(item.cell)).filter(n => n !== ''))]
  const count = items.length
  const summary = `${count} 次工具调用`
  return names.length > 0 ? `${summary} · ${names.join(', ')}` : summary
}

/** 一个投影后的 ledger 行：真实 cell 或折叠摘要。 */
type LedgerRow =
  | {
    type: 'cell'
    cell: TrajectoryCell
    originalIndex: number
    turnStart: boolean
  }
  | {
    type: 'collapsed-turn'
    summary: string
  }
  | {
    type: 'collapsed-assistant'
    summary: string
    /** 折叠的 assistant message 的 cell_id — 切换目标。 */
    assistantId: string
    turnEnd: boolean
  }

/**
 * 对一个 turn 的 rows 应用折叠规则 #2 + #5：
 * collapsedTurn 将第一条内容行之后的所有内容折叠为
 * "… N 次迭代 · N 次工具调用" 行；collapsedAssistants 将每个
 * assistant 连续的 tool-call run 折叠为 "… N 次工具调用 · names" 行。
 */
function projectRows(
  items: readonly CellWithIndex[],
  turnCollapsed: boolean,
  collapsedAssistants: ReadonlySet<string>,
): LedgerRow[] {
  const rows: LedgerRow[] = []
  let pending: CellWithIndex[] = []

  const flush = () => {
    if (pending.length === 0) return
    rows.push(...pending.map((item, i) => ({
      type: 'cell' as const,
      cell: item.cell,
      originalIndex: item.originalIndex,
      turnStart: i === 0 && rows.length === 0,
    })))
    pending = []
  }

  // Turn 折叠：保留 system 行 + 第一条内容行，其余合并为一条摘要行
  if (turnCollapsed) {
    const content = items.filter(item => item.cell.kind !== 'system')
    if (content.length > 1) {
      const first = content[0]
      if (first !== undefined) {
        rows.push({ type: 'cell', cell: first.cell, originalIndex: first.originalIndex, turnStart: true })
      }
      rows.push({ type: 'collapsed-turn', summary: summarizeTurn(content.slice(1)) })
      return rows
    }
  }

  // Assistant 折叠
  for (let i = 0; i < items.length; i++) {
    const item = items[i]
    if (item === undefined) continue
    if (item.cell.kind === 'message' && collapsedAssistants.has(item.cell.cell_id)) {
      const calls = assistantToolCalls(items, item.originalIndex)
      if (calls.length > 0) {
        flush()
        rows.push({ type: 'cell', cell: item.cell, originalIndex: item.originalIndex, turnStart: rows.length === 0 })
        rows.push({
          type: 'collapsed-assistant',
          summary: summarizeAssistantTools(calls),
          assistantId: item.cell.cell_id,
          turnEnd: calls.at(-1)?.originalIndex === items.at(-1)?.originalIndex,
        })
        i += calls.length
        continue
      }
    }
    pending.push(item)
  }
  flush()
  return rows
}

/**
 * 紧凑 cell 行。
 *
 * 布局（2 列 grid）：
 *   [event]  kind tag（icon + label）+ turn 起始时的 turn rail
 *   [content] 单行文本；工具显示 `name(args) → result`
 */
function CellRow({
  cell,
  originalIndex,
  turnStart,
  selected,
  searchQuery,
  onSelect,
  onDoubleClick,
  focusIndexes,
}: {
  cell: TrajectoryCell
  originalIndex: number
  turnStart: boolean
  selected: boolean
  searchQuery: string
  onSelect: (index: number) => void
  onDoubleClick?: () => void
  focusIndexes?: ReadonlySet<number> | null
}) {
  const km = KIND_META[cell.kind]
  const hasError = !!cell.error || cell.is_error
  const isTool = cell.kind === 'tool' || cell.kind === 'subtool'

  // 时间线选区变暗 — 选区外行 opacity 0.24（无 hover 恢复）
  const inFocus = focusIndexes == null ? null : focusIndexes.has(originalIndex)

  // Content 行 — 工具显示 `name(args) → result`，其他显示 content/input/output 预览
  let contentLine: ReactNode
  if (isTool) {
    const name = toolName(cell)
    // input_detail = 完整工具参数（后端从发起调用的 assistant entry 反查）
    const args = cell.input_detail ?? ''
    const result = cell.output_detail ?? cell.content_preview ?? ''
    contentLine = (
      <span className="flex items-baseline gap-1 min-w-0 font-mono text-[length:var(--fs-xs)]">
        <span className="text-[var(--c-text)] font-medium shrink-0">
          {highlightMatch(name, searchQuery)}
        </span>
        {args && (
          <span className="text-[var(--c-text-tertiary)] truncate">
            ({highlightMatch(truncate(args, 80), searchQuery)})
          </span>
        )}
        {result && (
          <>
            <span className="text-[var(--c-text-tertiary)] shrink-0">→</span>
            <span className={hasError
              ? 'text-[var(--c-danger, #dc2626)] truncate'
              : 'text-[var(--c-text-secondary)] truncate'}
            >
              {highlightMatch(truncate(result, PREVIEW_MAX), searchQuery)}
            </span>
          </>
        )}
      </span>
    )
  } else if (cell.kind === 'message' || cell.kind === 'compacted') {
    const text = cell.output_detail ?? cell.content_preview ?? ''
    contentLine = text
      ? <span className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)] truncate">
          {highlightMatch(truncate(text, PREVIEW_MAX), searchQuery)}
        </span>
      : <span className="text-[length:var(--fs-xs)] text-[var(--c-text-tertiary)] italic pl-4">执行工具</span>
  } else {
    // user / system / context
    const text = cell.input_detail ?? cell.content_preview ?? ''
    contentLine = text
      ? <span className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)] truncate">
          {highlightMatch(truncate(text, PREVIEW_MAX), searchQuery)}
        </span>
      : null
  }

  // 右侧紧凑 metrics（assistant: tokens + duration；其他: duration）
  const durationMs = cell.completed_at_ms != null
    ? cell.completed_at_ms - cell.started_at_ms
    : null
  const outTok = cell.usage_output_tokens
  const inTok = cell.usage_input_tokens

  return (
    <button
      type="button"
      onClick={() => onSelect(originalIndex)}
      onDoubleClick={onDoubleClick}
      aria-selected={selected}
      role="row"
      data-kind={cell.kind}
      data-error={hasError || undefined}
      data-selected={selected || undefined}
      data-timeline-focus={inFocus === null ? undefined : inFocus ? 'inside' : 'outside'}
      className={`group w-full grid items-center gap-2 px-3 py-[7px] text-left transition-colors border-l-2
        ${selected
          ? 'bg-[var(--c-accent)]/10 border-l-[var(--c-accent)]'
          : 'border-l-transparent hover:bg-[var(--c-surface-2)]'}
        ${inFocus === false ? 'opacity-[0.24]' : ''}
        ${inFocus === true && !selected ? 'bg-[var(--c-accent)]/5 border-l-[var(--c-accent)]/40' : ''}
      `}
      style={{ gridTemplateColumns: 'minmax(72px, max-content) minmax(0, 1fr) auto' }}
    >
      {/* ── event column: turn label (turn start) + kind tag ── */}
      <span className="flex items-center gap-1.5 min-w-0">
        <span
          className={`text-[length:var(--fs-xs)] font-semibold tabular-nums text-[var(--c-text-tertiary)] shrink-0 w-6 ${turnStart ? '' : 'invisible'}`}
          title={turnStart ? `Turn ${cell.turn}` : undefined}
        >
          {turnStart ? `#${cell.turn}` : '#'}
        </span>
        <span
          className="inline-flex items-center gap-1 rounded-[var(--radius-control)] px-1.5 py-[1px] text-[length:var(--fs-xs)] font-semibold uppercase tracking-wide shrink-0"
          style={{ backgroundColor: km.bg, color: km.color }}
          title={km.label}
        >
          <KindIcon kind={cell.kind} />
          <span>{km.label}</span>
        </span>
      </span>

      {/* ── content column: one-line text ── */}
      <span className="min-w-0 truncate">{contentLine}</span>

      {/* ── trailing metrics ── */}
      <span className="flex items-center gap-1.5 shrink-0 text-[length:var(--fs-xs)] text-[var(--c-text-tertiary)] tabular-nums justify-end">
        {cell.kind === 'message' && inTok != null && (
          <span title="input tokens" className="hidden sm:inline">↑{formatTokens(inTok)}</span>
        )}
        {cell.kind === 'message' && outTok != null && (
          <span title="output tokens" className="hidden sm:inline">↓{formatTokens(outTok)}</span>
        )}
        {durationMs != null && <span title="duration">{formatMs(durationMs)}</span>}
        {hasError && <span className="w-1.5 h-1.5 rounded-full bg-[var(--c-danger, #dc2626)]" title="error" />}
      </span>
    </button>
  )
}

/** 折叠摘要行："… summary"，点击展开。 */
function CollapsedRow({
  summary,
  kind,
  onToggle,
  turnEnd,
}: {
  summary: string
  kind: 'turn' | 'assistant'
  onToggle: () => void
  turnEnd: boolean
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      onDoubleClick={onToggle}
      role="row"
      data-collapsed-summary={kind}
      data-turn-end={turnEnd || undefined}
      title={summary}
      className="w-full grid items-center gap-2 px-3 py-[5px] text-left border-l-2 border-l-transparent hover:bg-[var(--c-surface-2)] transition-colors cursor-pointer"
      style={{ gridTemplateColumns: 'minmax(72px, max-content) minmax(0, 1fr) auto' }}
    >
      <span className="flex items-center gap-1.5 min-w-0">
        <span
          className={`text-[length:var(--fs-xs)] font-semibold tabular-nums text-[var(--c-text-tertiary)] shrink-0 w-6 ${turnEnd ? '' : 'invisible'}`}
        >
          {turnEnd ? '↳' : '↳'}
        </span>
        <span className="text-[length:var(--fs-xs)] font-semibold uppercase tracking-wide text-[var(--c-text-tertiary)]">
          …
        </span>
      </span>
      <span className="min-w-0 truncate text-[length:var(--fs-xs)] text-[var(--c-text-tertiary)] italic">
        {summary}
      </span>
      <span />
    </button>
  )
}

const TURN_HUES = [210, 270, 190, 150, 38, 330, 240, 0]

/** 吸附式 turn 标题按钮 — 普通渲染器和虚拟化渲染器共用。 */
function TurnHeaderButton({
  turn,
  cells,
  collapsed,
  onToggle,
}: {
  turn: number
  cells: CellWithIndex[]
  collapsed: boolean
  onToggle: () => void
}) {
  const count = cells.length
  const hasError = cells.some(c => c.cell.error != null || c.cell.is_error)
  const hue = TURN_HUES[(turn - 1) % TURN_HUES.length]

  // Turn 聚合统计 — wall duration · tokens · tool histogram via ./trajectory/metrics.ts
  const metrics = useMemo(() => trajectoryTurnMetrics(cells.map(c => c.cell)), [cells])

  return (
    <button
      type="button"
      onClick={onToggle}
      className="sticky top-0 z-10 w-full flex items-center gap-2 px-3 py-2 bg-[var(--c-surface)] border-b border-[var(--c-border)] text-left hover:bg-[var(--c-surface-2)] transition-colors cursor-pointer"
    >
      <div
        className="w-2 h-2 rounded-[var(--radius-control)] shrink-0"
        style={{ backgroundColor: `hsl(${hue}, 68%, 55%)` }}
      />
      {collapsed
        ? <ChevronRight size={12} className="text-[var(--c-text-secondary)]" />
        : <ChevronDown size={12} className="text-[var(--c-text-secondary)]" />}
      <span
        className="text-[length:var(--fs-sm)] font-semibold tabular-nums"
        style={{ color: `hsl(${hue}, 62%, 42%)` }}
      >
        Turn {turn}
      </span>
      <span className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]">
        {count} cells
      </span>
      {/* 聚合描述：wall duration · tokens · tool histogram */}
      {(metrics.wallMs != null || metrics.inputTokens > 0 || metrics.outputTokens > 0) && (
        <span className="text-[length:var(--fs-xs)] text-[var(--c-text-tertiary)] tabular-nums ml-auto truncate">
          {metrics.wallMs != null && <span title="turn wall clock">{formatMs(metrics.wallMs)}</span>}
          {metrics.wallMs != null && (metrics.inputTokens > 0 || metrics.outputTokens > 0) && <span className="mx-1 opacity-40">·</span>}
          {metrics.inputTokens > 0 && <span title="input tokens">↑{formatTokens(metrics.inputTokens)}</span>}
          {metrics.inputTokens > 0 && metrics.outputTokens > 0 && <span className="mx-0.5 opacity-40">/</span>}
          {metrics.outputTokens > 0 && <span title="output tokens">↓{formatTokens(metrics.outputTokens)}</span>}
          {metrics.cacheReadTokens > 0 && <span className="ml-1 opacity-60" title="cache read">· cache {formatTokens(metrics.cacheReadTokens)}</span>}
          {metrics.toolHistogram.length > 0 && (
            <span className="ml-1 opacity-60" title="tool calls">
              {' · '}
              {formatToolHistogram(metrics.toolHistogram)}
            </span>
          )}
        </span>
      )}
      {hasError && <span className="w-1.5 h-1.5 rounded-full bg-[var(--c-danger, #dc2626)] shrink-0" />}
    </button>
  )
}

/** 虚拟化渲染器用的扁平 ledger row。 */
type VirtualRow =
  | { type: 'header'; turn: number; cells: CellWithIndex[]; key: string }
  | { type: 'cell'; cell: TrajectoryCell; originalIndex: number; turnStart: boolean; key: string }
  | { type: 'collapsed-turn'; summary: string; turn: number; key: string }
  | { type: 'collapsed-assistant'; summary: string; assistantId: string; turnEnd: boolean; key: string }

function virtualRowHeight(row: VirtualRow): number {
  switch (row.type) {
    case 'header': return HEADER_ROW_HEIGHT
    case 'cell': return CELL_ROW_HEIGHT
    default: return COLLAPSED_ROW_HEIGHT
  }
}

/** 共享双击折叠处理器。 */
function rowDoubleClickHandler(
  cellsOfTurn: readonly CellWithIndex[],
  turnCollapsed: boolean,
  onToggleTurn: () => void,
  onToggleAssistant: (id: string) => void,
) {
  return (cell: TrajectoryCell, originalIndex: number, turnStart: boolean) => {
    if (turnCollapsed) {
      onToggleTurn()
      return
    }
    if (cell.kind === 'message' && assistantToolCalls(cellsOfTurn, originalIndex).length > 0) {
      onToggleAssistant(cell.cell_id)
      return
    }
    if (turnStart && cellsOfTurn.filter(c => c.cell.kind !== 'system').length > 1) {
      onToggleTurn()
    }
  }
}

export function TrajectoryLedger({
  cells,
  selectedIndex,
  onSelect,
  searchQuery,
  collapsedTurns,
  collapsedAssistants,
  onToggleTurn,
  onToggleAssistant,
  focusIndexes,
  searchMatchIds,
}: TrajectoryLedgerProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const isSearching = searchMatchIds != null

  // 用 Panel 的多词 AND 索引过滤，再按 turn（升序）分组
  const turnGroups = useMemo(() => {
    const filtered: CellWithIndex[] = []
    for (let i = 0; i < cells.length; i++) {
      const cell = cells[i]
      if (searchMatchIds != null && !searchMatchIds.has(cell.cell_id)) continue
      filtered.push({ cell, originalIndex: i })
    }
    const groups = new Map<number, CellWithIndex[]>()
    for (const item of filtered) {
      const arr = groups.get(item.cell.turn) ?? []
      arr.push(item)
      groups.set(item.cell.turn, arr)
    }
    return [...groups.entries()].sort((a, b) => a[0] - b[0])
  }, [cells, searchMatchIds])

  const toggleTurn = onToggleTurn

  const isCollapsed = (turn: number): boolean => {
    // 搜索时挂起折叠，让所有匹配可见
    if (isSearching) return false
    return collapsedTurns.has(turn)
  }

  // 扁平投影行：每个 turn 的 header + fold-projected rows。
  // 普通渲染器和虚拟化窗口共用
  const rows = useMemo<VirtualRow[]>(() => {
    const out: VirtualRow[] = []
    for (const [turn, groupCells] of turnGroups) {
      const collapsed = isCollapsed(turn)
      out.push({ type: 'header', turn, cells: groupCells, key: `h-${turn}` })
      if (collapsed) continue
      for (const row of projectRows(groupCells, collapsed, collapsedAssistants)) {
        if (row.type === 'cell') {
          out.push({
            type: 'cell',
            cell: row.cell,
            originalIndex: row.originalIndex,
            turnStart: row.turnStart,
            key: `c-${row.cell.cell_id}`,
          })
        } else if (row.type === 'collapsed-turn') {
          out.push({ type: 'collapsed-turn', summary: row.summary, turn, key: `ct-${turn}` })
        } else {
          out.push({
            type: 'collapsed-assistant',
            summary: row.summary,
            assistantId: row.assistantId,
            turnEnd: row.turnEnd,
            key: `ca-${row.assistantId}`,
          })
        }
      }
    }
    return out
  }, [turnGroups, collapsedTurns, collapsedAssistants, isSearching])

  // 仅超过阈值时启用虚拟化 — 小 ledger 用吸附 header 的普通渲染器
  const virtualizationEnabled = rows.length > VIRTUALIZATION_THRESHOLD
  const virtualizer = useVirtualizer({
    count: virtualizationEnabled ? rows.length : 0,
    enabled: virtualizationEnabled,
    estimateSize: (index: number) => virtualRowHeight(rows[index] ?? rows[0]),
    getItemKey: (index: number) => rows[index]?.key ?? index,
    getScrollElement: () => scrollRef.current,
    overscan: VIRTUAL_OVERSCAN_ROWS,
  })

  /** 渲染一条扁平行（两种渲染器共用）。 */
  const renderRow = (row: VirtualRow, turnCollapsed: boolean) => {
    if (row.type === 'header') {
      return (
        <TurnHeaderButton
          turn={row.turn}
          cells={row.cells}
          collapsed={turnCollapsed}
          onToggle={() => toggleTurn(row.turn)}
        />
      )
    }
    if (row.type === 'cell') {
      const groupCells = turnGroups.find(([turn]) => turn === row.cell.turn)?.[1] ?? []
      return (
        <CellRow
          cell={row.cell}
          originalIndex={row.originalIndex}
          turnStart={row.turnStart}
          selected={selectedIndex === row.originalIndex}
          searchQuery={searchQuery}
          onSelect={onSelect}
          onDoubleClick={() => rowDoubleClickHandler(
            groupCells,
            turnCollapsed,
            () => toggleTurn(row.cell.turn),
            onToggleAssistant,
          )(row.cell, row.originalIndex, row.turnStart)}
          focusIndexes={focusIndexes}
        />
      )
    }
    return (
      <CollapsedRow
        summary={row.summary}
        kind={row.type === 'collapsed-turn' ? 'turn' : 'assistant'}
        turnEnd={row.type === 'collapsed-assistant' ? row.turnEnd : false}
        onToggle={row.type === 'collapsed-turn'
          ? () => toggleTurn(row.turn)
          : () => onToggleAssistant(row.assistantId)}
      />
    )
  }

  if (cells.length === 0) {
    return (
      <div className="flex items-center justify-center py-12 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">
        <div className="text-center">
          <Cpu size={32} className="mx-auto mb-2 opacity-30" />
          <div>暂无轨迹数据</div>
        </div>
      </div>
    )
  }

  return (
    <div ref={scrollRef} className="flex-1 overflow-y-auto">
      {virtualizationEnabled ? (
        <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
          {virtualizer.getVirtualItems().map(item => {
            const row = rows[item.index]
            if (row === undefined) return null
            const turnCollapsed = row.type === 'header' ? isCollapsed(row.turn) : true
            return (
              <div
                key={row.key}
                data-index={item.index}
                style={{
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  width: '100%',
                  height: item.size,
                  transform: `translateY(${item.start}px)`,
                }}
              >
                {renderRow(row, row.type === 'cell' || row.type === 'collapsed-turn'
                  ? isCollapsed(row.type === 'cell' ? row.cell.turn : row.turn)
                  : turnCollapsed)}
              </div>
            )
          })}
        </div>
      ) : (
        turnGroups.map(([turn, groupCells]) => {
          const collapsed = isCollapsed(turn)
          return (
            <section key={String(turn)} className="border-b border-[var(--c-border)] last:border-b-0">
              <TurnHeaderButton
                turn={turn}
                cells={groupCells}
                collapsed={collapsed}
                onToggle={() => toggleTurn(turn)}
              />
              {!collapsed && (
                <div>
                  {projectRows(groupCells, collapsed, collapsedAssistants).map(row => {
                    if (row.type === 'cell') {
                      return (
                        <CellRow
                          key={row.cell.cell_id}
                          cell={row.cell}
                          originalIndex={row.originalIndex}
                          turnStart={row.turnStart}
                          selected={selectedIndex === row.originalIndex}
                          searchQuery={searchQuery}
                          onSelect={onSelect}
                          onDoubleClick={() => rowDoubleClickHandler(
                            groupCells,
                            collapsed,
                            () => toggleTurn(turn),
                            onToggleAssistant,
                          )(row.cell, row.originalIndex, row.turnStart)}
                          focusIndexes={focusIndexes}
                        />
                      )
                    }
                    return (
                      <CollapsedRow
                        key={row.type === 'collapsed-turn' ? `ct-${turn}` : `ca-${row.assistantId}`}
                        summary={row.summary}
                        kind={row.type === 'collapsed-turn' ? 'turn' : 'assistant'}
                        turnEnd={row.type === 'collapsed-assistant' ? row.turnEnd : false}
                        onToggle={row.type === 'collapsed-turn'
                          ? () => toggleTurn(turn)
                          : () => onToggleAssistant(row.assistantId)}
                      />
                    )
                  })}
                </div>
              )}
            </section>
          )
        })
      )}
    </div>
  )
}
