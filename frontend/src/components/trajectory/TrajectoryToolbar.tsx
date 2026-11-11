/**
 * TrajectoryToolbar — Search, timeline-mode and collapse controls.
 * Inspired by DSH's TrajectoryToolbar: DSH exposes a single Duration toggle
 * (sequence ↔ duration). We expose the full 3-state projection switch
 * (sequence / duration-compressed / actual) — ≥ DSH.
 */
import { Search, Minus, Plus } from 'lucide-react'
import type { TrajectoryTimelineMode } from './lib/timeline'

interface TrajectoryToolbarProps {
  searchQuery: string
  onSearchQueryChange: (query: string) => void
  /** Derived: every collapsible turn is folded (DSH toggleAllTurns pressed state). */
  allTurnsCollapsed: boolean
  onToggleAllTurns: () => void
  /** Derived: every collapsible assistant's tool calls are folded (DSH toggleAllAssistants). */
  allAssistantsCollapsed: boolean
  onToggleAllAssistants: () => void
  turnCount: number
  recordCount: number
  timelineMode: TrajectoryTimelineMode
  onTimelineModeChange: (mode: TrajectoryTimelineMode) => void
}

const MODE_OPTIONS: { value: TrajectoryTimelineMode; label: string; title: string }[] = [
  { value: 'sequence', label: '顺序', title: '等宽投影：每条记录占一个单位（DSH sequence）' },
  { value: 'duration', label: '时长', title: '真实时长·压缩空闲（DSH duration）' },
  { value: 'actual', label: '实时', title: '真实时间轴·含空闲（DSH actual）' },
]

export function TrajectoryToolbar({
  searchQuery,
  onSearchQueryChange,
  allTurnsCollapsed,
  onToggleAllTurns,
  allAssistantsCollapsed,
  onToggleAllAssistants,
  turnCount,
  recordCount,
  timelineMode,
  onTimelineModeChange,
}: TrajectoryToolbarProps) {
  return (
    <div
      className="flex items-center gap-3 px-3 py-2 border-b border-[var(--c-border)] bg-[var(--c-surface)]"
      role="toolbar"
      aria-label="轨迹工具栏"
    >
      {/* Collapse / expand all turns (DSH toolbar "Turns" — pressed = all collapsible folded) */}
      <button
        type="button"
        onClick={onToggleAllTurns}
        className="btn btn-sm btn-ghost"
        title={allTurnsCollapsed ? '展开所有回合' : '折叠所有回合'}
        aria-label={allTurnsCollapsed ? '展开所有回合' : '折叠所有回合'}
        aria-pressed={allTurnsCollapsed}
      >
        {allTurnsCollapsed ? <Plus size={13} /> : <Minus size={13} />}
        <span>回合</span>
      </button>

      {/* Collapse / expand all tool-call runs (DSH toolbar "Calls") */}
      <button
        type="button"
        onClick={onToggleAllAssistants}
        className="btn btn-sm btn-ghost"
        title={allAssistantsCollapsed ? '展开所有工具调用' : '折叠所有工具调用'}
        aria-label={allAssistantsCollapsed ? '展开所有工具调用' : '折叠所有工具调用'}
        aria-pressed={allAssistantsCollapsed}
      >
        {allAssistantsCollapsed ? <Plus size={13} /> : <Minus size={13} />}
        <span>调用</span>
      </button>

      {/* Timeline projection mode — DSH Duration toggle, extended 3-state */}
      <div
        className="flex items-center rounded-[var(--radius-md)] border border-[var(--c-border)] overflow-hidden"
        role="group"
        aria-label="时间线投影模式"
      >
        {MODE_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            type="button"
            onClick={() => onTimelineModeChange(opt.value)}
            title={opt.title}
            aria-pressed={timelineMode === opt.value}
            className={`px-2 py-1 text-[length:var(--fs-xs)] font-medium transition-colors cursor-pointer ${
              timelineMode === opt.value
                ? 'bg-[var(--c-accent)]/12 text-[var(--c-accent)]'
                : 'text-[var(--c-text-secondary)] hover:bg-[var(--c-surface-2)] hover:text-[var(--c-text)]'
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {/* Stats */}
      <div className="flex items-center gap-1 text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]">
        <span className="px-1.5 py-0.5 rounded-[var(--radius-control)] bg-[var(--c-surface-2)]">
          {turnCount} 回合
        </span>
        <span className="text-[var(--c-text-tertiary)]">·</span>
        <span className="px-1.5 py-0.5 rounded-[var(--radius-control)] bg-[var(--c-surface-2)]">
          {recordCount} 次迭代
        </span>
      </div>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Search */}
      <div className="relative">
        <Search
          size={12}
          className="absolute left-2 top-1/2 -translate-y-1/2 text-[var(--c-text-secondary)]"
        />
        <input
          type="search"
          placeholder="搜索模型 / 错误 / 状态…"
          value={searchQuery}
          onChange={(e) => onSearchQueryChange(e.target.value)}
          className="w-52 pl-7 pr-2 py-1 rounded-[var(--radius-md)] border border-[var(--c-border)] bg-[var(--c-surface-2)] text-[length:var(--fs-xs)] text-[var(--c-text)] placeholder:text-[var(--c-text-secondary)] focus:outline-none focus:border-[var(--c-accent)]/60 transition-colors"
          aria-label="搜索轨迹"
        />
      </div>
    </div>
  )
}
