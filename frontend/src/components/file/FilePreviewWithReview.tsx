import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import {
  Check,
  Eye,
  FileText,
  GitCompare,
  Loader2,
  Pencil,
  RotateCcw,
  X,
} from 'lucide-react'
import type { FileChange } from '../../types'
import type { JSONContent } from '@tiptap/core'
import { FilePreview } from './FilePreview'
import { PreviewBoundary } from './PreviewBoundary'
import { DiffViewer } from './DiffViewer'
import { KIND_LABEL, OfficeChangeRow } from '../modals/FileReviewModal'
import { DocxReviewHost, type DocxReviewHostRef } from './docxReview/DocxReviewHost'
import { OfficeReviewEditor } from './officeReview/OfficeReviewEditor'
import { Surface } from '../ui/Surface'
import { saveDocxNativeReviewed } from '../../lib/file/fileReview'

type ViewMode = 'preview' | 'review' | 'edit'

/** 判断文件 kind 是否可做文本编辑（非二进制/非办公文件） */
function isTextKind(kind: FileChange['kind']): boolean {
  return kind === 'text' || kind === 'code' || kind === 'config'
}

/** 可直接渲染预览的文件类型（HTML/图片/PDF/Markdown 等）。
 *  这类交付物点开时应直接看到渲染结果，而非审阅 diff（diff 会把整份 HTML
 *  当成新增行铺成纯文本/代码视图——这正是「首次预览显示文本」的根因）。 */
const RENDERABLE_VIEW_EXT = /\.(html?|pdf|md|markdown|png|jpe?g|gif|svg|webp|bmp)$/i
function isRenderableViewType(name: string): boolean {
  return RENDERABLE_VIEW_EXT.test(name || '')
}

/** 行级 diff 统计徽章（审阅 tab 右侧） */
function DiffBadge({ insertions, deletions }: { insertions: number; deletions: number }) {
  return (
    <span className="flex items-center gap-1 text-[length:var(--fs-sm)] font-semibold tabular-nums leading-none ml-0.5">
      <span className="text-[var(--c-success)]">+{insertions}</span>
      <span className="text-[var(--c-danger)]">−{deletions}</span>
    </span>
  )
}

/** 模式切换分段控件中的单个 tab */
function ModeTab({
  active,
  onClick,
  icon,
  label,
  children,
}: {
  active: boolean
  onClick: () => void
  icon: ReactNode
  label: string
  children?: ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`btn btn-sm btn-ghost ${active ? 'font-medium' : 'font-normal'}`}
      aria-pressed={active}
    >
      <span className="shrink-0">{icon}</span>
      {label}
      {children}
    </button>
  )
}

interface FilePreviewWithReviewProps {
  file: Blob
  fileName: string
  /** 工作区相对路径，用于保存/回滚 */
  filePath?: string
  change?: FileChange
  /** `xlsx/pptx` 审查版 TipTap JSON（由后端 office-review-html 合成） */
  reviewDoc?: JSONContent | null
  /** `xlsx/pptx` 审查文档合成失败的错误信息（后端不可达 / 超时等） */
  reviewError?: string | null
  /** 初始模式；不传时自动判断：有 change→review，无 change→preview */
  initialMode?: ViewMode
  onConfirm?: () => Promise<void>
  onReject?: () => Promise<void>
  /** 手动编辑保存回调：传入编辑后的文本内容 */
  onSaveContent?: (content: string) => Promise<void>
  /** 回滚到备份版本 */
  onRollback?: () => Promise<void>
  conversationId?: string
  token?: string
  onDocxNativeCommitted?: () => Promise<void>
  /** `.docx` 原生审阅拒绝后回调（文件已停在 before 态，仅需记账审阅决策）。 */
  onDocxNativeRejected?: () => Promise<void>
  onReviewDone?: () => void
  onClose?: () => void
  className?: string
}

/**
 * 融合组件：文件预览 / 审阅 / 编辑通过模式切换共存。
 * - Preview：完整展示文件内容（FilePreview）
 * - Review：完整展示差异审阅 + Accept/Reject 操作栏
 * - Edit：文本编辑器，可手动修改内容后保存；支持回滚到备份版本
 * 三种模式独占同一区域，通过顶部 tab 切换。
 */
export function FilePreviewWithReview({
  file,
  fileName,
  change,
  reviewDoc,
  reviewError,
  initialMode,
  onConfirm,
  onReject,
  onSaveContent,
  onRollback,
  conversationId,
  token,
  onDocxNativeCommitted,
  onDocxNativeRejected,
  onReviewDone,
  onClose,
  className,
}: FilePreviewWithReviewProps) {
  // 有 change 且为非渲染类（代码/配置/纯文本）文件 → 默认审阅；
  // 渲染类交付物（HTML/图片/PDF/Markdown）→ 默认预览，避免整份内容被
  // DiffViewer 当成新增行铺成纯文本（「首次预览显示文本」的根因）。
  const [mode, setMode] = useState<ViewMode>(
    initialMode ?? (change && !isRenderableViewType(fileName) ? 'review' : 'preview'),
  )
  // 初始态从持久化的审阅决策回放：重开一个已审阅的文件时按钮呈已决状态，不重复提示。
  const [confirmed, setConfirmed] = useState(change?.reviewed === 'confirmed')
  const [rejected, setRejected] = useState(change?.reviewed === 'rejected')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  /* ---- Edit 模式状态 ---- */
  const [editContent, setEditContent] = useState('')
  const [editLoading, setEditLoading] = useState(false)
  const [editSaving, setEditSaving] = useState(false)
  const [editDirty, setEditDirty] = useState(false)
  const [editRollbackBusy, setEditRollbackBusy] = useState(false)
  const editRef = useRef<HTMLTextAreaElement>(null)
  const docxReviewRef = useRef<DocxReviewHostRef | null>(null)
  const lastLoadedBlobRef = useRef<Blob | null>(null)  // 跟踪上次加载的 Blob，用于判断是否需要重新读取

  /* ---- 审阅状态随文件变化重置 ----
     只在「文件路径」真正变化时才重置终态。原来依赖整个 `change` 引用做依赖，
     但 `persistReviewDecision` → `mergeMessageMetadata` 会生成新的 change 对象引用，
     触发本 effect 把用户刚点下的「已确认/已拒绝」清掉，造成按钮状态闪烁/丢失。 */
  const prevReviewPathRef = useRef<string | undefined>(change?.path)
  useEffect(() => {
    const prevPath = prevReviewPathRef.current
    const nextPath = change?.path
    if (prevPath !== nextPath) {
      prevReviewPathRef.current = nextPath
      setConfirmed(change?.reviewed === 'confirmed')
      setRejected(change?.reviewed === 'rejected')
      setError(null)
    }
  }, [change])

  /* ---- 模式随 change 变化自动调整 ---- */
  useEffect(() => {
    // change 变化时重新推断初始模式
    setMode(initialMode ?? (change && !isRenderableViewType(fileName) ? 'review' : 'preview'))
  }, [change, initialMode, fileName])

  /* ---- 从 Blob 读取文本内容（Edit 模式需要） ---- */
  useEffect(() => {
    if (mode !== 'edit') return
    // Blob 变化时（如回滚/保存后重新 fetch）必须重新读取
    const blobChanged = file !== lastLoadedBlobRef.current
    if (!blobChanged && editContent && !editDirty) return // 同一 Blob 且已加载，不重复读
    lastLoadedBlobRef.current = file
    setEditLoading(true)
    const reader = new FileReader()
    reader.onload = () => {
      setEditContent(reader.result as string)
      setEditDirty(false)
      setEditLoading(false)
    }
    reader.onerror = () => {
      setError('无法读取文件内容')
      setEditLoading(false)
    }
    reader.readAsText(file, 'utf-8')
  }, [mode, file]) // editContent/editDirty 不入 deps，避免循环

  /* ---- 审阅计算 ---- */
  const isDocxNativeReview = Boolean(
    change &&
      change.kind === 'docx' &&
      change.review_mode === 'docx-native' &&
      change.review_docx_ref,
  )
  const isStructuredOfficeReview = change
    ? change.kind === 'xlsx' || change.kind === 'pptx'
    : false
  const isTextKindFile = change ? isTextKind(change.kind) : false
  // Edit 模式仅对文本类文件开放（Office 文件走审阅逐条 Accept/Reject，不走文本编辑）
  const canEdit = change
    ? isTextKindFile
    : /\.(txt|md|py|js|ts|tsx|jsx|css|html|json|yaml|yml|toml|cfg|ini|sh|bash|zsh|sql|csv|log|conf|env|gitignore|dockerfile|makefile)$/i.test(fileName)

  const officeDetails = change?.officeDetail ?? []
  // `xlsx/pptx` 使用后端合成的 TipTap 审阅文档；`.docx` 改走原生 review artifact。
  const effectiveReviewDoc = reviewDoc ?? null
  const showOfficeStats = isDocxNativeReview || isStructuredOfficeReview
  const totalChars = change?.hunks.reduce(
    (sum, h) => sum + h.lines.reduce((s, l) => s + l.length, 0),
    0,
  ) ?? 0

  /* ---- Review 操作按钮 ---- */
  const handleConfirm = useCallback(async () => {
    setBusy(true)
    setError(null)
    try {
      if (isDocxNativeReview) {
        if (!change?.review_docx_ref || !change.before_ref || !change.before_sha) {
          throw new Error('缺少 DOCX 原生审阅上下文')
        }
        if (!conversationId || !token) {
          throw new Error('缺少会话上下文')
        }
        docxReviewRef.current?.acceptAll()
        const reviewedBuffer = await docxReviewRef.current?.save()
        if (!reviewedBuffer) throw new Error('DOCX 审阅结果导出失败')
        await saveDocxNativeReviewed(
          conversationId,
          change.path,
          change.review_docx_ref,
          change.before_ref,
          change.before_sha,
          reviewedBuffer,
          token,
        )
        await onDocxNativeCommitted?.()
      } else {
        if (!onConfirm) return
        await onConfirm()
      }
      setConfirmed(true)
      setTimeout(() => onReviewDone?.(), 600)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }, [change, conversationId, isDocxNativeReview, onConfirm, onDocxNativeCommitted, onReviewDone, token])

  const handleReject = useCallback(async () => {
    setBusy(true)
    setError(null)
    try {
      if (isDocxNativeReview) {
        // docx 原生审阅：拒绝 = 保持 before 态（文件从未被真实修改），仅本地
        // 撤销 tracked-changes 视图，再通知父组件记账审阅决策。
        docxReviewRef.current?.rejectAll()
        await onDocxNativeRejected?.()
      } else {
        if (!onReject) return
        await onReject()
      }
      setRejected(true)
      setTimeout(() => onReviewDone?.(), 400)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }, [isDocxNativeReview, onReject, onDocxNativeRejected, onReviewDone])

  /* ---- Edit 操作按钮 ---- */
  const handleSaveContent = useCallback(async () => {
    if (!onSaveContent) return
    setEditSaving(true)
    setError(null)
    try {
      await onSaveContent(editContent)
      setEditDirty(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setEditSaving(false)
    }
  }, [onSaveContent, editContent])

  const handleRollback = useCallback(async () => {
    if (!onRollback) return
    setEditRollbackBusy(true)
    setError(null)
    try {
      await onRollback()
      // 回滚后 Blob 会由 Chat.tsx 重新 fetch 并更新 previewBlob
      // useEffect 检测到 file(Blob) 变化后会自动重新读取内容
      // 这里只需标记不再 dirty
      setEditDirty(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setEditRollbackBusy(false)
    }
  }, [onRollback])

  /* ---- 审阅完成状态时自动切换回预览 ---- */
  useEffect(() => {
    if (confirmed || rejected) {
      setTimeout(() => setMode('preview'), 300)
    }
  }, [confirmed, rejected])

  const hasChange = Boolean(change)
  const tabPreviewActive = mode === 'preview'
  const tabReviewActive = mode === 'review'
  const tabEditActive = mode === 'edit'
  const docxPendingCount = change?.pending_changes ?? 0
  const reviewSummary = isDocxNativeReview
    ? `${docxPendingCount} 处修订待审`
    : isStructuredOfficeReview
      ? `${officeDetails.length || 0} 处结构化改动`
      : `${change?.insertions ?? 0} 增 / ${change?.deletions ?? 0} 删`

  /* ---- Edit 模式行数统计 ---- */
  const editLineCount = editContent.split('\n').length

  return (
    <Surface bordered={false} className={`h-full w-full min-w-0 flex flex-col ${className ?? ''}`}>
      {/* ── 顶部导航栏：左 tab / 中标题 / 右操作（极简融合） ── */}
      <Surface
        bordered={false}
        className="grid grid-cols-[auto_1fr_auto] items-center gap-3 px-4 py-2.5 shrink-0"
        style={{ borderBottom: '1px solid var(--c-border)' }}
      >
        {/* 左区：模式切换 tab */}
        <div className="flex items-center shrink-0">
          <div className="flex items-center gap-1">
            {hasChange && (
              <ModeTab
                active={tabReviewActive}
                onClick={() => setMode('review')}
                icon={<GitCompare size={14} />}
                label="审阅"
              >
                {change && <DiffBadge insertions={change.insertions} deletions={change.deletions} />}
              </ModeTab>
            )}
            <ModeTab
              active={tabPreviewActive}
              onClick={() => setMode('preview')}
              icon={<Eye size={14} />}
              label="预览"
            />
            {canEdit && onSaveContent && (
              <ModeTab
                active={tabEditActive}
                onClick={() => setMode('edit')}
                icon={<Pencil size={14} />}
                label="编辑"
              />
            )}
          </div>
        </div>

        {/* 中区：文件标题 */}
        <div className="flex items-center justify-center gap-2 min-w-0 overflow-hidden">
          <FileText size={15} className="text-[var(--c-text-secondary)]/50 shrink-0" />
          <span
            className="text-[length:var(--fs-md)] font-semibold text-[var(--c-text)] truncate"
            title={fileName}
          >
            {fileName}
          </span>
          {change && (
            <span className="px-1.5 py-0.5 rounded-[var(--radius-control)] text-[length:var(--fs-sm)] bg-[var(--c-accent)]/8 text-[var(--c-accent)] shrink-0">
              {KIND_LABEL[change.kind]}
            </span>
          )}
        </div>

        {/* 右区：操作按钮 */}
        <div className="flex items-center gap-1 justify-self-end shrink-0">
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              className="btn btn-icon btn-ghost"
              title="关闭"
            >
              <X size={16} className="text-[var(--c-text-secondary)]" />
            </button>
          )}
        </div>
      </Surface>

      {hasChange && (
        <div className="px-4 py-2 border-b border-[var(--c-border)]/25 bg-[var(--c-surface-2)]/45 shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            <span
              className={`inline-flex items-center gap-1 rounded-[var(--radius-control)] px-2 py-0.5 text-[length:var(--fs-sm)] font-medium shrink-0 ${
                tabReviewActive
                  ? 'bg-[var(--c-accent)]/10 text-[var(--c-accent)]'
                  : 'bg-[var(--c-surface-2)] text-[var(--c-text-secondary)]'
              }`}
            >
              <GitCompare size={12} />
              {tabReviewActive ? '审阅中' : '有待审改动'}
            </span>
            <span className="min-w-0 truncate text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">
              {reviewSummary}
              {tabReviewActive
                ? '，可在底部执行 Confirm All / Reject All'
                : '，切换到“审阅”查看 diff'}
            </span>
            {!tabReviewActive && (
              <button
                type="button"
                onClick={() => setMode('review')}
                className="ml-auto inline-flex items-center gap-1 rounded-[var(--radius-control)] px-2.5 py-1 text-[length:var(--fs-sm)] text-[var(--c-accent)] hover:bg-[var(--c-accent)]/8 transition-colors cursor-pointer shrink-0"
              >
                查看审阅
              </button>
            )}
          </div>
        </div>
      )}

      {/* ── 内容区：根据 mode 渲染 ── */}
      <Surface bordered={false} className="flex-1 min-w-0 min-h-0 overflow-hidden w-full">
        {/* ── Preview 模式 ── */}
        {tabPreviewActive && (
          <PreviewBoundary>
            <FilePreview
              file={file}
              fileName={fileName}
              className="w-full h-full"
            />
          </PreviewBoundary>
        )}

        {/* ── Review 模式 ── */}
        {tabReviewActive && (
          change ? (
            <div className="h-full flex flex-col">
              {/* 差异区域 */}
              {isDocxNativeReview && conversationId && token && change.review_docx_ref ? (
                <DocxReviewHost
                  ref={docxReviewRef}
                  conversationId={conversationId}
                  token={token}
                  reviewDocxRef={change.review_docx_ref}
                  fileName={fileName}
                />
              ) : isStructuredOfficeReview && effectiveReviewDoc ? (
                <OfficeReviewEditor
                  doc={effectiveReviewDoc}
                  details={officeDetails}
                />
              ) : isStructuredOfficeReview && reviewError ? (
                <div className="flex-1 overflow-y-auto px-5 py-4 min-h-0">
                  <div className="text-[length:var(--fs-sm)] text-[var(--c-error)] bg-[color-mix(in_srgb,var(--c-error)_10%,transparent)] rounded-[var(--radius-md)] px-3.5 py-2.5 leading-relaxed border border-[color-mix(in_srgb,var(--c-error)_30%,transparent)]">
                    审阅内容合成失败：{reviewError}
                  </div>
                </div>
              ) : (
              <div className="flex-1 overflow-y-auto px-5 py-4 min-h-0">
                {isStructuredOfficeReview ? (
                  officeDetails.length > 0 ? (
                    <div className="space-y-2.5">
                      {officeDetails.map((d, i) => (
                        <OfficeChangeRow key={i} d={d} />
                      ))}
                    </div>
                  ) : change.note ? (
                    <div className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/70 bg-[var(--c-surface-2)]/50 rounded-[var(--radius-md)] px-3.5 py-2 leading-relaxed border border-[var(--c-border)]/20">
                      {change.note}
                    </div>
                  ) : (
                    <div className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/40">
                      无详细改动信息。
                    </div>
                  )
                ) : (
                  <>
                    {change.note && (
                      <div className="mb-3 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/70 bg-[var(--c-surface-2)]/50 rounded-[var(--radius-md)] px-3.5 py-2 leading-relaxed border border-[var(--c-border)]/20">
                        {change.note}
                      </div>
                    )}
                    <DiffViewer
                      hunks={change.hunks}
                      kind={change.kind}
                      truncated={change.truncated}
                    />
                  </>
                )}
              </div>
              )}

              {/* 底部操作栏 */}
              <div className="flex items-center justify-between gap-4 px-6 py-4 border-t border-[var(--c-border)]/30 shrink-0 bg-[var(--c-surface-2)]/70 backdrop-blur-sm">
                <span className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/50 tabular-nums select-none">
                  {showOfficeStats
                    ? `${change.insertions} 增 · ${change.deletions} 删 字符`
                    : `${totalChars.toLocaleString()} characters`}
                </span>
                <div className="flex items-center gap-3">
                  {error && (
                    <span className="text-[length:var(--fs-sm)] text-[var(--c-danger)] max-w-[220px] truncate" title={error}>
                      {error}
                    </span>
                  )}
                  <button
                    type="button"
                    onClick={handleReject}
                    disabled={rejected || busy}
                    className="btn btn-md btn-secondary"
                  >
                    {busy && !confirmed ? <Loader2 size={15} className="animate-spin" /> : <X size={15} />}
                    {rejected ? '已拒绝' : busy ? '处理中…' : 'Reject All'}
                  </button>
                  <button
                    type="button"
                    onClick={handleConfirm}
                    disabled={confirmed || busy}
                    className="btn btn-md btn-primary"
                  >
                    {busy && !rejected ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />}
                    {confirmed ? 'Saved' : busy ? '处理中…' : 'Confirm All'}
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <div className="h-full flex items-center justify-center text-[var(--c-text-secondary)]/40 text-[length:var(--fs-sm)]">
              当前文件无变更信息，仅可预览
            </div>
          )
        )}

        {/* ── Edit 模式 ── */}
        {tabEditActive && (
          <div className="h-full flex flex-col">
            {editLoading ? (
              <div className="flex-1 flex items-center justify-center text-[var(--c-text-secondary)]/40 text-[length:var(--fs-sm)]">
                <Loader2 size={18} className="animate-spin mr-2" />
                加载文件内容…
              </div>
            ) : (
              <>
                {/* 编辑器区域 */}
                <div className="flex-1 min-h-0 overflow-hidden p-0">
                  <textarea
                    ref={editRef}
                    value={editContent}
                    onChange={(e) => {
                      setEditContent(e.target.value)
                      setEditDirty(true)
                    }}
                    className="w-full h-full resize-none bg-[var(--c-surface-2)]/30 text-[var(--c-text)] text-[length:var(--fs-sm)] leading-[1.6] font-mono p-4 border-0 outline-none focus:ring-0 placeholder:text-[var(--c-text-secondary)]/30"
                    spellCheck={false}
                    autoComplete="off"
                    autoCorrect="off"
                    placeholder="文件内容"
                  />
                </div>

                {/* 编辑模式底部操作栏 */}
                <div className="flex items-center gap-3 px-4 py-2.5 border-t border-[var(--c-border)]/30 shrink-0 bg-[var(--c-surface-2)]/70 backdrop-blur-sm">
                  {/* 左区：行数 + 修改状态 */}
                  <span className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/35 tabular-nums select-none">
                    {editLineCount} 行
                    {editDirty && (
                      <span className="ml-2 text-[var(--c-warning)]">未保存</span>
                    )}
                  </span>

                  {/* 错误信息 */}
                  {error && (
                    <span className="text-[length:var(--fs-sm)] text-[var(--c-danger)] max-w-[200px] truncate" title={error}>
                      {error}
                    </span>
                  )}

                  {/* 右区：操作按钮 */}
                  <div className="ml-auto flex items-center gap-2">
                    {/* 回滚到备份 */}
                    {hasChange && onRollback && (
                      <button
                        type="button"
                        onClick={handleRollback}
                        disabled={editRollbackBusy || editSaving}
                        className="btn btn-sm btn-secondary"
                        title="回滚到修改前的版本"
                      >
                        {editRollbackBusy ? <Loader2 size={13} className="animate-spin" /> : <RotateCcw size={13} />}
                        {editRollbackBusy ? '回滚中…' : '回滚'}
                      </button>
                    )}

                    {/* 保存编辑内容 */}
                    <button
                      type="button"
                      onClick={handleSaveContent}
                      disabled={editSaving || !editDirty}
                      className="btn btn-sm btn-primary"
                    >
                      {editSaving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                      {editSaving ? '保存中…' : editDirty ? '保存' : '已保存'}
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        )}
      </Surface>
    </Surface>
  )
}
