/**
 * 简报卡片 — 列表视图中的单张简报条目
 *
 * 展示：状态徽标 + 版本 + 时间 + 操作按钮（重新分析/删除）
 */

import { useState } from 'react'
import { FileCheck, AlertTriangle, Trash2, RotateCcw, Loader2 } from 'lucide-react'
import { reanalyzeBrief, deleteBrief } from '../../api/client'
import type { BriefListItem, BriefStatus } from '../../types/view'

interface BriefCardProps {
  brief: BriefListItem
  onClick: () => void
  onRefresh: () => void
}

const STATUS_CONFIG: Record<BriefStatus, { icon: React.ReactNode; label: string; tone: string }> = {
  '分析中': {
    icon: <Loader2 className="h-3.5 w-3.5 animate-spin" />,
    label: '分析中',
    tone: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
  },
  '已通过': {
    icon: <FileCheck className="h-3.5 w-3.5" />,
    label: '已通过',
    tone: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300',
  },
  '待修正': {
    icon: <AlertTriangle className="h-3.5 w-3.5" />,
    label: '待修正',
    tone: 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
  },
  '已废弃': {
    icon: <Trash2 className="h-3.5 w-3.5" />,
    label: '已废弃',
    tone: 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400',
  },
}

export function BriefCard({ brief, onClick, onRefresh }: BriefCardProps) {
  const [loading, setLoading] = useState(false)
  const cfg = STATUS_CONFIG[brief.status] ?? STATUS_CONFIG['待修正']

  const handleReanalyze = async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (brief.status === '分析中') return
    setLoading(true)
    try {
      await reanalyzeBrief(brief.id)
      onRefresh()
    } catch { /* 静默 */ }
    finally { setLoading(false) }
  }

  const handleDelete = async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (!confirm('确定删除该简报？')) return
    setLoading(true)
    try {
      await deleteBrief(brief.id)
      onRefresh()
    } catch { /* 静默 */ }
    finally { setLoading(false) }
  }

  const timeStr = brief.finishedAt
    ? new Date(brief.finishedAt).toLocaleDateString('zh-CN')
    : brief.startedAt
      ? new Date(brief.startedAt).toLocaleDateString('zh-CN')
      : ''

  return (
    <div
      className="group flex items-start gap-3 p-3 rounded-lg bg-[var(--c-surface-2)] hover:bg-[var(--c-surface-3)] cursor-pointer transition-colors"
      onClick={onClick}
    >
      {/* 状态徽标 */}
      <div className={`shrink-0 mt-0.5 inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[length:var(--fs-xs)] font-medium ${cfg.tone}`}>
        {cfg.icon}
        {cfg.label}
      </div>

      {/* 主体 */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-[length:var(--fs-md)] font-medium text-[var(--c-text)]">
            研究简报
          </span>
          <span className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]">
            v{brief.version}
          </span>
        </div>
        <p className="mt-0.5 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">
          {brief.reportIds.length} 份报告{timeStr ? ` · ${timeStr}` : ''}
        </p>
      </div>

      {/* 操作按钮 */}
      <div className="shrink-0 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        {brief.status !== '分析中' && brief.status !== '已废弃' && (
          <button
            type="button"
            className="p-1.5 rounded-md hover:bg-[var(--c-surface-2)] text-[var(--c-text-secondary)] hover:text-[var(--c-text)] transition-colors"
            onClick={handleReanalyze}
            disabled={loading}
            title="重新分析"
          >
            {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
          </button>
        )}
        <button
          type="button"
          className="p-1.5 rounded-md hover:bg-red-100 dark:hover:bg-red-900/20 text-[var(--c-text-secondary)] hover:text-red-600 transition-colors"
          onClick={handleDelete}
          disabled={loading}
          title="删除"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  )
}
