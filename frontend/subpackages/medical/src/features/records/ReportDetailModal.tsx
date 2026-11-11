/**
 * 智能病历 · 报告详情弹窗
 *
 * 右侧报告列表点任意一条 → 弹窗看详情，列表本身不再内联展开（保持页面简约）。
 * 修正编辑态（AI 摘要 / 报告原文）与原件灯箱都收敛在本弹窗内：
 * 它们只可能发生在「正在看这份报告」的时候，故编辑态 state 下沉到本组件，
 * 页面层不再持有 draft/编辑态，也不再需要在换报告时手动清理。
 *
 * 关闭入口：右上角 X、点击遮罩、Esc（灯箱打开时 Esc 让位给灯箱）。
 */

import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { Panel, useToast } from '../../components'
import { correctReport } from '../../api/client'
import { flattenNarrative } from '../../lib/format'
import { resolveReportType } from '../../lib/reportTypes'
import { reportFlag } from '../../lib/review'
import { StructuredData } from './StructuredData'
import { AttachmentLightbox } from './AttachmentViewer'
import type { NarrativeSection, PatientReport } from '../../types/view'

type EditTarget = 'summary' | 'raw' | null

/** 报告原文元信息草稿的初始形状（机构 / 科室 / 日期 / 医师） */
const EMPTY_META = { hospitalName: '', doctorName: '', department: '', visitDate: '' }
type MetaDraft = typeof EMPTY_META

/** 报告原文头部四个可修正字段（顺序照设计稿） */
const META_FIELDS: { key: keyof MetaDraft; label: string }[] = [
  { key: 'hospitalName', label: '机构' },
  { key: 'department', label: '科室' },
  { key: 'visitDate', label: '日期' },
  { key: 'doctorName', label: '医师' },
]

/** 报告元信息（机构 / 科室 / 日期 / 医师）从报告对象收敛为可编辑草稿形状 */
function reportMeta(r: PatientReport | null): MetaDraft {
  return {
    hospitalName: r?.hospitalName ?? '',
    doctorName: r?.doctorName ?? '',
    department: r?.department ?? '',
    visitDate: r?.visitDate || r?.date || '',
  }
}

interface ReportDetailModalProps {
  report: PatientReport
  onClose: () => void
  /** 修正保存成功后回报更新后的报告，供列表同步刷新 */
  onSaved?: (updated: PatientReport) => void
}

export function ReportDetailModal({ report, onClose, onSaved }: ReportDetailModalProps) {
  const { toast } = useToast()

  const [editing, setEditing] = useState<EditTarget>(null)
  const [saving, setSaving] = useState(false)
  const [draftSections, setDraftSections] = useState<NarrativeSection[]>([])
  const [draftSummary, setDraftSummary] = useState('')
  const [draftRaw, setDraftRaw] = useState('')
  const [draftMeta, setDraftMeta] = useState(EMPTY_META)
  /**
   * 进入编辑态那一刻的元信息快照：① 作为 contentEditable 的渲染源（编辑期间恒定，
   * React 不会改写浏览器正在编辑的 DOM）；② 保存时比对，只提交真正改动过的字段。
   */
  const [metaSeed, setMetaSeed] = useState(EMPTY_META)
  const [lightbox, setLightbox] = useState<number | null>(null)

  // ── 派生
  const sections = flattenNarrative(report.narrative)
  const typeLabel = resolveReportType(report.reportType).label
  const hasAtt = (report.attachments?.length ?? 0) > 0
  const flag = reportFlag(report)
  const metaView: MetaDraft = editing === 'raw' ? metaSeed : reportMeta(report)

  // Esc 关闭；灯箱打开时让位给灯箱自己的 Esc 处理
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && lightbox === null) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, lightbox])

  const startEdit = (t: Exclude<EditTarget, null>) => {
    setDraftSections(sections.map((s) => ({ ...s })))
    setDraftSummary(report.summary ?? '')
    setDraftRaw(report.rawText ?? '')
    const meta = reportMeta(report)
    setDraftMeta(meta)
    setMetaSeed(meta)
    setEditing(t)
  }

  const saveEdit = async () => {
    setSaving(true)
    try {
      const metaPatch: Partial<MetaDraft> = {}
      for (const k of Object.keys(metaSeed) as (keyof MetaDraft)[]) {
        if (draftMeta[k] !== metaSeed[k]) metaPatch[k] = draftMeta[k]
      }
      const patch = editing === 'raw'
        ? { rawText: draftRaw, ...metaPatch }
        : { narrative: draftSections, summary: draftSummary }
      const updated = await correctReport(report.patientId, report.id, patch)
      setEditing(null)
      onSaved?.(updated)
    } catch {
      toast('保存失败，请稍后重试', 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <div className="mw-overlay" onClick={onClose}>
        <Panel
          className="report-detail-dialog max-w-3xl w-full max-h-[86vh] flex flex-col overflow-hidden"
          onClick={(e) => e.stopPropagation()}
          role="dialog"
          aria-modal="true"
          aria-label={report.name || typeLabel}
        >
          {/* 弹头：报告名 + 元信息 + 关闭 */}
          <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-[var(--c-border)]">
            <div className="min-w-0">
              <h3 className="text-[length:var(--fs-lg)] font-bold mw-strong truncate">
                {report.name || typeLabel}
              </h3>
              <div className="mt-1 flex items-center flex-wrap gap-x-1.5 text-[length:var(--fs-sm)] mw-faint">
                <span>{typeLabel}</span>
                <span>·</span>
                <span>{report.visitDate || report.date || '—'}</span>
                <span>·</span>
                <span>{report.doctorName || '—'}</span>
                <span>·</span>
                <span>{report.patientName}</span>
              </div>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="shrink-0 rounded-full p-1.5 mw-faint hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer"
              aria-label="关闭"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* 弹体：详情（AI 摘要 / 结构化数据 / 报告原文），内部滚动 */}
          <div className="flex-1 min-h-0 overflow-y-auto px-5 py-4">
            {/* 行内标在这里展开成一句话：为什么需要人看、要看哪里。
                否则时间轴上的「需核验」只是个无处兑现的提示。 */}
            {flag && flag.tone !== 'info' && (
              <div className={`rd-flag ${flag.tone}`}>
                <span className="rd-flag-tag">{flag.text}</span>
                <span className="rd-flag-text">{flag.hint}</span>
              </div>
            )}
            {/* AI 摘要 */}
            <div className={`section${editing === 'summary' ? ' editing' : ''}`}>
              <div className="section-head">
                <div className="section-icon blue">AI</div>
                <span className="section-title">AI 摘要</span>
                {editing !== 'summary' && (
                  <button className="section-edit-trigger" onClick={() => startEdit('summary')}>
                    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M13.5 2.5l-.5-.5a1.4 1.4 0 00-2 0L4 9l-1 4 4-1 7-7a1.4 1.4 0 000-2z" />
                      <path d="M10 5l2 2" />
                    </svg>
                    修正
                  </button>
                )}
              </div>

              <div className="kv-grid">
                {sections.map((s, idx) => (
                  // key 里带编辑态：取消编辑时强制重挂载，丢弃未保存的手改内容
                  <span key={`${idx}-${editing === 'summary' ? 'edit' : 'view'}`} style={{ display: 'contents' }}>
                    <span className="kv-key">{s.heading}</span>
                    <span
                      className="kv-val"
                      contentEditable={editing === 'summary'}
                      suppressContentEditableWarning
                      onInput={(e) => {
                        // 必须先把值取出来再 setState：updater 可能延迟到渲染阶段才执行，
                        // 那时合成事件的 currentTarget 已被置 null（整页崩溃）
                        const body = e.currentTarget.textContent ?? ''
                        setDraftSections((prev) => prev.map((it, i) => (i === idx ? { ...it, body } : it)))
                      }}
                    >
                      {s.body}
                    </span>
                  </span>
                ))}
                {sections.length === 0 && (
                  <span className="kv-val">尚未识别出结构化字段</span>
                )}
              </div>

              <div className="ai-note">
                <span className="ai-note-label">AI 建议</span>
                <span
                  key={editing === 'summary' ? 'edit' : 'view'}
                  className="ai-note-text"
                  contentEditable={editing === 'summary'}
                  suppressContentEditableWarning
                  onInput={(e) => { const v = e.currentTarget.textContent ?? ''; setDraftSummary(v) }}
                >
                  {report.summary || '—'}
                </span>
              </div>

              <div className="section-edit-bar">
                <span className="edit-bar-hint">点击字段即可编辑</span>
                <button className="edit-bar-cancel" onClick={() => setEditing(null)}>取消</button>
                <button className="edit-bar-save" onClick={() => void saveEdit()} disabled={saving}>
                  {saving ? '保存中…' : '保存修改'}
                </button>
              </div>
            </div>

            {/* 结构化数据（HIS / 上传解析共享同一套原生呈现路径） */}
            <StructuredData report={report} />

            {/* 报告原文 */}
            <div className={`section raw-section${editing === 'raw' ? ' editing' : ''}`}>
              <div className="section-head">
                <div className="section-icon amber">原</div>
                <span className="section-title">报告原文</span>
                {editing !== 'raw' && hasAtt && (
                  <button className="action-btn action-btn-archive" onClick={() => setLightbox(0)}>
                    查看原件
                  </button>
                )}
                {editing !== 'raw' && (
                  <button className="section-edit-trigger" onClick={() => startEdit('raw')}>
                    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M13.5 2.5l-.5-.5a1.4 1.4 0 00-2 0L4 9l-1 4 4-1 7-7a1.4 1.4 0 000-2z" />
                      <path d="M10 5l2 2" />
                    </svg>
                    修正
                  </button>
                )}
              </div>

              <div className="raw-body">
                <div className="raw-doc">
                  <div className="raw-doc-title">{report.name || typeLabel}</div>
                  <div className="raw-doc-meta">
                    <span className="raw-doc-meta-item">姓名：{report.patientName || '—'}</span>
                    {META_FIELDS.map((f) => (
                      // key 里带上编辑态：退出编辑时强制重挂载，
                      // 把 contentEditable 里未保存的手改内容还原成库里的值
                      <span
                        key={`${editing === 'raw' ? 'edit' : 'view'}-${f.key}`}
                        className="raw-doc-meta-item"
                        contentEditable={editing === 'raw'}
                        suppressContentEditableWarning
                        onInput={(e) => {
                          const v = (e.currentTarget.textContent ?? '').replace(
                            new RegExp('^' + f.label + '：'),
                            '',
                          )
                          setDraftMeta((m) => ({ ...m, [f.key]: v }) as MetaDraft)
                        }}
                      >
                        {f.label}：{metaView[f.key] || '—'}
                      </span>
                    ))}
                  </div>
                  {report.rawText ? (
                    <div
                      // key 里带编辑态：取消编辑时强制重挂载，丢弃未保存的手改内容
                      key={`${report.id}-${editing === 'raw' ? 'edit' : 'view'}`}
                      contentEditable={editing === 'raw'}
                      suppressContentEditableWarning
                      onInput={(e) => { const v = e.currentTarget.innerText ?? ''; setDraftRaw(v) }}
                    >
                      {report.rawText.split('\n').filter((l) => l.trim()).map((line, i) => (
                        <div className="raw-doc-p" key={i}>{line}</div>
                      ))}
                    </div>
                  ) : (
                    <div className="raw-doc-p">暂无原文内容</div>
                  )}
                </div>
                <div className="section-edit-bar">
                  <span className="edit-bar-hint">点击字段即可编辑</span>
                  <button className="edit-bar-cancel" onClick={() => setEditing(null)}>取消</button>
                  <button className="edit-bar-save" onClick={() => void saveEdit()} disabled={saving}>
                    {saving ? '保存中…' : '保存修改'}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </Panel>
      </div>

      {/* 灯箱渲染在遮罩之外：否则点灯箱会冒泡到 .mw-overlay 把弹窗一起关掉 */}
      {lightbox !== null && (
        <AttachmentLightbox
          report={report}
          index={lightbox}
          onIndexChange={setLightbox}
          onClose={() => setLightbox(null)}
        />
      )}
    </>
  )
}
