/**
 * 患者管理 · 顶部统计条
 *
 * 指标口径基于「上传报告」而非住院流程：建档患者 / 质子评估中 / 高危重点 / 今日上传报告。
 */

import { AlertTriangle, FileUp, FolderOpen, UserCheck, type LucideIcon } from 'lucide-react'
import { Panel } from '../../components'
import type { Tone } from '../../components'

interface StatTileProps {
  icon: LucideIcon
  value: string
  label: string
  sub: string
  tone: Tone
}

const TONE_TEXT: Record<Tone, string> = {
  accent: 'text-[var(--c-accent)]',
  success: 'text-[var(--c-success)]',
  warning: 'text-[var(--c-warning)]',
  danger: 'text-[var(--c-danger)]',
  purple: 'text-[var(--c-tag-purple-fg)]',
  neutral: 'text-[var(--c-text-secondary)]',
}

const TONE_BG: Record<Tone, string> = {
  accent: 'bg-[var(--c-accent-soft)]',
  success: 'bg-[var(--c-success-soft)]',
  warning: 'bg-[var(--c-warning-soft)]',
  danger: 'bg-[var(--c-danger-soft)]',
  purple: 'bg-[var(--c-tag-purple-bg)]',
  neutral: 'bg-[var(--c-surface-3)]',
}

function StatTile({ icon: Icon, value, label, sub, tone }: StatTileProps) {
  return (
    <Panel className="p-4 flex items-center gap-3.5">
      <div className={`h-10 w-10 rounded-lg flex items-center justify-center shrink-0 ${TONE_BG[tone]}`}>
        <Icon className={`h-5 w-5 ${TONE_TEXT[tone]}`} />
      </div>
      <div className="min-w-0">
        <p className="text-[length:var(--fs-xl)] font-bold mw-strong mw-num leading-none">{value}</p>
        <p className="text-[length:var(--fs-sm)] mw-muted mt-1 truncate">
          {label}
          <span className="mw-faint"> · {sub}</span>
        </p>
      </div>
    </Panel>
  )
}

/** 患者档案 + 报告统计（基于上传报告，非住院流程） */
export function PatientStatsRow({ stats }: { stats: import('../../types/view').PatientStats }) {
  const tiles: StatTileProps[] = [
    { icon: FolderOpen, value: String(stats.archivedPatients), label: '建档患者', sub: `本月 +${stats.archivedMonthlyDelta}`, tone: 'accent' },
    { icon: UserCheck, value: String(stats.reportsParsing), label: '质子评估中', sub: '来院咨询 · 待初筛', tone: 'success' },
    { icon: AlertTriangle, value: String(stats.highRisk), label: '高危重点', sub: '需优先关注', tone: 'danger' },
    { icon: FileUp, value: String(stats.reportsToday), label: '今日上传报告', sub: `已解析 ${stats.reportsParsed} · 解析中 ${stats.reportsParsing}`, tone: 'success' },
  ]

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      {tiles.map((t) => (
        <StatTile key={t.label} {...t} />
      ))}
    </div>
  )
}
