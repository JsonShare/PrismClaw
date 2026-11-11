/**
 * 智能病历 · 指派抽屉（聚焦指派交互）
 *
 * 替代旧的「点选待指派文档 → 整列患者翻成可指派态 → 误点即归档」模式。
 * 这里是一张自包含浮层：左侧文档预览，右侧同名候选一键点选 + 搜索兜底，
 * 底部显式「确认归档 / 移动」按钮（未选目标时禁用），取消即关、绝不误提交。
 *
 * 当目标患者档案尚不存在时，抽屉内嵌「新建患者并归档」子视图：
 * 填写 姓名（必填）+ 证件类型 + 证件信息，提交后自动归档当前文档到新患者。
 *
 * 纯展示组件：真实请求由 onConfirm(patientId) / createPatient 交回外部；
 * 失败则抽屉保持打开并展示错误（不引入跨域 toast 依赖）。
 */

import { useEffect, useState } from 'react'
import { Check, UserPlus, X, Search } from 'lucide-react'
import { Panel, Btn, SearchInput } from '../../components'
import { listPatients, createPatient } from '../../api/client'
import { resolveReportType } from '../../lib/reportTypes'
import type { PatientProfile, PatientReport, CreatePatientPayload } from '../../types/view'

interface CandidateRef {
  id: string
  name: string
  gender?: string
  age?: number
}

interface AssignDrawerProps {
  open: boolean
  /** 待归档 / 待移动的文档 */
  report: PatientReport | null
  /** 同名候选（ambiguous 时由 inbox 直出）；move 模式通常为空 */
  candidates?: CandidateRef[]
  /** 'assign' = 待整理归档；'move' = 已归档报告改归其他患者 */
  mode: 'assign' | 'move'
  /** move 模式下当前归属患者 id，搜索与候选需排除它 */
  currentPatientId?: string
  /** 确认：返回 Promise，失败时抽屉保持打开并展示错误 */
  onConfirm: (patientId: string) => Promise<void>
  onClose: () => void
}

/** 候选/搜索结果 → 一行可点选患者 */
function PatientPickRow({
  p,
  selected,
  onPick,
}: {
  p: { id: string; name: string; gender?: string; age?: number; diagnosis?: string }
  selected: boolean
  onPick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onPick}
      className={[
        'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left transition-colors cursor-pointer',
        selected
          ? 'bg-[var(--c-accent-soft)] border border-[var(--c-accent)]'
          : 'bg-[var(--c-surface-2)] hover:bg-[var(--c-surface-3)]',
      ].join(' ')}
    >
      <div className="h-8 w-8 rounded-lg bg-[var(--c-surface-3)] flex items-center justify-center text-[length:var(--fs-sm)] font-semibold mw-muted shrink-0">
        {p.name.charAt(0)}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-[length:var(--fs-md)] font-semibold mw-strong truncate">{p.name}</span>
          {(p.gender || (p.age ?? 0) > 0) && (
            <span className="text-[length:var(--fs-xs)] mw-faint shrink-0">
              {[p.gender, (p.age ?? 0) > 0 ? `${p.age}岁` : ''].filter(Boolean).join(' · ')}
            </span>
          )}
        </div>
        {p.diagnosis ? (
          <div className="text-[length:var(--fs-xs)] mw-faint truncate mt-0.5">{p.diagnosis}</div>
        ) : null}
      </div>
      {selected && <Check className="h-4 w-4 text-[var(--c-accent)] shrink-0" />}
    </button>
  )
}

/** 证件类型可选项（与后端 id_type 取值约定一致） */
const ID_TYPE_OPTIONS = ['身份证', '护照', '港澳通行证', '台湾通行证', '军官证', '其他']

/** 输入框统一类名（复用令牌，避免引入新组件） */
const FIELD_INPUT =
  'w-full px-3 py-2 rounded-lg bg-[var(--c-surface-2)] ' +
  'text-[length:var(--fs-md)] mw-strong outline-none transition-colors ' +
  'focus:border-[var(--c-accent)] placeholder:mw-faint'

export function AssignDrawer({
  open,
  report,
  candidates,
  mode,
  currentPatientId,
  onConfirm,
  onClose,
}: AssignDrawerProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selectedName, setSelectedName] = useState('')
  const [keyword, setKeyword] = useState('')
  const [searchResults, setSearchResults] = useState<PatientProfile[]>([])
  const [searching, setSearching] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  // 新建患者子视图态
  const [showCreate, setShowCreate] = useState(false)
  const [newName, setNewName] = useState('')
  const [newIdType, setNewIdType] = useState('')
  const [newIdNumber, setNewIdNumber] = useState('')
  const [createSubmitting, setCreateSubmitting] = useState(false)
  const [createError, setCreateError] = useState('')

  const busy = submitting || createSubmitting

  // 打开 / 换文档时重置内部态
  useEffect(() => {
    if (open) {
      setSelectedId(null)
      setSelectedName('')
      setKeyword('')
      setSearchResults([])
      setSearching(false)
      setSubmitting(false)
      setError('')
      setShowCreate(false)
      setNewName('')
      setNewIdType('')
      setNewIdNumber('')
      setCreateSubmitting(false)
      setCreateError('')
    }
  }, [open, report?.id])

  // Esc 关闭（提交中禁止）
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, busy, onClose])

  // 搜索（去抖 250ms），排除当前归属患者
  useEffect(() => {
    if (!open || showCreate) return
    const kw = keyword.trim()
    if (!kw) {
      setSearchResults([])
      return
    }
    const t = window.setTimeout(async () => {
      setSearching(true)
      try {
        const paged = await listPatients({ keyword: kw, pageSize: 20 })
        const filtered = paged.items.filter((p) => p.id !== currentPatientId)
        setSearchResults(filtered)
      } finally {
        setSearching(false)
      }
    }, 250)
    return () => window.clearTimeout(t)
  }, [keyword, open, showCreate, currentPatientId])

  if (!open || !report) return null

  const pick = (id: string, name: string) => {
    setSelectedId(id)
    setSelectedName(name)
    setError('')
  }

  const handleConfirm = async () => {
    if (!selectedId || !report || submitting) return
    setSubmitting(true)
    setError('')
    try {
      await onConfirm(selectedId)
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败，请重试')
    } finally {
      setSubmitting(false)
    }
  }

  const handleCreate = async () => {
    const name = newName.trim()
    if (!name || !report || createSubmitting) return
    setCreateSubmitting(true)
    setCreateError('')
    try {
      const payload: CreatePatientPayload = {
        name,
        idType: newIdType || undefined,
        idNumber: newIdNumber.trim() || undefined,
      }
      const created = await createPatient(payload)
      await onConfirm(created.id)
      onClose()
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : '创建失败，请重试')
    } finally {
      setCreateSubmitting(false)
    }
  }

  if (showCreate) {
    const canCreate = newName.trim().length > 0 && !createSubmitting
    return (
      <div className="mw-overlay" onClick={() => { if (!createSubmitting) onClose() }}>
        <Panel
          className="max-w-lg w-full overflow-hidden p-0"
          onClick={(e) => e.stopPropagation()}
          role="dialog"
          aria-modal="true"
          aria-label="新建患者并归档"
        >
          <div className="flex items-start justify-between gap-3 px-5 pt-5 pb-3 border-b border-[var(--c-border)]">
            <div className="min-w-0">
              <h3 className="text-[length:var(--fs-lg)] font-bold mw-strong">新建患者并归档</h3>
              <p className="text-[length:var(--fs-sm)] mw-faint mt-0.5 truncate">
                新建「{newName.trim() || '—'}」后，立即将「{report.name}」归档到该档案
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              disabled={createSubmitting}
              className="rounded-full p-1.5 mw-faint hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer disabled:opacity-40"
              aria-label="关闭"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="px-5 py-4 overflow-y-auto" style={{ maxHeight: '60vh' }}>
            <div className="flex flex-col gap-4">
              <div>
                <label className="block text-[length:var(--fs-sm)] mw-strong mb-1.5">
                  姓名 <span className="text-[var(--c-danger)]">*</span>
                </label>
                <input
                  className={FIELD_INPUT}
                  value={newName}
                  placeholder="请输入患者姓名"
                  onChange={(e) => { setNewName(e.currentTarget.value); setCreateError('') }}
                  autoFocus
                />
              </div>

              <div>
                <label className="block text-[length:var(--fs-sm)] mw-strong mb-1.5">证件类型</label>
                <select
                  className={FIELD_INPUT}
                  value={newIdType}
                  onChange={(e) => setNewIdType(e.currentTarget.value)}
                >
                  <option value="">请选择证件类型（可选）</option>
                  {ID_TYPE_OPTIONS.map((opt) => (
                    <option key={opt} value={opt}>{opt}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-[length:var(--fs-sm)] mw-strong mb-1.5">证件信息</label>
                <input
                  className={FIELD_INPUT}
                  value={newIdNumber}
                  placeholder={newIdType ? `${newIdType}号码` : '证件号码（将脱敏存储）'}
                  onChange={(e) => setNewIdNumber(e.currentTarget.value)}
                />
                <p className="text-[length:var(--fs-xs)] mw-faint mt-1.5 leading-relaxed">
                  证件号仅脱敏（保留前 6 位与后 4 位）后存储，原文不落库。
                </p>
              </div>

              {createError && (
                <span className="text-[length:var(--fs-sm)] text-[var(--c-danger)]">{createError}</span>
              )}
            </div>
          </div>

          <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-[var(--c-border)]">
            <Btn variant="glass" onClick={() => { setShowCreate(false); setCreateError('') }} disabled={createSubmitting}>
              返回
            </Btn>
            <Btn variant="primary" onClick={() => void handleCreate()} disabled={!canCreate}>
              {createSubmitting ? (
                <span className="flex items-center gap-1.5">
                  <span className="mw-pqf-spinner" style={{ width: 14, height: 14, borderWidth: 2 }} />
                  创建中
                </span>
              ) : (
                '创建并归档'
              )}
            </Btn>
          </div>
        </Panel>
      </div>
    )
  }

  const title = mode === 'assign' ? '归档到患者' : '移动报告'
  const confirmLabel = mode === 'assign' ? '确认归档' : '移动到该患者'
  const showCandidates = mode === 'assign' && (candidates?.length ?? 0) > 0
  const hasResults = searchResults.length > 0

  return (
    <div className="mw-overlay" onClick={() => { if (!submitting) onClose() }}>
      <Panel
        className="max-w-lg w-full overflow-hidden p-0"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        {/* 头 */}
        <div className="flex items-start justify-between gap-3 px-5 pt-5 pb-3 border-b border-[var(--c-border)]">
          <div className="min-w-0">
            <h3 className="text-[length:var(--fs-lg)] font-bold mw-strong">{title}</h3>
            <p className="text-[length:var(--fs-sm)] mw-faint mt-0.5 truncate">
              {mode === 'assign' ? '将' : '将'}「{report.name}」{mode === 'assign' ? '归档到患者' : '改归其他患者'}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="rounded-full p-1.5 mw-faint hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer disabled:opacity-40"
            aria-label="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* 文档预览 */}
        <div className="px-5 pt-4">
          <div className="rounded-lg bg-[var(--c-surface-2)] p-3">
            <div className="flex items-center gap-2">
              <span className="mw-pill mw-tone-accent">{resolveReportType(report.reportType).label}</span>
              <span className="text-[length:var(--fs-md)] font-semibold mw-strong truncate">{report.name}</span>
            </div>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1.5 text-[length:var(--fs-xs)] mw-faint">
              {report.visitDate || report.date ? (
                <span>{report.visitDate || report.date}</span>
              ) : null}
              {report.hospitalName ? <span>· {report.hospitalName}</span> : null}
              {report.patientName && report.patientName !== report.id ? (
                <span className="text-[var(--c-accent)]">· 疑似 {report.patientName}</span>
              ) : (
                <span>· 未识别患者</span>
              )}
            </div>
            {report.summary ? (
              <p className="text-[length:var(--fs-sm)] mw-muted mt-2 line-clamp-2">{report.summary}</p>
            ) : null}
          </div>
        </div>

        {/* 主体：候选 + 搜索 */}
        <div className="px-5 py-4 overflow-y-auto" style={{ maxHeight: '52vh' }}>
          {showCandidates && (
            <div className="mb-4">
              <div className="text-[length:var(--fs-xs)] mw-faint tracking-wider mb-2 px-1">
                同名候选（{candidates!.length}）
              </div>
              <div className="flex flex-col gap-1.5">
                {candidates!.map((c) => (
                  <PatientPickRow
                    key={c.id}
                    p={c}
                    selected={selectedId === c.id}
                    onPick={() => pick(c.id, c.name)}
                  />
                ))}
              </div>
            </div>
          )}

          <div className="mb-2">
            <SearchInput
              value={keyword}
              onChange={(v) => {
                setKeyword(v)
                setError('')
              }}
              placeholder={mode === 'assign' ? '都不是？搜索其他患者' : '搜索目标患者'}
            />
          </div>

          {searching && (
            <div className="flex items-center gap-2 px-1 py-2 text-[length:var(--fs-sm)] mw-faint">
              <span className="mw-pqf-spinner" style={{ width: 16, height: 16, borderWidth: 2 }} />
              搜索中…
            </div>
          )}

          {!searching && hasResults && (
            <div className="flex flex-col gap-1.5">
              {searchResults.map((p) => (
                <PatientPickRow
                  key={p.id}
                  p={p}
                  selected={selectedId === p.id}
                  onPick={() => pick(p.id, p.name)}
                />
              ))}
            </div>
          )}

          {!searching && !hasResults && !showCandidates && (
            <div className="assign-empty">
              <Search className="h-7 w-7 opacity-30" />
              <p className="text-[length:var(--fs-sm)] mw-faint m-0 leading-relaxed">
                没有同名候选。可在上方输入姓名搜索患者；若患者档案尚不存在，可点击下方「新建患者并归档」。
              </p>
            </div>
          )}

          {!searching && !hasResults && showCandidates && (
            <div className="assign-empty">
              <Search className="h-7 w-7 opacity-30" />
              <p className="text-[length:var(--fs-sm)] mw-faint m-0 leading-relaxed">
                没有匹配的搜索结果，换个关键词试试。
              </p>
            </div>
          )}

          <button
            type="button"
            onClick={() => { setShowCreate(true); setError('') }}
            className="mt-3 flex items-center gap-2 text-[length:var(--fs-sm)] text-[var(--c-accent)] hover:underline cursor-pointer"
          >
            <UserPlus className="h-4 w-4" />
            新建患者并归档
          </button>
        </div>

        {/* 脚：取消 / 确认 */}
        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-[var(--c-border)]">
          {error && (
            <span className="mr-auto text-[length:var(--fs-sm)] text-[var(--c-danger)] truncate">{error}</span>
          )}
          <Btn variant="glass" onClick={onClose} disabled={submitting}>
            取消
          </Btn>
          <Btn variant="primary" onClick={() => void handleConfirm()} disabled={!selectedId || submitting}>
            {submitting ? (
              <span className="flex items-center gap-1.5">
                <span className="mw-pqf-spinner" style={{ width: 14, height: 14, borderWidth: 2 }} />
                处理中
              </span>
            ) : selectedId ? (
              `${confirmLabel}${selectedName ? ` · ${selectedName}` : ''}`
            ) : (
              '请选择患者'
            )}
          </Btn>
        </div>
      </Panel>
    </div>
  )
}
