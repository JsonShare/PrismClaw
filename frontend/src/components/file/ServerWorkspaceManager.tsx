import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  FolderOpen,
  Folder,
  UploadCloud,
  Trash2,
  Plus,
  ChevronDown,
  LoaderCircle,
  Upload,
  X,
  ShieldOff,
} from 'lucide-react'
import { PrimaryButton } from '../ui/PrimaryButton'
import { HairlineDivider } from '../ui/HairlineDivider'
import type { Workspace } from '../../types'

interface ServerWorkspaceManagerProps {
  workspaceId?: string
  workspaces?: Workspace[]
  isLoading?: boolean
  onSelectWorkspace?: (id: string) => void
  onCreateWorkspace?: (name: string) => Promise<void>
  onUploadFiles?: (files: File[]) => Promise<void>
  onRemoveWorkspace?: (id: string) => Promise<void>
  /** 只读模式（Chat 咨询）：操作按钮可见但禁用，弹层底部给出切换提示，而非静默隐藏 */
  readOnly?: boolean
}

/** Read a dropped file/folder tree into a flat File[] list, attaching the
 *  original relative path (folder structure) onto each File as `relativePath`. */
async function filesFromDataTransfer(dt: DataTransfer): Promise<File[]> {
  const entries = Array.from(dt.items)
    .map((it) => (it as any).webkitGetAsEntry?.())
    .filter(Boolean)

  if (entries.length === 0) {
    return Array.from(dt.files)
  }

  const out: File[] = []
  const readEntry = async (entry: any): Promise<void> => {
    const full = (entry.fullPath || entry.name || '').replace(/^\/+/, '')
    if (entry.isFile) {
      const file: File = await new Promise((res, rej) => entry.file(res, rej))
      try {
        Object.defineProperty(file, 'relativePath', { value: full || file.name })
      } catch {
        /* ignore non-configurable */
      }
      out.push(file)
    } else if (entry.isDirectory) {
      const reader = entry.createReader()
      let batch: any[] = []
      await new Promise<void>((res, rej) => {
        const read = () =>
          reader.readEntries((es: any[]) => {
            if (es.length === 0) res()
            else {
              batch = batch.concat(es)
              read()
            }
          }, rej)
        read()
      })
      for (const child of batch) await readEntry(child)
    }
  }

  for (const e of entries) await readEntry(e)
  return out
}

/** 工作区菜单条目（菜单式：默认无背景，active 靠左侧短竖线 + 文字加重，操作图标 hover 才显隐） */
function WorkspaceMenuItem({
  name,
  icon,
  isActive,
  showSync,
  showDelete,
  onSelect,
  onSync,
  onDelete,
  title,
  disabled = false,
}: {
  name: string
  icon: ReactNode
  isActive: boolean
  showSync?: boolean
  showDelete?: boolean
  onSelect: () => void
  onSync?: () => void
  onDelete?: () => void
  title?: string
  /** 只读模式：操作按钮渲染但禁用（可见性不随模式变化，避免功能"凭空消失"） */
  disabled?: boolean
}) {
  const disabledHint = '咨询模式只读，切换到 Work 协作后可操作'
  return (
    <div
      className={`group relative flex items-center gap-2.5 rounded-[var(--radius-md)] pl-3 pr-1.5 py-1.5 transition-colors ${
        isActive
          ? 'bg-[var(--c-surface-2)]/40'
          : 'hover:bg-[var(--c-surface-2)]/30'
      }`}
    >
      {/* active 左侧短竖线 */}
      {isActive && (
        <span className="absolute left-0.5 top-1/2 -translate-y-1/2 h-4 w-0.5 rounded-full bg-[var(--c-accent)]" />
      )}
      <button
        type="button"
        onClick={onSelect}
        title={title}
        className="flex min-w-0 flex-1 items-center gap-2.5 text-left cursor-pointer"
      >
        <span className="shrink-0">{icon}</span>
        <span
          className={`min-w-0 flex-1 truncate text-[length:var(--fs-sm)] ${
            isActive
              ? 'font-semibold text-[var(--c-text)]'
              : 'text-[var(--c-text-secondary)]'
          }`}
        >
          {name}
        </span>
      </button>
      {/* 操作图标：默认透明，整条 hover/group-hover 时显隐，避免常驻红图标带来的视觉噪音 */}
      <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
        {showSync && onSync && (
          <button
            type="button"
            onClick={onSync}
            disabled={disabled}
            title={disabled ? disabledHint : '上传文件或文件夹'}
            aria-label="上传文件或文件夹"
            className="btn btn-icon btn-ghost"
          >
            <UploadCloud size={14} strokeWidth={1.8} />
          </button>
        )}
        {showDelete && onDelete && (
          <button
            type="button"
            onClick={onDelete}
            disabled={disabled}
            title={disabled ? disabledHint : '删除目录'}
            aria-label="删除目录"
            className="btn btn-icon btn-ghost"
          >
            <Trash2 size={14} strokeWidth={1.8} />
          </button>
        )}
      </div>
    </div>
  )
}

export default function ServerWorkspaceManager({
  workspaceId,
  workspaces = [],
  isLoading = false,
  onSelectWorkspace,
  onCreateWorkspace,
  onUploadFiles,
  onRemoveWorkspace,
  readOnly = false,
}: ServerWorkspaceManagerProps) {
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<'menu' | 'create' | 'upload'>('menu')
  const [createName, setCreateName] = useState('')
  const [createError, setCreateError] = useState<string | null>(null)
  const [isCreating, setIsCreating] = useState(false)
  const [dragging, setDragging] = useState(false)
  const wrapperRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const folderInputRef = useRef<HTMLInputElement>(null)

  const currentWorkspace = useMemo(
    () => workspaces.find((w) => w.id === workspaceId),
    [workspaces, workspaceId],
  )
  const displayName = currentWorkspace?.name || (workspaceId && workspaceId !== 'default' ? workspaceId : '默认工作区')

  useEffect(() => {
    if (!open) return
    function onDocumentClick(event: MouseEvent) {
      const target = event.target
      if (!(target instanceof Node)) return
      if (wrapperRef.current && !wrapperRef.current.contains(target)) {
        closeAll()
      }
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') closeAll()
    }
    document.addEventListener('mousedown', onDocumentClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocumentClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const closeAll = useCallback(() => {
    setOpen(false)
    setMode('menu')
    setCreateName('')
    setCreateError(null)
  }, [])

  const handleSelect = useCallback((id: string) => {
    onSelectWorkspace?.(id)
    closeAll()
  }, [closeAll, onSelectWorkspace])

  const handleSync = useCallback((id: string) => {
    if (workspaceId !== id) {
      onSelectWorkspace?.(id)
    }
    setMode('upload')
  }, [workspaceId, onSelectWorkspace])

  const handleRemove = useCallback(async (id: string) => {
    try {
      await onRemoveWorkspace?.(id)
    } catch {
      // caller is responsible for surfacing errors
    }
  }, [onRemoveWorkspace])

  const handleCreate = useCallback(async () => {
    const trimmed = createName.trim()
    if (!trimmed) {
      setCreateError('请输入工作区名称')
      return
    }
    if (trimmed.length > 64) {
      setCreateError('名称过长（最多 64 字符）')
      return
    }
    if (!/^[\u4e00-\u9fa5a-zA-Z0-9\s_-]+$/.test(trimmed)) {
      setCreateError('名称仅支持中英文、数字、空格、_、-')
      return
    }

    setIsCreating(true)
    setCreateError(null)
    try {
      await onCreateWorkspace?.(trimmed)
      closeAll()
    } catch (err) {
      const msg = err instanceof Error ? err.message : '创建失败'
      setCreateError(msg)
    } finally {
      setIsCreating(false)
    }
  }, [createName, onCreateWorkspace, closeAll])

  const handleFilePick = useCallback((event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? [])
    event.target.value = ''
    if (files.length === 0) return
    closeAll()
    void onUploadFiles?.(files)
  }, [closeAll, onUploadFiles])

  const handleFolderPick = useCallback((event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? [])
    event.target.value = ''
    if (files.length === 0) return
    closeAll()
    void onUploadFiles?.(files)
  }, [closeAll, onUploadFiles])

  const handleDrop = useCallback(async (event: React.DragEvent<HTMLDivElement>) => {
    const dt = event.dataTransfer
    const files = await filesFromDataTransfer(dt)
    if (files.length === 0) return
    closeAll()
    void onUploadFiles?.(files)
  }, [closeAll, onUploadFiles])

  const nonDefaultWorkspaces = workspaces.filter((w) => w.id !== 'default')

  return (
    <div ref={wrapperRef} className="relative inline-flex">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={`h-8 px-2 flex items-center gap-2 rounded-[var(--radius-control)] transition-colors cursor-pointer outline-none
                    hover:text-[var(--c-text)]
                    ${open ? 'text-[var(--c-text)]' : 'text-[var(--c-text-secondary)]'}`}
      >
        <FolderOpen size={14} className="shrink-0" strokeWidth={1.5} />
        <span className="text-[length:var(--fs-sm)] font-medium truncate max-w-[180px]">{displayName}</span>
        <ChevronDown size={14} className={`shrink-0 transition-transform duration-200 ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div
          role="menu"
          className="overlay-panel absolute left-0 bottom-full mb-2 z-30 w-[280px]
                     rounded-[var(--radius-card)]
                     overflow-hidden flex flex-col animate-in fade-in slide-in-from-bottom-2 duration-200"
        >
          {mode === 'menu' && (
            <div className="p-2 max-h-[400px] overflow-y-auto">
              {/* 工作区列表（菜单式：默认无背景，active 靠左侧短竖线 + 文字加重） */}
              <div className="space-y-0.5">
                {/* 默认工作区 */}
                <WorkspaceMenuItem
                  name="默认工作区"
                  icon={<Folder size={16} strokeWidth={1.8} className="text-[var(--c-text-secondary)]/70" />}
                  isActive={workspaceId === 'default'}
                  showSync={!!onUploadFiles}
                  disabled={readOnly}
                  onSelect={() => handleSelect('default')}
                  onSync={() => handleSync('default')}
                />
                {/* 其他工作区 */}
                {nonDefaultWorkspaces.map((w) => (
                  <WorkspaceMenuItem
                    key={w.id}
                    name={w.name || w.id}
                    title={w.name || w.id}
                    icon={
                      w.trusted === false ? (
                        <span title="不受信任">
                          <ShieldOff size={14} className="text-[var(--c-error)] shrink-0" />
                        </span>
                      ) : (
                        <Folder size={16} strokeWidth={1.8} className="text-[var(--c-text-secondary)]/70" />
                      )
                    }
                    isActive={w.id === workspaceId}
                    showSync={!!onUploadFiles}
                    showDelete={!!onRemoveWorkspace}
                    disabled={readOnly}
                    onSelect={() => handleSelect(w.id)}
                    onSync={() => handleSync(w.id)}
                    onDelete={() => handleRemove(w.id)}
                  />
                ))}
              </div>

              {!isLoading && nonDefaultWorkspaces.length === 0 && (
                <div className="px-3 py-2 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/60 text-center">
                  暂无其他工作区
                </div>
              )}

              {isLoading && (
                <div className="px-3 py-2 flex items-center justify-center gap-2 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/70">
                  <LoaderCircle size={14} className="animate-spin" />
                  加载工作区中...
                </div>
              )}

              {/* 只读模式提示：解释操作按钮为何禁用，而不是让功能静默消失 */}
              {readOnly && (
                <div className="mt-1.5 px-3 py-1.5 flex items-center gap-2 text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]/70">
                  <ShieldOff size={12} className="shrink-0" />
                  <span>咨询模式只读 — 切换到 Work 协作后可上传 / 删除</span>
                </div>
              )}

              {/* 新建工作目录（仅读写模式） */}
              {onCreateWorkspace && (
                <>
                  <HairlineDivider className="mx-1 my-1.5" />
                  <button
                    type="button"
                    onClick={() => setMode('create')}
                    disabled={readOnly}
                    className={`flex w-full items-center gap-2 rounded-[var(--radius-control)] px-3 py-2 text-[length:var(--fs-sm)] transition-colors ${
                      readOnly
                        ? 'text-[var(--c-text-secondary)]/50 cursor-not-allowed'
                        : 'text-[var(--c-text-secondary)] hover:bg-[var(--c-surface-2)]/40 hover:text-[var(--c-text)] cursor-pointer'
                    }`}
                  >
                    <Plus size={15} strokeWidth={1.8} />
                    <span>新建工作目录</span>
                  </button>
                </>
              )}
            </div>
          )}

          {mode === 'create' && (
            <div className="p-3">
              <span className="mb-3 block text-[length:var(--fs-sm)] font-medium text-[var(--c-text)]">新建工作区</span>

              <input
                type="text"
                value={createName}
                onChange={(e) => { setCreateName(e.target.value); setCreateError(null) }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !isCreating) void handleCreate()
                }}
                placeholder="请输入工作区名称"
                autoFocus
                className="h-9 w-full rounded-[var(--radius-md)] bg-[var(--c-surface-2)]/40 px-3 text-[length:var(--fs-md)] text-[var(--c-text)] placeholder:text-[var(--c-text-secondary)]/60 outline-none transition-all focus:ring-1 focus:ring-[var(--c-accent)]"
              />

              {createError && (
                <p className="mt-2 text-[length:var(--fs-sm)] text-[var(--c-error)]">{createError}</p>
              )}

              <div className="mt-3 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => { setMode('menu'); setCreateError(null); setCreateName('') }}
                  className="btn btn-sm btn-ghost"
                >
                  取消
                </button>
                {/* 主色实心按钮：颜色/禁用态由 .btn-primary 接管 */}
                <button
                  type="button"
                  onClick={() => void handleCreate()}
                  disabled={isCreating || !createName.trim()}
                  className="btn btn-sm btn-primary"
                >
                  {isCreating && <LoaderCircle size={12} className="animate-spin" />}
                  创建
                </button>
              </div>
            </div>
          )}

          {mode === 'upload' && (
            <div className="p-4">
              <div className="flex items-center justify-between mb-3">
                <span className="text-[length:var(--fs-sm)] font-semibold text-[var(--c-text)]">上传文件</span>
                <button
                  type="button"
                  onClick={() => setMode('menu')}
                  className="btn btn-icon btn-ghost"
                  title="返回"
                  aria-label="返回"
                >
                  <X size={14} className="text-[var(--c-text-secondary)]" />
                </button>
              </div>

              <div
                onDragOver={(e) => {
                  e.preventDefault()
                  setDragging(true)
                }}
                onDragLeave={(e) => {
                  e.preventDefault()
                  setDragging(false)
                }}
                onDrop={(e) => {
                  e.preventDefault()
                  setDragging(false)
                  void handleDrop(e)
                }}
                className={`mb-3 rounded-[var(--radius-card)] py-10 px-3 text-center transition-colors ${
                  dragging ? 'bg-[var(--c-surface-2)]/70' : ''
                }`}
              >
                <div className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/60 leading-relaxed">
                  支持上传文档、表格、图片等文件<br />
                  或将文件 / 文件夹拖拽到此处
                </div>
              </div>

              <input
                ref={fileInputRef}
                type="file"
                multiple
                onChange={handleFilePick}
                className="hidden"
              />

              <input
                ref={folderInputRef}
                type="file"
                multiple
                /* @ts-ignore: webkitdirectory is widely supported */
                webkitdirectory=""
                /* @ts-ignore */
                directory=""
                onChange={handleFolderPick}
                className="hidden"
              />

              <div className="flex items-center justify-center gap-3">
                <PrimaryButton
                  type="button"
                  onClick={() => folderInputRef.current?.click()}
                >
                  <FolderOpen size={15} className="text-[var(--c-btn-primary-fg)]" strokeWidth={1.5} />
                  上传文件夹
                </PrimaryButton>

                <PrimaryButton
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                >
                  <Upload size={15} className="text-[var(--c-btn-primary-fg)]" strokeWidth={1.5} />
                  上传文件
                </PrimaryButton>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
