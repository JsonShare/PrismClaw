/**
 * TrajectoryPanel — 轨迹审计弹窗。
 *
 * Timeline + Ledger 渲染 TrajectoryCell[]（per-conversation-event，
 * 每个 tree 节点一个 cell），Inspector 也是 cell 驱动，展示选中 cell 的
 * content/tokens/timing/schema；assistant cell 附带按 turn+iteration
 * 匹配的 RequestAuditRecord 显示 4 层调用快照。
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

// Inspector 宽度常量
const INSPECTOR_MIN_WIDTH = 320
const INSPECTOR_MAX_WIDTH = 720
const LEDGER_MIN_WIDTH = 280
const INSPECTOR_RESIZE_STEP = 16

/** 将 Inspector 宽度限制在 [320, min(720, split − 280)] 范围内。 */
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
  // 数据获取 — cells（主键，per-conversation-event）+ records
  //（辅键，per-LLM-iteration，用于 assistant cell 的 4 层快照）
  const { data, readWarning, loading, error, refresh } = useTrajectory(sessionId, token, open, onUnauthorized)
  const { cells, loading: cellsLoading, refresh: refreshCells } = useTrajectoryCells(sessionId, token, open, onUnauthorized)

  // UI 状态 — selectedIndex 索引 cells[]（不是 records[]）
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null)
  const [inspectorOpen, setInspectorOpen] = useState(false)
  // 折叠状态在 Panel 层持有 — toolbar 的全折叠按钮状态由此派生
  const [collapsedTurns, setCollapsedTurns] = useState<ReadonlySet<number>>(new Set())
  const [collapsedAssistants, setCollapsedAssistants] = useState<ReadonlySet<string>>(new Set())
  // 时间线拖拽选区 + 搜索匹配索引，共同驱动 timeline 变暗 + ledger 高亮
  const [timelineRange, setTimelineRange] = useState<{ start: number; end: number } | null>(null)
  const [timelineMode, setTimelineMode] = useState<TrajectoryTimelineMode>('sequence')
  // Inspector 面板宽度：null = 默认对半分
  const [inspectorWidth, setInspectorWidth] = useState<number | null>(null)
  const splitRef = useRef<HTMLDivElement>(null)
  const inspectorDragRef = useRef<InspectorResizeDrag | null>(null)

  // 不同投影模式的选区互不通用 — 切换模式时清空选区
  const handleTimelineModeChange = (mode: TrajectoryTimelineMode) => {
    setTimelineMode(mode)
    setTimelineRange(null)
  }

  // Records — 丢弃孤儿行（防御性；后端现在在投影时已丢弃）
  const visibleRecords = useMemo(() => {
    if (!data?.records) return []
    return data.records.filter((r) => r.status !== 'orphaned')
  }, [data?.records])

  // cells 变化时同步选中索引（例如刷新后数组缩短）
  useEffect(() => {
    if (selectedIndex == null) return
    if (selectedIndex >= cells.length) {
      setSelectedIndex(null)
    }
  }, [cells.length, selectedIndex])

  // 处理 timeline span 点击 / ledger 行点击 — 两者都索引 cells[]，
  // 保持 timeline + ledger + inspector 三者同步
  const handleSelect = (index: number) => {
    setSelectedIndex(index)
    setInspectorOpen(true)
  }

  // 折叠控制：可折叠性从 cells 派生 —
  // turns 有 >1 条非 system 内容行，及 message cell 后紧跟 tool/subtool cell。
  // toolbar 按下态 = "每个可折叠的都已折叠"。
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

  // 选中 cell（cell 驱动 inspector）
  const selectedCell: TrajectoryCell | null = useMemo(() => {
    if (selectedIndex == null) return null
    return cells[selectedIndex] ?? null
  }, [selectedIndex, cells])

  // assistant cell 的匹配迭代记录 — 用于 4 层调用快照 tab。
  // 按 (turn, iteration) 匹配；assistant cell 自身携带 iteration。
  const matchingRecord: RequestAuditRecord | null = useMemo(() => {
    if (!selectedCell) return null
    // 只有 assistant cell 有对应的 LLM 迭代记录
    if (selectedCell.kind !== 'message' || selectedCell.iteration == null) return null
    return visibleRecords.find(r =>
      r.turn === selectedCell.turn && r.iteration === selectedCell.iteration,
    ) ?? null
  }, [selectedCell, visibleRecords])

  // 会话累计 usage：选中 cell 之前（含自身）所有 assistant cell 的 usage 汇总
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

  // toolbar 统计用 turn / cell 计数
  const turnCount = useMemo(() => {
    const turns = new Set(cells.map(c => c.turn).filter(t => t != null))
    return turns.size
  }, [cells])
  const recordCount = cells.length

  // 增量多词 AND 搜索索引 — 同时驱动 timeline 变暗（不匹配 → 0.14）和 ledger 行过滤
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

  // timeline 选区内的 cell 索引 → ledger 高亮，在当前投影模式下计算
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

      {/* 主体 — cells[] 是数据源 */}
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

          {/* Timeline — 渲染 TrajectoryCell[]（per-conversation-event） */}
          <TrajectoryTimeline
            cells={cells}
            onSpanClick={handleSelect}
            selectedIndex={selectedIndex}
            range={timelineRange}
            onRangeChange={setTimelineRange}
            searchMatchIndexes={searchMatchIndexes}
            mode={timelineMode}
          />

          {/* 主内容区：Ledger + Inspector（可拖拽分割） */}
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

            {/* Inspector — cell 驱动，可拖拽 / 方向键调整宽度，双击重置 */}
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
