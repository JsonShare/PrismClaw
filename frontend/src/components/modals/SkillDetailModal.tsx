import { useState } from 'react'
import { X, Trash2, Pencil, Check, Loader2 } from 'lucide-react'
import type { Skill } from '../../types/skill'
import MarkdownRenderer from '../chat/MarkdownRenderer'
import { PrimaryButton } from '../ui/PrimaryButton'

export interface SkillEditPatch {
  description?: string
  body?: string
}

interface Props {
  open: boolean
  skill: Skill | null
  onClose: () => void
  /** 删除回调：返回 true 表示成功（调用方负责关闭弹窗） */
  onDelete?: (skillId: string) => Promise<boolean> | boolean
  /** 编辑保存：仅当 skill.editable 为 true 且详情已加载时展示入口；返回 true 表示成功 */
  onEdit?: (skillId: string, patch: SkillEditPatch) => Promise<boolean> | boolean
}

export default function SkillDetailModal({ open, skill, onClose, onDelete, onEdit }: Props) {
  const [deleting, setDeleting] = useState(false)
  const [editing, setEditing] = useState(false)
  const [editDesc, setEditDesc] = useState('')
  const [editBody, setEditBody] = useState('')
  const [saving, setSaving] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)
  if (!open || !skill) return null

  const sourceLabel = (skill as any).tags?.[0] || '系统内置'
  const loadedAt = typeof (skill as any).loadedAt === 'number' ? ((skill as any).loadedAt as number) : null
  const updatedLabel = loadedAt
    ? new Date(loadedAt).toLocaleString(undefined, {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
    : null
  const displayName = (skill as any).title || (skill as any).id || 'Skill'
  const slug = (skill as any).id || displayName
  const canDelete = !!onDelete && (skill.origin === 'plaza' || skill.origin === 'workspace')
  // 详情已加载（loadedAt 由 openSkillDetail 成功后写入）才允许编辑，避免正文未到位就保存而清空内容
  const detailLoaded = typeof (skill as any).loadedAt === 'number'
  const canEdit = !!onEdit && skill.editable === true && detailLoaded

  const startEdit = () => {
    setEditDesc(skill.desc || '')
    setEditBody(skill.content || '')
    setEditError(null)
    setEditing(true)
  }

  const cancelEdit = () => {
    setEditing(false)
    setEditError(null)
  }

  const handleSave = async () => {
    if (!onEdit || saving) return
    setSaving(true)
    setEditError(null)
    try {
      const ok = await onEdit(skill.id, { description: editDesc, body: editBody })
      if (ok) {
        setEditing(false)
      } else {
        setEditError('保存失败，请重试')
      }
    } catch (e) {
      setEditError(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!onDelete || deleting) return
    if (!window.confirm(`确认删除「${displayName}」？此操作不可撤销。`)) return
    setDeleting(true)
    const ok = await onDelete(skill.id)
    setDeleting(false)
    if (ok) onClose()
  }

  return (
    <div className="overlay-scrim fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
      <div
        className="overlay-panel w-full max-w-4xl max-h-[85vh] rounded-[var(--radius-card)] flex flex-col animate-in zoom-in-95 duration-200 overflow-hidden"
        style={{
          background: 'var(--c-surface)',
          border: '1px solid var(--c-border)',
          backdropFilter: 'none',
          WebkitBackdropFilter: 'none',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-6 pt-5 pb-4 flex items-start justify-between gap-4 shrink-0">
          <div className="flex items-center gap-3.5 min-w-0">
            <div className="w-11 h-11 rounded-[10px] bg-[var(--c-accent-soft)] grid place-items-center shrink-0">
              <span className="text-[length:var(--fs-lg)] font-semibold text-[var(--c-accent)]">{displayName.slice(0, 1)}</span>
            </div>
            <div className="min-w-0">
              <h2 className="text-[length:var(--fs-xl)] font-semibold text-[var(--c-text)] leading-tight truncate">{displayName}</h2>
              <div className="mt-1 flex items-center gap-1.5 flex-wrap text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">
                <span className="px-2 py-[3px] rounded-[var(--radius-control)] bg-[var(--c-surface-2)] text-[length:var(--fs-xs)] leading-none">{sourceLabel}</span>
                {updatedLabel ? <span className="text-[length:var(--fs-xs)] text-[var(--c-text-tertiary)]">更新于 {updatedLabel}</span> : null}
              </div>
            </div>
          </div>
          {!editing && (
            <button
              type="button"
              onClick={onClose}
              className="btn btn-icon btn-ghost"
              aria-label="关闭"
            >
              <X size={14} />
            </button>
          )}
        </div>

        <div className="px-6 pb-5 flex-1 overflow-y-auto">
          {editing ? (
            <div className="space-y-4">
              <div>
                <label className="block text-[length:var(--fs-sm)] font-medium text-[var(--c-text-secondary)] mb-1.5">描述</label>
                <textarea
                  value={editDesc}
                  onChange={(e) => setEditDesc(e.target.value)}
                  rows={3}
                  placeholder="技能描述（展示在列表与选择器）"
                  className="w-full rounded-[var(--radius-md)] border border-[var(--c-border)] bg-[var(--c-surface-2)] p-3 text-[length:var(--fs-md)] text-[var(--c-text)] placeholder:text-[var(--c-text-secondary)]/70 focus:outline-none focus:ring-1 focus:ring-[var(--c-accent)]/40 resize-y font-mono"
                />
              </div>
              <div>
                <label className="block text-[length:var(--fs-sm)] font-medium text-[var(--c-text-secondary)] mb-1.5">正文（SKILL.md 内容）</label>
                <textarea
                  value={editBody}
                  onChange={(e) => setEditBody(e.target.value)}
                  rows={14}
                  placeholder="技能正文指令（Markdown）"
                  className="w-full rounded-[var(--radius-md)] border border-[var(--c-border)] bg-[var(--c-surface-2)] p-3 text-[length:var(--fs-sm)] text-[var(--c-text)] placeholder:text-[var(--c-text-secondary)]/70 focus:outline-none focus:ring-1 focus:ring-[var(--c-accent)]/40 resize-y font-mono leading-relaxed"
                />
              </div>
              {editError && (
                <div className="text-[length:var(--fs-sm)] text-[var(--c-error)]">{editError}</div>
              )}
            </div>
          ) : (
            <>
              {skill.desc ? (
                <p className="text-[length:var(--fs-md)] text-[var(--c-text-secondary)] leading-relaxed">{skill.desc}</p>
              ) : null}

              <div className="mt-5 flex items-center gap-3">
                <span className="text-[length:var(--fs-xs)] font-medium tracking-wide text-[var(--c-text-tertiary)]">技能内容</span>
                <span className="h-px flex-1 bg-[var(--c-border)]/70" />
                <span className="font-mono text-[length:var(--fs-xs)] text-[var(--c-text-tertiary)] truncate max-w-[45%]">{slug}</span>
              </div>
              <div className="mt-3">
                <MarkdownRenderer content={skill.content || '暂无详细描述'} className="markdown-content" />
              </div>
            </>
          )}
        </div>

        <div className="px-6 py-3.5 border-t border-[var(--c-border)]/70 flex items-center justify-between gap-3 shrink-0">
          {editing ? (
            <>
              <button
                type="button"
                onClick={cancelEdit}
                disabled={saving}
                className="btn btn-md btn-ghost"
              >
                取消
              </button>
              <PrimaryButton
                type="button"
                onClick={() => void handleSave()}
                disabled={saving}
              >
                {saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                {saving ? '保存中…' : '保存'}
              </PrimaryButton>
            </>
          ) : (
            <>
              {canDelete ? (
                <button
                  type="button"
                  onClick={() => void handleDelete()}
                  disabled={deleting}
                  className="inline-flex items-center gap-1.5 h-9 px-3 -ml-1 rounded-[var(--radius-control)] text-[length:var(--fs-sm)] font-medium transition-colors cursor-pointer text-[var(--c-error)] hover:bg-[var(--c-danger-soft)] disabled:opacity-50 disabled:cursor-not-allowed"
                  aria-label="删除"
                >
                  <Trash2 size={14} />
                  {deleting ? '删除中…' : '删除'}
                </button>
              ) : (
                <span className="font-mono text-[length:var(--fs-xs)] text-[var(--c-text-tertiary)] truncate">
                  {slug}
                </span>
              )}
              <div className="flex items-center gap-2">
                {canEdit && (
                  <button
                    type="button"
                    onClick={startEdit}
                    className="btn btn-md btn-ghost"
                    aria-label="编辑"
                  >
                    <Pencil size={14} />
                    编辑
                  </button>
                )}
                <PrimaryButton type="button" onClick={onClose}>
                  去使用
                </PrimaryButton>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
