/**
 * TrajectoryPanel — 轨迹审计弹窗 (DSH-aligned).
 *
 * Timeline + Ledger 渲染 TrajectoryCell[] (per-conversation-event cells,
 * 每个 tree 节点一个 cell) — 这是 DSH 真正的数据模型.
 * Inspector 也是 cell 驱动 (DSH-aligned)，展示选中 cell 的
 * content/tokens/timing/schema；assistant cell 附带按 turn+iteration
 * 匹配的 RequestAuditRecord 显示 4 层快照 bonus (PrismClaw 特有 ≥ DSH).
 */
import { useState, useMemo, useEffect, useRef } from 'react'
import { RefreshCw, BarChart3, Eye } from 'lucide-react'
import { ModalShell } from '../modals/ModalShell'
import { TrajectoryToolbar } from './TrajectoryToolbar'
import { TrajectoryTimeline } from './TrajectoryTimeline'
import { TrajectoryLedger } from './TrajectoryLedger'
import { TrajectoryInspector } from './TrajectoryInspector'
import { useTrajectory } from '../../hooks/trajectory/useTrajectory'
import { useTrajectoryCells } from '../../hooks/trajectory/useTrajectoryCells'
import type { TrajectoryCell, RequestAuditRecord } from '../../types'
import {
  trajectoryTimelineFocusIndexes,
  type TrajectoryTimelineMode,
} from './lib/timeline'
import { TrajectorySearchIndex } from './lib/searchIndex'
import type { TrajectoryCumulativeUsage } from './TrajectoryInspector'

interface TrajectoryPanelProps {
  open: boolean
  onClose: () => void
  sessionId: string | null
  token: string | null
  onUnauthorized?: () => void
}

// DSH detailsResizeHandle constants (TrajectoryTable.tsx).
const INSPECTOR_MIN_WIDTH = 320
const INSPECTOR_MAX_WIDTH = 720
const LEDGER_MIN_WIDTH = 280
const INSPECTOR_RESIZE_STEP = 16

/** DSH clampDetailsWidth: clamp to [320, min(720, split − 280)]. */
function clampInspectorWidth(width: number, splitWidth: number): number {
  const maxWidth = Math.max(
    INSPECTOR_MIN_WIDTH,
    Math.min(INSPECTOR_MAX_WIDTH, splitWidth - LEDGER_MIN_WIDTH),
  )
  return Math.round(Math.min(Math.max(width, INSPECTOR_MIN_WIDTH), maxWidth))
}

interface InspectorResizeDrag {
  pointerId: number
  startX: number
  startWidth: number
  splitWidth: number
}

export function TrajectoryPanel({
  open,
  onClose,
  sessionId,
  token,
  onUnauthorized,
}: TrajectoryPanelProps) {
  // Data fetching — cells (primary, DSH per-conversation-event) + records
  // (secondary, per-LLM-iteration, for assistant cells' 4-layer bonus)
  const { data, readWarning, loading, error, refresh } = useTrajectory(sessionId, token, open, onUnauthorized)
  const { cells, loading: cellsLoading, refresh: refreshCells } = useTrajectoryCells(sessionId, token, open, onUnauthorized)

  // UI state — selectedIndex indexes into cells[] (NOT records[])
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null)
  const [inspectorOpen, setInspectorOpen] = useState(false)
  // Fold state owned HERE (DSH TrajectoryView structure) — the toolbar's
  // all-collapsed pressed states derive from collapsibility, which needs
  // the full cells[] the Panel already holds.
  const [collapsedTurns, setCollapsedTurns] = useState<ReadonlySet<number>>(new Set())
  const [collapsedAssistants, setCollapsedAssistants] = useState<ReadonlySet<string>>(new Set())
  // DSH parity: timeline drag-to-select range + search match indexes drive
  // timeline dimming + ledger focus highlighting. The range lives in the
  // ACTIVE MODE's domain (sequence = record units, timed = ms).
  const [timelineRange, setTimelineRange] = useState<{ start: number; end: number } | null>(null)
  // DSH default is 'sequence' (equal-width); switchable in the toolbar.
  const [timelineMode, setTimelineMode] = useState<TrajectoryTimelineMode>('sequence')
  // Inspector split width — DSH detailsWidth: null = default half split,
  // draggable handle + arrow keys + double-click reset.
  const [inspectorWidth, setInspectorWidth] = useState<number | null>(null)
  const splitRef = useRef<HTMLDivElement>(null)
  const inspectorDragRef = useRef<InspectorResizeDrag | null>(null)

  // A selection from one projection is meaningless in another — clear it
  // on mode switch (DSH clears the range on mode change the same way).
  const handleTimelineModeChange = (mode: TrajectoryTimelineMode) => {
    setTimelineMode(mode)
    setTimelineRange(null)
  }

  // Records — drop orphaned rows (defensive; backend now drops at projection)
  const visibleRecords = useMemo(() => {
    if (!data?.records) return []
    return data.records.filter((r) => r.status !== 'orphaned')
  }, [data?.records])

  // Sync selected cell index when cells change (e.g. refresh shortens array)
  useEffect(() => {
    if (selectedIndex == null) return
    if (selectedIndex >= cells.length) {
      setSelectedIndex(null)
    }
  }, [cells.length, selectedIndex])

  // Handle span click from timeline / row click from ledger — both index
  // into cells[], keeping timeline + ledger + inspector in sync (DSH parity).
  const handleSelect = (index: number) => {
    setSelectedIndex(index)
    setInspectorOpen(true)
  }

  // DSH parity fold controls (TrajectoryView toggleAllTurns/toggleAllAssistants):
  // collapsibility is DERIVED from cells — turns with >1 non-system content row,
  // and message cells directly followed by a tool/subtool cell. The toolbar's
  // pressed state = "every collapsible one is folded".
  const collapsibleTurnIds = useMemo(() => {
    const byTurn = new Map<number, number>()
    for (const cell of cells) {
      if (cell.kind === 'system') continue
      byTurn.set(cell.turn, (byTurn.get(cell.turn) ?? 0) + 1)
    }
    return [...byTurn.entries()].filter(([, count]) => count > 1).map(([turn]) => turn)
  }, [cells])
  const collapsibleAssistantIds = useMemo(() => {
    const ids: string[] = []
    for (let i = 0; i < cells.length; i++) {
      const cell = cells[i]
      const next = cells[i + 1]
      if (cell?.kind === 'message' && (next?.kind === 'tool' || next?.kind === 'subtool')) {
        ids.push(cell.cell_id)
      }
    }
    return ids
  }, [cells])
  const allTurnsCollapsed = collapsibleTurnIds.length > 0
    && collapsibleTurnIds.every(turn => collapsedTurns.has(turn))
  const allAssistantsCollapsed = collapsibleAssistantIds.length > 0
    && collapsibleAssistantIds.every(id => collapsedAssistants.has(id))

  const handleToggleAllTurns = () => {
    setCollapsedTurns(current => {
      const next = new Set(current)
      if (allTurnsCollapsed) {
        for (const turn of collapsibleTurnIds) next.delete(turn)
      } else {
        for (const turn of collapsibleTurnIds) next.add(turn)
      }
      return next
    })
  }
  const handleToggleAllAssistants = () => {
    setCollapsedAssistants(current => {
      const next = new Set(current)
      if (allAssistantsCollapsed) {
        for (const id of collapsibleAssistantIds) next.delete(id)
      } else {
        for (const id of collapsibleAssistantIds) next.add(id)
      }
      return next
    })
  }
  const handleToggleTurn = (turn: number) => {
    setCollapsedTurns(current => {
      const next = new Set(current)
      if (next.has(turn)) next.delete(turn)
      else next.add(turn)
      return next
    })
  }
  const handleToggleAssistant = (id: string) => {
    setCollapsedAssistants(current => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  // Selected cell (cell-driven inspector — DSH-aligned)
  const selectedCell: TrajectoryCell | null = useMemo(() => {
    if (selectedIndex == null) return null
    return cells[selectedIndex] ?? null
  }, [selectedIndex, cells])

  // Matching per-iteration record for the selected assistant cell — used
  // for the 4-layer snapshot bonus tab (PrismClaw ≥ DSH richness).
  // Match by (turn, iteration); assistant cells carry iteration on the cell.
  const matchingRecord: RequestAuditRecord | null = useMemo(() => {
    if (!selectedCell) return null
    // Only assistant cells have a matching LLM iteration record
    if (selectedCell.kind !== 'message' || selectedCell.iteration == null) return null
    return visibleRecords.find(r =>
      r.turn === selectedCell.turn && r.iteration === selectedCell.iteration,
    ) ?? null
  }, [selectedCell, visibleRecords])

  // DSH RequestUsagePanel "Session cumulative": sum usage of every assistant
  // cell up to and including the selected one (frontend aggregate of what
  // the audit layer already persists — no backend change needed).
  const cumulativeUsage = useMemo<TrajectoryCumulativeUsage | null>(() => {
    if (!selectedCell || selectedCell.kind !== 'message') return null
    const acc = { input: 0, cacheRead: 0, cacheWrite: 0, output: 0, reasoning: 0 }
    for (const c of cells) {
      if (c.kind !== 'message' || c.seq > selectedCell.seq) continue
      acc.input += c.usage_input_tokens ?? 0
      acc.cacheRead += c.usage_cache_read_tokens ?? 0
      acc.cacheWrite += c.usage_cache_write_tokens ?? 0
      acc.output += c.usage_output_tokens ?? 0
      acc.reasoning += c.usage_reasoning_tokens ?? 0
    }
    return acc
  }, [cells, selectedCell])

  // Turn / cell counts for toolbar stats
  const turnCount = useMemo(() => {
    const turns = new Set(cells.map(c => c.turn).filter(t => t != null))
    return turns.size
  }, [cells])
  const recordCount = cells.length

  // DSH parity: incremental multi-term AND search index — drives BOTH the
  // timeline dimming (non-matches → 0.14) and the ledger row filter.
  // (ported TrajectorySearchIndex; entries reparse Markdown only when a
  // cell's sources actually change across refreshes)
  const searchIndexRef = useRef(new TrajectorySearchIndex())
  const searchMatchIds = useMemo<ReadonlySet<string> | null>(() => {
    if (!searchQuery.trim()) return null
    searchIndexRef.current.update(cells)
    return searchIndexRef.current.search(searchQuery)
  }, [cells, searchQuery])
  const searchMatchIndexes = useMemo<ReadonlySet<number> | null>(() => {
    if (searchMatchIds == null) return null
    const indexes = new Set<number>()
    for (let i = 0; i < cells.length; i++) {
      if (searchMatchIds.has(cells[i].cell_id)) indexes.add(i)
    }
    return indexes
  }, [cells, searchMatchIds])

  // DSH parity: focus indexes → ledger highlight for cells inside the active
  // timeline selection, computed in the ACTIVE MODE's projection domain
  // (ported trajectoryTimelineFocusIndexes, inclusive interval overlap).
  const focusIndexes = useMemo<ReadonlySet<number> | null>(() => {
    if (timelineRange == null) return null
    return trajectoryTimelineFocusIndexes(cells, timelineRange, timelineMode)
  }, [cells, timelineRange, timelineMode])

  return (
    <ModalShell
      open={open}
      onClose={onClose}
      title="运行轨迹"
      maxWidth="max-w-7xl"
      headerRight={
        <button
          type="button"
          onClick={() => { void refresh(); void refreshCells(); }}
          disabled={loading || cellsLoading}
          className="p-2 rounded-[var(--radius-control)] hover:bg-[var(--c-surface-2)] text-[var(--c-text-secondary)] hover:text-[var(--c-text)] transition-colors cursor-pointer disabled:opacity-50"
          title="刷新数据"
          aria-label="刷新"
        >
          <RefreshCw size={14} className={(loading || cellsLoading) ? 'animate-spin' : ''} />
        </button>
      }
    >
      {/* Error display */}
      {error && (
        <div className="m-3 p-3 rounded-[var(--radius-md)] bg-[var(--c-danger, #dc2626)]/10 border border-[var(--c-danger, #dc2626)]/30 text-[length:var(--fs-sm)] text-[var(--c-danger, #dc2626)]">
          {error}
        </div>
      )}

      {/* Empty state */}
      {!cells.length && !loading && !error && (
        <div className="p-12 text-center">
          <BarChart3 size={40} className="mx-auto mb-3 text-[var(--c-text-tertiary)] opacity-30" />
          <div className="text-[length:var(--fs-md)] text-[var(--c-text-secondary)]">
            暂无轨迹数据
          </div>
          {readWarning && (
            <div className="mt-2 text-[length:var(--fs-xs)] text-[var(--c-warning, #d97706)]">
              后端降级: {readWarning}
            </div>
          )}
        </div>
      )}

      {/* Data content — cells[] is the primary data source (DSH parity) */}
      {cells.length ? (
        <div className="flex flex-col h-[75vh]">
          {/* Toolbar */}
          <TrajectoryToolbar
            searchQuery={searchQuery}
            onSearchQueryChange={setSearchQuery}
            allTurnsCollapsed={allTurnsCollapsed}
            onToggleAllTurns={handleToggleAllTurns}
            allAssistantsCollapsed={allAssistantsCollapsed}
            onToggleAllAssistants={handleToggleAllAssistants}
            turnCount={turnCount}
            recordCount={recordCount}
            timelineMode={timelineMode}
            onTimelineModeChange={handleTimelineModeChange}
          />

          {/* Timeline — renders TrajectoryCell[] (per-conversation-event) */}
          <TrajectoryTimeline
            cells={cells}
            onSpanClick={handleSelect}
            selectedIndex={selectedIndex}
            range={timelineRange}
            onRangeChange={setTimelineRange}
            searchMatchIndexes={searchMatchIndexes}
            mode={timelineMode}
          />

          {/* Main content: Ledger + Inspector (resizable split, DSH parity) */}
          <div className="flex flex-1 overflow-hidden" ref={splitRef}>
            {/* Ledger */}
            <div className="flex-1 min-w-0 overflow-y-auto">
              <TrajectoryLedger
                cells={cells}
                selectedIndex={selectedIndex}
                onSelect={handleSelect}
                searchQuery={searchQuery}
                collapsedTurns={collapsedTurns}
                collapsedAssistants={collapsedAssistants}
                onToggleTurn={handleToggleTurn}
                onToggleAssistant={handleToggleAssistant}
                focusIndexes={focusIndexes}
                searchMatchIds={searchMatchIds}
              />
            </div>

            {/* Inspector toggle button */}
            {!inspectorOpen && (
              <button
                type="button"
                onClick={() => {
                  if (selectedIndex == null && cells.length > 0) {
                    setSelectedIndex(0)
                  }
                  setInspectorOpen(true)
                }}
                className="absolute right-4 bottom-4 p-2.5 rounded-[var(--radius-control)] bg-[var(--c-accent)] text-[var(--c-btn-primary-fg)] shadow-lg hover:bg-[var(--c-accent)]/90 transition-colors cursor-pointer flex items-center gap-2"
                title="打开详情面板"
              >
                <Eye size={14} />
                <span className="text-[length:var(--fs-xs)] font-medium">详情</span>
              </button>
            )}

            {/* Inspector — cell-driven (DSH-aligned), with matching record bonus.
                Width is draggable (DSH detailsResizeHandle): drag / arrow keys
                resize, double-click resets to the default half split. */}
            {inspectorOpen && (
              <>
                <div
                  role="separator"
                  aria-label="调整详情面板宽度"
                  aria-orientation="vertical"
                  tabIndex={0}
                  title="拖拽调整宽度，双击重置"
                  className="w-1 shrink-0 cursor-col-resize bg-[var(--c-border)] hover:bg-[var(--c-accent)]/60 transition-colors"
                  onDoubleClick={() => setInspectorWidth(null)}
                  onPointerDown={(event) => {
                    if (event.button !== 0) return
                    const split = splitRef.current
                    if (split === null) return
                    inspectorDragRef.current = {
                      pointerId: event.pointerId,
                      startX: event.clientX,
                      startWidth: split.getBoundingClientRect().width / 2,
                      splitWidth: split.getBoundingClientRect().width,
                    }
                    event.currentTarget.setPointerCapture(event.pointerId)
                    event.preventDefault()
                  }}
                  onPointerMove={(event) => {
                    const drag = inspectorDragRef.current
                    if (drag === null || drag.pointerId !== event.pointerId) return
                    setInspectorWidth(clampInspectorWidth(
                      drag.startWidth + drag.startX - event.clientX,
                      drag.splitWidth,
                    ))
                  }}
                  onPointerUp={(event) => {
                    if (inspectorDragRef.current?.pointerId !== event.pointerId) return
                    inspectorDragRef.current = null
                    event.currentTarget.releasePointerCapture(event.pointerId)
                  }}
                  onPointerCancel={() => {
                    inspectorDragRef.current = null
                  }}
                  onKeyDown={(event) => {
                    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
                    const split = splitRef.current
                    if (split === null) return
                    const splitWidth = split.getBoundingClientRect().width
                    const current = inspectorWidth ?? splitWidth / 2
                    setInspectorWidth(clampInspectorWidth(
                      current + (event.key === 'ArrowLeft' ? INSPECTOR_RESIZE_STEP : -INSPECTOR_RESIZE_STEP),
                      splitWidth,
                    ))
                    event.preventDefault()
                  }}
                />
                <div
                  className="border-l-0 overflow-hidden flex shrink-0"
                  style={inspectorWidth != null ? { width: inspectorWidth } : undefined}
                >
                  <TrajectoryInspector cell={selectedCell} record={matchingRecord} cumulativeUsage={cumulativeUsage} />
                </div>
              </>
            )}
          </div>
        </div>
      ) : null}

      {/* Loading state */}
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-[var(--c-surface)]/50">
          <div className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)] flex items-center gap-2">
            <RefreshCw size={14} className="animate-spin" />
            加载中...
          </div>
        </div>
      )}
    </ModalShell>
  )
}
