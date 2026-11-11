/**
 * 病历档案 · 按医学用途分类 —— 三级树
 *
 * 文件夹（大类）→ 子类型 → 报告行
 * 大类使用 Panel 折叠面板，视觉与 MedicalSummary 卡片体系一致。
 * 点击报告行弹出文件预览（Lightbox），点击缩略图亦可。
 * 无树形边线，以缩进+图标区分层级。
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ChevronDown,
  Dna,
  FileText,
  FlaskConical,
  Folder,
  FolderOpen,
  ScanLine,
  Stethoscope,
  type LucideIcon,
} from 'lucide-react'
import { Pill } from '../../components'
import { AttachmentLightbox, AttachmentThumb } from './AttachmentViewer'
import { OTHER_CATEGORY, resolveReportType } from '../../lib/reportTypes'
import { REPORT_STATE_TONE } from '../../lib/tone'
import type { PatientReport } from '../../types/view'

// ── 大类定义 ──

interface Category {
  key: string
  label: string
  icon: LucideIcon
}

const CATEGORIES: Category[] = [
  { key: 'narrative', label: '病理与叙事文书', icon: FileText },
  { key: 'imaging', label: '影像', icon: ScanLine },
  { key: 'genomic', label: '分子与组学', icon: Dna },
  { key: 'lab', label: '检验', icon: FlaskConical },
  { key: 'treatment', label: '治疗记录', icon: Stethoscope },
  { key: OTHER_CATEGORY, label: '其他', icon: Folder },
]

export function ReportArchive({ reports }: { reports: PatientReport[] }) {
  const [openCats, setOpenCats] = useState<string[]>([])
  const [openSubs, setOpenSubs] = useState<string[]>([])
  const [preview, setPreview] = useState<{ report: PatientReport; index: number } | null>(null)
  const initialized = useRef(false)

  // 报告到位后默认展开最近一次就诊所在的大类与子类（只做一次）
  useEffect(() => {
    if (initialized.current || reports.length === 0) return
    initialized.current = true
    const latest = [...reports].sort((a, b) => (b.date || '').localeCompare(a.date || ''))[0]
    const { cat, label } = resolveReportType(latest.reportType)
    setOpenCats([cat])
    setOpenSubs([`${cat}/${label}`])
  }, [reports])

  // 大类 → 类型 → 报告
  const groups = useMemo(() => {
    const map = new Map<string, Map<string, PatientReport[]>>()
    for (const r of reports) {
      const { cat, label } = resolveReportType(r.reportType)
      if (!map.has(cat)) map.set(cat, new Map())
      const sub = map.get(cat)!
      if (!sub.has(label)) sub.set(label, [])
      sub.get(label)!.push(r)
    }
    return CATEGORIES.filter((c) => map.has(c.key)).map((c) => {
      const subs = [...map.get(c.key)!.entries()]
        .map(([label, items]) => ({
          label,
          items: [...items].sort((a, b) => (b.date || '').localeCompare(a.date || '')),
        }))
        .sort((a, b) => b.items.length - a.items.length)
      return { ...c, total: subs.reduce((n, s) => n + s.items.length, 0), subs }
    })
  }, [reports])

  const toggleCat = (key: string) =>
    setOpenCats((p) => (p.includes(key) ? p.filter((k) => k !== key) : [...p, key]))
  const toggleSub = (key: string) =>
    setOpenSubs((p) => (p.includes(key) ? p.filter((k) => k !== key) : [...p, key]))

  if (groups.length === 0) {
    return <p className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">暂无报告</p>
  }

  return (
    <>
      <div className="space-y-1.5">
        {groups.map((g) => {
          const catOpen = openCats.includes(g.key)
          const CatIcon = catOpen ? FolderOpen : g.icon
          return (
            <div key={g.key} className="rounded-lg">
              {/* 一级：医学用途大类 */}
              <button
                type="button"
                onClick={() => toggleCat(g.key)}
                className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer text-left"
              >
                <ChevronDown
                  className={`h-3.5 w-3.5 shrink-0 text-[var(--c-text-secondary)] transition-transform duration-200 ${
                    catOpen ? '' : '-rotate-90'
                  }`}
                />
                <CatIcon className="h-4 w-4 shrink-0 text-[var(--c-accent)]" />
                <span className="text-[length:var(--fs-md)] font-medium text-[var(--c-text)]">
                  {g.label}
                </span>
                <span className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)] tabular-nums">
                  {g.total} 份
                </span>
              </button>

              {/* 二级：具体类型 + 三级报告行 */}
              {catOpen && (
                <div className="px-3 pb-2 space-y-0.5">
                  {g.subs.map((s) => {
                    const subKey = `${g.key}/${s.label}`
                    const subOpen = openSubs.includes(subKey)
                    return (
                      <div key={subKey}>
                        <button
                          type="button"
                          onClick={() => toggleSub(subKey)}
                          className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-md hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer text-left"
                        >
                          <ChevronDown
                            className={`h-3 w-3 shrink-0 text-[var(--c-text-secondary)] transition-transform duration-200 ${
                              subOpen ? '' : '-rotate-90'
                            }`}
                          />
                          <span className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">
                            {s.label}
                          </span>
                          <span className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)] tabular-nums">
                            {s.items.length}
                          </span>
                        </button>

                        {/* 三级：报告行（可点击弹出预览）+ 文件缩略图 */}
                        {subOpen && (
                          <div className="ml-[18px] pl-2.5 space-y-1.5 pb-1">
                            {s.items.map((r) => (
                              <div key={r.id} className="rounded-md bg-white border border-[var(--c-border)]/60 px-2.5 py-1.5">
                                <button
                                  type="button"
                                  onClick={() => {
                                    if (r.attachments && r.attachments.length > 0) {
                                      setPreview({ report: r, index: 0 })
                                    }
                                  }}
                                  className="w-full flex items-center gap-2 min-w-0 text-left cursor-pointer"
                                >
                                  <span className="text-[length:var(--fs-sm)] text-[var(--c-text)] truncate flex-1 min-w-0">
                                    {r.name}
                                  </span>
                                  <span className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)] shrink-0 tabular-nums">
                                    {r.date}
                                  </span>
                                  <Pill tone={REPORT_STATE_TONE[r.state] ?? 'neutral'} className="shrink-0">
                                    {r.state}
                                  </Pill>
                                </button>

                                {/* 文件缩略图：多附件时显示，可点击切换页 */}
                                {(r.attachments?.length ?? 0) > 1 && (
                                  <div className="flex gap-1.5 mt-1.5 overflow-x-auto pb-0.5">
                                    {r.attachments.map((a, idx) => (
                                      <AttachmentThumb
                                        key={idx}
                                        reportId={r.id}
                                        index={idx}
                                        mime={a.mime}
                                        pageNo={a.page_no}
                                        onClick={() => setPreview({ report: r, index: idx })}
                                      />
                                    ))}
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* 文件预览灯箱 */}
      {preview && (
        <AttachmentLightbox
          report={preview.report}
          index={preview.index}
          onIndexChange={(i) => setPreview((p) => (p ? { ...p, index: i } : p))}
          onClose={() => setPreview(null)}
        />
      )}
    </>
  )
}
