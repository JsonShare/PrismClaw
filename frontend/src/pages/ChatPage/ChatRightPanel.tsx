import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ComponentType } from 'react'
import { createPortal } from 'react-dom'
import {
  ChevronDown,
  CornerDownLeft,
  Download,
  Eye,
  MoreHorizontal,
  Pencil,
  Plus,
  Trash2,
  X,
} from 'lucide-react'
import { FolderIcon, RefreshIcon, UploadIcon } from '../../components/icons/SystemIcons'
import { FilePreviewWithReview } from '../../components/file/FilePreviewWithReview'
import FileIcon from '../../components/file/FileIcon'
import ConfirmModal from '../../components/modals/ConfirmModal'
import { useToast } from '../../lib/toast'
import {
  referenceDisplayTitle,
  referenceIsRag,
} from '../../lib/chat/referenceSources'
import {
  deleteWorkspacePath,
  listSessionInbox,
  listSessionOutputs,
  mkdirWorkspacePath,
  moveWorkspacePath,
  type PanelFile,
} from '../../lib/file/workspaceFiles'
import type { FileChange, ReferenceSource } from '../../types'
import type { JSONContent } from '@tiptap/core'

interface ReviewHandlers {
  onConfirm: () => Promise<void>
  onReject: (force?: boolean) => Promise<void>
  onSaveContent: (content: string) => Promise<void>
  onRollback: () => Promise<void>
  onDocxNativeCommitted: () => Promise<void>
  onDocxNativeRejected: () => Promise<void>
  onReviewDone: () => void
}

interface ChatRightPanelProps {
  showLandingHome: boolean
  taskFilesOpen: boolean
  /** 面板当前宽度（px）。实际宽度由父级 CSS 变量 `--right-panel-w` 驱动，此值仅作 fallback */
  width: number
  workspaceId: string
  selectedFile: string | null
  previewBlob: Blob | null
  reviewingChange: FileChange | null
  /** 参考来源预览（非空时面板显示网址视图，优先于空文件态） */
  activeSource: ReferenceSource | null
  reviewHtml: JSONContent | null
  reviewHtmlError: string | null
  selectedFileError: string | null
  activeConvId: string | null
  token: string | null
  /** 本会话消息里引用的其他 session inbox（如 landing 阶段先上传、后建会话） */
  extraUploadSessionIds: string[]
  /** 两组文件数量变化时上报，供父级决定是否自动展开 */
  onFilesPresenceChange: (hasFiles: boolean) => void
  openFileFromOutputList: (path: string) => void
  downloadSingleFile: (path: string) => Promise<boolean>
  closePreviewModal: () => void
  /** 重新拉取当前选中文件（加载失败页的重试） */
  retrySelectedFile: () => void
  reviewHandlers: ReviewHandlers
}

interface DirNode {
  name: string
  /** 目录 key：交付物用真实路径，上传树用合成路径 */
  path: string
  dirs: DirNode[]
  files: PanelFile[]
}

type Group = 'output' | 'upload'

interface MenuState {
  type: 'file' | 'dir'
  group: Group
  path: string
  name: string
  file?: PanelFile
  node?: DirNode
  left: number
  top: number
}

interface EditingState {
  group: Group
  type: 'file' | 'dir'
  path: string
  name: string
}

const GROUP_HEADER_BTN =
  'w-7 h-7 grid place-items-center rounded-[var(--radius-control)] text-[var(--c-text-tertiary)] hover:text-[var(--c-text)] hover:bg-[var(--c-hover-bg)] transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-default'
const MENU_ITEM =
  'w-full flex items-center gap-2.5 px-3 py-2 text-left text-[length:var(--fs-sm)] text-[var(--c-text)] hover:bg-[var(--c-hover-bg)] transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-default'
const ROW_MENU_BTN =
  'w-6 h-6 shrink-0 grid place-items-center rounded-[var(--radius-control)] text-[var(--c-text-tertiary)] opacity-0 group-hover:opacity-100 focus:opacity-100 hover:text-[var(--c-text)] hover:bg-[var(--c-select-bg)] transition-all cursor-pointer'
const EDIT_INPUT =
  'h-7 min-w-0 flex-1 rounded-[var(--radius-md)] border border-[var(--c-accent)] bg-[var(--c-surface)] px-2 text-[length:var(--fs-sm)] text-[var(--c-text)] outline-none'

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback
}

function sortTree(node: DirNode) {
  node.dirs.sort((a, b) => a.name.localeCompare(b.name, 'zh'))
  node.files.sort((a, b) => b.mtimeMs - a.mtimeMs)
  node.dirs.forEach(sortTree)
}

/** 单根目录树（交付物 outbox）。 */
function buildTree(rootPath: string, files: PanelFile[], dirs: string[]): DirNode {
  const root: DirNode = { name: '', path: rootPath, dirs: [], files: [] }
  const map = new Map<string, DirNode>([[rootPath, root]])

  const ensureDir = (fullPath: string): DirNode => {
    const existing = map.get(fullPath)
    if (existing) return existing
    const idx = fullPath.lastIndexOf('/')
    const parentPath = idx >= 0 ? fullPath.slice(0, idx) : ''
    const parent = parentPath && parentPath.startsWith(rootPath) ? ensureDir(parentPath) : root
    const node: DirNode = { name: fullPath.slice(idx + 1), path: fullPath, dirs: [], files: [] }
    parent.dirs.push(node)
    map.set(fullPath, node)
    return node
  }

  for (const dirPath of dirs) {
    if (dirPath === rootPath || !dirPath.startsWith(`${rootPath}/`)) continue
    ensureDir(dirPath)
  }
  for (const file of files) {
    const idx = file.path.lastIndexOf('/')
    const parentPath = idx >= 0 ? file.path.slice(0, idx) : rootPath
    const node = parentPath.startsWith(rootPath) ? ensureDir(parentPath) : root
    node.files.push(file)
  }
  sortTree(root)
  return root
}

/**
 * 多 session inbox 合并成一棵树（landing 上传的临时 session + 当前会话）。
 * 以「相对各自 inbox 的路径」为 key，同名目录合并展示。
 */
function buildMergedTree(roots: string[], files: PanelFile[], dirs: string[]): DirNode {
  const root: DirNode = { name: '', path: '__uploads__', dirs: [], files: [] }
  const map = new Map<string, DirNode>([['', root]])

  const ensureDir = (rel: string): DirNode => {
    const existing = map.get(rel)
    if (existing) return existing
    const idx = rel.lastIndexOf('/')
    const parentRel = idx >= 0 ? rel.slice(0, idx) : ''
    const parent = ensureDir(parentRel)
    const node: DirNode = { name: rel.slice(idx + 1), path: `__uploads__/${rel}`, dirs: [], files: [] }
    parent.dirs.push(node)
    map.set(rel, node)
    return node
  }

  const relOf = (path: string): string | null => {
    for (const rootPath of roots) {
      if (path.startsWith(`${rootPath}/`)) return path.slice(rootPath.length + 1)
    }
    return null
  }

  for (const dirPath of dirs) {
    const rel = relOf(dirPath)
    if (rel) ensureDir(rel)
  }
  for (const file of files) {
    const rel = relOf(file.path)
    if (!rel) continue
    const idx = rel.lastIndexOf('/')
    const parent = ensureDir(idx >= 0 ? rel.slice(0, idx) : '')
    parent.files.push(file)
  }
  sortTree(root)
  return root
}

export function ChatRightPanel(props: ChatRightPanelProps) {
  const { toast } = useToast()
  const [outputs, setOutputs] = useState<PanelFile[]>([])
  const [outputDirs, setOutputDirs] = useState<string[]>([])
  const [uploads, setUploads] = useState<PanelFile[]>([])
  const [uploadDirs, setUploadDirs] = useState<string[]>([])
  const [outputsLoading, setOutputsLoading] = useState(false)
  const [uploadsLoading, setUploadsLoading] = useState(false)
  const [outputsError, setOutputsError] = useState<string | null>(null)
  const [uploadsError, setUploadsError] = useState<string | null>(null)
  const [outputsOpen, setOutputsOpen] = useState(true)
  const [uploadsOpen, setUploadsOpen] = useState(true)
  const [collapsedDirs, setCollapsedDirs] = useState<Set<string>>(new Set())
  const [menu, setMenu] = useState<MenuState | null>(null)
  const [movePicker, setMovePicker] = useState(false)
  const [reloadNonce, setReloadNonce] = useState(0)
  const [creatingFolderIn, setCreatingFolderIn] = useState<string | null>(null)
  const [folderName, setFolderName] = useState('')
  const [creatingFolderBusy, setCreatingFolderBusy] = useState(false)
  const [editing, setEditing] = useState<EditingState | null>(null)
  const [editName, setEditName] = useState('')
  /** 待确认的删除目标：走应用内确认弹框，不用系统 confirm */
  const [pendingDelete, setPendingDelete] = useState<{
    type: 'file' | 'dir'
    group: Group
    path: string
    name: string
  } | null>(null)
  /** 当前数据所属会话：切换会话后、新数据回来前不向父级报告“有文件” */
  const [dataConvId, setDataConvId] = useState<string | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const renameInputRef = useRef<HTMLInputElement>(null)
  const activeConvRef = useRef(props.activeConvId)
  activeConvRef.current = props.activeConvId

  const outboxRoot = props.activeConvId ? `sessions/${props.activeConvId}/outbox` : ''
  const uploadRoots = useMemo(() => {
    if (!props.activeConvId) return [] as string[]
    return [props.activeConvId, ...props.extraUploadSessionIds].map(
      (id) => `sessions/${id}/inbox`,
    )
  }, [props.activeConvId, props.extraUploadSessionIds])

  const loadOutputs = useCallback(async () => {
    const convId = props.activeConvId
    if (props.showLandingHome || !convId) {
      setDataConvId(null)
      return
    }
    setOutputsLoading(true)
    setOutputsError(null)
    try {
      const tree = await listSessionOutputs(props.workspaceId, convId, props.token)
      if (activeConvRef.current !== convId) return
      setOutputs(tree.files)
      setOutputDirs(tree.dirs)
    } catch (e) {
      if (activeConvRef.current === convId) setOutputsError(errText(e, '加载交付物失败'))
    } finally {
      if (activeConvRef.current === convId) {
        setOutputsLoading(false)
        setDataConvId(convId)
      }
    }
  }, [props.activeConvId, props.showLandingHome, props.token, props.workspaceId])

  const loadUploads = useCallback(async () => {
    const convId = props.activeConvId
    if (props.showLandingHome || !convId) {
      setDataConvId(null)
      return
    }
    setUploadsLoading(true)
    setUploadsError(null)
    try {
      // landing 阶段上传的文件落在后端自动创建的临时 session inbox 里，
      // 会话建立后不会迁移；这里把消息里引用到的 inbox 一并合并展示。
      const sessionIds = [convId, ...props.extraUploadSessionIds.filter((id) => id !== convId)]
      const results = await Promise.allSettled(
        sessionIds.map((id) => listSessionInbox(props.workspaceId, id, props.token)),
      )
      if (activeConvRef.current !== convId) return
      const fileMap = new Map<string, PanelFile>()
      const dirSet = new Set<string>()
      let firstError: unknown = null
      for (const result of results) {
        if (result.status === 'fulfilled') {
          for (const file of result.value.files) fileMap.set(file.path, file)
          for (const dir of result.value.dirs) dirSet.add(dir)
        } else if (firstError === null) {
          firstError = result.reason
        }
      }
      if (fileMap.size === 0 && dirSet.size === 0 && firstError) throw firstError
      setUploads([...fileMap.values()])
      setUploadDirs([...dirSet])
    } catch (e) {
      if (activeConvRef.current === convId) setUploadsError(errText(e, '加载上传文件失败'))
    } finally {
      if (activeConvRef.current === convId) {
        setUploadsLoading(false)
        setDataConvId(convId)
      }
    }
  }, [props.activeConvId, props.extraUploadSessionIds, props.showLandingHome, props.token, props.workspaceId])

  // 会话切换：清空数据与编辑状态。放在加载 effect 之前，
  // 避免新会话的数据被下一次 reset 清掉。
  useEffect(() => {
    setOutputs([])
    setOutputDirs([])
    setUploads([])
    setUploadDirs([])
    setOutputsError(null)
    setUploadsError(null)
    setOutputsLoading(false)
    setUploadsLoading(false)
    setDataConvId(null)
    setCollapsedDirs(new Set())
    setMenu(null)
    setMovePicker(false)
    setCreatingFolderIn(null)
    setFolderName('')
    setEditing(null)
    setEditName('')
    setPendingDelete(null)
  }, [props.activeConvId])

  // 面板即使收起也要加载：父级需要知道“是否已有文件”来决定自动展开。
  useEffect(() => {
    void loadOutputs()
    void loadUploads()
  }, [loadOutputs, loadUploads, reloadNonce])

  // 上传完成 / outbox 有新产物 → 合并 300ms 内的连续事件后刷新。
  useEffect(() => {
    let timer: number | null = null
    const schedule = () => {
      if (timer !== null) window.clearTimeout(timer)
      timer = window.setTimeout(() => {
        timer = null
        setReloadNonce((n) => n + 1)
      }, 300)
    }
    const onUploaded = () => schedule()
    const onOutbox = (event: Event) => {
      const detail = (event as CustomEvent<{ convId?: string }>).detail
      if (detail?.convId && detail.convId !== props.activeConvId) return
      schedule()
    }
    window.addEventListener('prismclaw:files-uploaded', onUploaded)
    window.addEventListener('prismclaw:outbox-updated', onOutbox)
    return () => {
      window.removeEventListener('prismclaw:files-uploaded', onUploaded)
      window.removeEventListener('prismclaw:outbox-updated', onOutbox)
      if (timer !== null) window.clearTimeout(timer)
    }
  }, [props.activeConvId])

  useEffect(() => {
    if (outputsLoading || uploadsLoading) return
    if (dataConvId !== (props.activeConvId ?? null)) return
    props.onFilesPresenceChange(
      outputs.length + uploads.length + outputDirs.length + uploadDirs.length > 0,
    )
  }, [outputs, uploads, outputsLoading, uploadsLoading, dataConvId, props.activeConvId, props.onFilesPresenceChange])

  // 菜单：外部点击 / Esc / 滚动时关闭。
  useEffect(() => {
    if (!menu) return
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenu(null)
        setMovePicker(false)
      }
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setMenu(null)
        setMovePicker(false)
      }
    }
    const onScroll = () => {
      setMenu(null)
      setMovePicker(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', onScroll)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', onScroll)
    }
  }, [menu])

  // 面板收起时一并关掉浮动菜单、内联编辑与弹框。
  useEffect(() => {
    if (!props.taskFilesOpen) {
      setMenu(null)
      setMovePicker(false)
      setCreatingFolderIn(null)
      setFolderName('')
      setPendingDelete(null)
    }
  }, [props.taskFilesOpen])

  const anchorMenu = (e: React.MouseEvent<HTMLElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    return {
      left: rect.right,
      top: Math.min(rect.bottom + 4, Math.max(8, window.innerHeight - 240)),
    }
  }

  const openFileMenu = (e: React.MouseEvent<HTMLElement>, group: Group, file: PanelFile) => {
    e.stopPropagation()
    setMovePicker(false)
    setMenu({ type: 'file', group, path: file.path, name: file.name, file, ...anchorMenu(e) })
  }

  const openDirMenu = (e: React.MouseEvent<HTMLElement>, node: DirNode) => {
    e.stopPropagation()
    setMovePicker(false)
    setMenu({ type: 'dir', group: 'output', path: node.path, name: node.name, node, ...anchorMenu(e) })
  }

  const closeMenu = () => {
    setMenu(null)
    setMovePicker(false)
  }

  const handlePreview = (file: PanelFile) => {
    closeMenu()
    props.openFileFromOutputList(file.path)
  }

  const handleDownload = async (file: PanelFile) => {
    closeMenu()
    const ok = await props.downloadSingleFile(file.path)
    if (!ok) toast('下载失败：文件可能已被删除或移动', 'error')
  }

  /** 删除前先落到确认弹框（不用系统 confirm，保证明暗主题与样式一致）。 */
  const requestDelete = (type: 'file' | 'dir', group: Group, path: string, name: string) => {
    closeMenu()
    setPendingDelete({ type, group, path, name })
  }

  const confirmDelete = async () => {
    const target = pendingDelete
    if (!target) return
    setPendingDelete(null)
    const { type, group, path, name } = target
    const isDir = type === 'dir'
    try {
      await deleteWorkspacePath(props.workspaceId, path, props.token, isDir)
      const selectedFile = props.selectedFile
      if (
        selectedFile &&
        (selectedFile === path || (isDir && selectedFile.startsWith(`${path}/`)))
      ) {
        props.closePreviewModal()
      }
      if (editing?.path === path || (isDir && editing?.path?.startsWith(`${path}/`))) {
        setEditing(null)
        setEditName('')
      }
      if (creatingFolderIn === path || (isDir && creatingFolderIn?.startsWith(`${path}/`))) {
        setCreatingFolderIn(null)
        setFolderName('')
      }
      toast(`已删除「${name}」`, 'success')
      if (group === 'output') void loadOutputs()
      else void loadUploads()
    } catch (e) {
      toast(`删除失败：${errText(e, '未知错误')}`, 'error')
    }
  }

  const startCreateFolder = (parentPath: string) => {
    if (!parentPath) return
    setCreatingFolderIn(parentPath)
    setOutputsOpen(true)
    setCollapsedDirs((prev) => {
      if (!prev.has(parentPath)) return prev
      const next = new Set(prev)
      next.delete(parentPath)
      return next
    })
    setFolderName('')
  }

  const cancelCreateFolder = () => {
    if (creatingFolderBusy) return
    setCreatingFolderIn(null)
    setFolderName('')
  }

  const submitCreateFolder = async () => {
    const parent = creatingFolderIn
    const name = folderName.trim()
    if (!parent || !name || creatingFolderBusy) return
    if (name.includes('/') || name.includes('\\')) {
      toast('文件夹名称不能包含 / 或 \\', 'error')
      return
    }
    if (name.startsWith('.')) {
      // 以 . 开头的目录会被文件服务过滤，创建后用户看不到
      toast('文件夹名称不能以 . 开头', 'error')
      return
    }
    setCreatingFolderBusy(true)
    try {
      await mkdirWorkspacePath(props.workspaceId, `${parent}/${name}`, props.token)
      toast('已创建文件夹', 'success')
      setCreatingFolderIn(null)
      setFolderName('')
      await loadOutputs()
    } catch (e) {
      toast(`新建文件夹失败：${errText(e, '未知错误')}`, 'error')
    } finally {
      setCreatingFolderBusy(false)
    }
  }

  const startRename = (group: Group, type: 'file' | 'dir', path: string, name: string) => {
    closeMenu()
    setCreatingFolderIn(null)
    setFolderName('')
    setEditing({ group, type, path, name })
    setEditName(name)
    requestAnimationFrame(() => renameInputRef.current?.focus())
  }

  const cancelRename = () => {
    setEditing(null)
    setEditName('')
  }

  const submitRename = async () => {
    const current = editing
    if (!current) return
    const name = editName.trim()
    if (!name || name === current.name) {
      cancelRename()
      return
    }
    if (name.includes('/') || name.includes('\\')) {
      toast('名称不能包含 / 或 \\', 'error')
      return
    }
    if (name.startsWith('.')) {
      toast('名称不能以 . 开头', 'error')
      return
    }
    const idx = current.path.lastIndexOf('/')
    const parent = idx >= 0 ? current.path.slice(0, idx) : ''
    try {
      await moveWorkspacePath(props.workspaceId, current.path, `${parent}/${name}`, props.token)
      toast('已重命名', 'success')
      cancelRename()
      if (current.group === 'output') void loadOutputs()
      else void loadUploads()
    } catch (e) {
      const status = (e as { status?: number }).status
      toast(
        status === 409 ? '同名文件或文件夹已存在' : `重命名失败：${errText(e, '未知错误')}`,
        'error',
      )
    }
  }

  const handleMove = async (file: PanelFile, targetDir: string) => {
    closeMenu()
    const name = file.path.slice(file.path.lastIndexOf('/') + 1)
    const dst = `${targetDir}/${name}`
    if (dst === file.path) return
    try {
      await moveWorkspacePath(props.workspaceId, file.path, dst, props.token)
      toast('已移动', 'success')
      await loadOutputs()
    } catch (e) {
      const status = (e as { status?: number }).status
      toast(
        status === 409 ? '目标位置已存在同名文件' : `移动失败：${errText(e, '未知错误')}`,
        'error',
      )
    }
  }

  const toggleDir = (path: string) => {
    setCollapsedDirs((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }

  if (props.showLandingHome) return null

  const tree = buildTree(outboxRoot, outputs, outputDirs)
  const uploadTree = buildMergedTree(uploadRoots, uploads, uploadDirs)
  const outputsEmpty = outputs.length === 0 && outputDirs.length === 0

  const isEditing = (path: string, type: 'file' | 'dir') =>
    editing?.path === path && editing.type === type

  const renderEditRow = (file?: PanelFile) => (
    <div className="flex h-9 items-center gap-2 rounded-[var(--radius-control)] px-2">
      {file ? (
        <FileIcon filename={file.name} size={20} className="shrink-0" />
      ) : (
        <FolderIcon size={18} className="text-[var(--c-text-secondary)]" />
      )}
      <input
        ref={renameInputRef}
        value={editName}
        onChange={(e) => setEditName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            void submitRename()
          } else if (e.key === 'Escape') {
            e.preventDefault()
            cancelRename()
          }
        }}
        onBlur={() => cancelRename()}
        className={EDIT_INPUT}
        aria-label="重命名"
      />
    </div>
  )

  const renderFileRow = (file: PanelFile, group: Group) => {
    if (isEditing(file.path, 'file')) {
      return <div key={`${group}:${file.path}`}>{renderEditRow(file)}</div>
    }
    return (
      <div
        key={`${group}:${file.path}`}
        role="button"
        tabIndex={0}
        onClick={() => handlePreview(file)}
        onKeyDown={(e) => {
          // 行内的「···」按钮自己处理键盘事件，避免 Enter 同时打开菜单和预览
          if (e.target !== e.currentTarget) return
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            handlePreview(file)
          }
        }}
        className="group flex h-9 w-full items-center gap-2.5 rounded-[var(--radius-control)] px-2 text-left transition-colors cursor-pointer hover:bg-[var(--c-hover-bg)] focus:outline-none focus-visible:bg-[var(--c-hover-bg)]"
        title={file.path}
      >
        <FileIcon filename={file.name} size={20} className="shrink-0" />
        <span className="min-w-0 flex-1 truncate text-[length:var(--fs-md)] text-[var(--c-text-secondary)] transition-colors group-hover:text-[var(--c-text)]">
          {file.name}
        </span>
        <button
          type="button"
          onClick={(e) => openFileMenu(e, group, file)}
          className={ROW_MENU_BTN}
          title="更多操作"
          aria-label="更多操作"
        >
          <MoreHorizontal size={15} />
        </button>
      </div>
    )
  }

  const renderDirRow = (node: DirNode, manage: boolean) => {
    if (manage && isEditing(node.path, 'dir')) {
      return <div key={node.path}>{renderEditRow()}</div>
    }
    const collapsed = collapsedDirs.has(node.path)
    const hasChildren = node.dirs.length > 0 || node.files.length > 0
    return (
      <div
        key={node.path}
        role="button"
        tabIndex={0}
        onClick={() => hasChildren && toggleDir(node.path)}
        onKeyDown={(e) => {
          if (e.target !== e.currentTarget) return
          if ((e.key === 'Enter' || e.key === ' ') && hasChildren) {
            e.preventDefault()
            toggleDir(node.path)
          }
        }}
        className={`group flex h-9 w-full items-center gap-2 rounded-[var(--radius-control)] px-2 text-left transition-colors focus:outline-none focus-visible:bg-[var(--c-hover-bg)] ${
          hasChildren ? 'cursor-pointer hover:bg-[var(--c-hover-bg)]' : 'cursor-default'
        }`}
        title={node.path}
      >
        {hasChildren ? (
          <ChevronDown
            size={14}
            className={`shrink-0 text-[var(--c-text-tertiary)] transition-transform ${collapsed ? '-rotate-90' : ''}`}
          />
        ) : (
          <span className="w-[14px] shrink-0" />
        )}
        <FolderIcon size={18} className="text-[var(--c-text-secondary)]" />
        <span className="min-w-0 flex-1 truncate text-[length:var(--fs-md)] text-[var(--c-text)]">
          {node.name}
        </span>
        {manage && (
          <>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation()
                startCreateFolder(node.path)
              }}
              className={ROW_MENU_BTN}
              title="新建子文件夹"
              aria-label="新建子文件夹"
            >
              <Plus size={15} />
            </button>
            <button
              type="button"
              onClick={(e) => openDirMenu(e, node)}
              className={ROW_MENU_BTN}
              title="更多操作"
              aria-label="更多操作"
            >
              <MoreHorizontal size={15} />
            </button>
          </>
        )}
      </div>
    )
  }

  // 目录树：每层子项带一条竖向导引线，比纯缩进更容易看清层级。
  const renderChildren = (node: DirNode, manage: boolean): React.ReactNode => (
    <div className="relative pl-5">
      <span
        aria-hidden
        className="pointer-events-none absolute bottom-1.5 left-2 top-1.5 w-px bg-[var(--c-border)] opacity-70"
      />
      <div className="space-y-0.5">
        {node.dirs.map((child) => (
          <div key={child.path}>
            {renderDirRow(child, manage)}
            {!collapsedDirs.has(child.path) && renderChildren(child, manage)}
          </div>
        ))}
        {node.files.map((file) => renderFileRow(file, manage ? 'output' : 'upload'))}
      </div>
    </div>
  )

  const currentParent = (path: string) => {
    const idx = path.lastIndexOf('/')
    return idx >= 0 ? path.slice(0, idx) : ''
  }
  const moveTargets =
    menu && menu.type === 'file' && menu.group === 'output'
      ? [outboxRoot, ...outputDirs]
          .filter((dir) => dir && dir !== currentParent(menu.path))
          .sort((a, b) => (a === outboxRoot ? -1 : b === outboxRoot ? 1 : a.localeCompare(b, 'zh')))
      : []

  const menuItem = (
    Icon: ComponentType<{ size?: number; className?: string }>,
    label: string,
    onClick: () => void,
    options: { danger?: boolean; disabled?: boolean } = {},
  ) => (
    <button
      type="button"
      className={`${MENU_ITEM} ${options.danger ? 'text-[var(--c-error)]' : ''}`}
      onClick={onClick}
      disabled={options.disabled}
    >
      <Icon size={15} />
      <span>{label}</span>
    </button>
  )

  return (
    <aside
      className={`hidden xl:flex shrink-0 min-w-0 app-sidebar app-sidebar-right flex-col h-full transition-opacity duration-200 ${
        props.taskFilesOpen ? '' : 'w-0'
      }`}
      style={props.taskFilesOpen ? { width: `var(--right-panel-w, ${props.width}px)` } : undefined}
    >
      {props.taskFilesOpen ? (
        <>
          {props.activeSource && referenceIsRag(props.activeSource) ? (
            <FilePreviewWithReview
              key={`source:rag:${props.activeSource.doi ?? props.activeSource.title}`}
              fileName={referenceDisplayTitle(props.activeSource)}
              ragPreview={props.activeSource}
              onClose={props.closePreviewModal}
              className="w-full h-full"
            />
          ) : props.selectedFile ? (
            props.previewBlob && props.reviewingChange ? (
              <FilePreviewWithReview
                key={props.selectedFile}
                file={props.previewBlob}
                fileName={props.selectedFile?.split('/').pop() || ''}
                filePath={props.selectedFile}
                change={props.reviewingChange}
                reviewDoc={props.reviewHtml}
                reviewError={props.reviewHtmlError}
                conversationId={props.activeConvId ?? undefined}
                token={props.token ?? undefined}
                onConfirm={props.reviewHandlers.onConfirm}
                onReject={props.reviewHandlers.onReject}
                onSaveContent={props.reviewHandlers.onSaveContent}
                onRollback={props.reviewHandlers.onRollback}
                onDocxNativeCommitted={props.reviewHandlers.onDocxNativeCommitted}
                onDocxNativeRejected={props.reviewHandlers.onDocxNativeRejected}
                onReviewDone={props.reviewHandlers.onReviewDone}
                onClose={props.closePreviewModal}
                className="w-full h-full"
              />
            ) : props.previewBlob ? (
              <FilePreviewWithReview
                key={props.selectedFile}
                file={props.previewBlob}
                fileName={props.selectedFile?.split('/').pop() || ''}
                filePath={props.selectedFile}
                onSaveContent={props.reviewHandlers.onSaveContent}
                onClose={props.closePreviewModal}
                className="w-full h-full"
              />
            ) : (
              <FilePreviewWithReview
                key={props.selectedFile}
                fileName={props.selectedFile?.split('/').pop() || ''}
                loadState={props.selectedFileError ? { status: 'error' } : { status: 'loading' }}
                onRetry={props.retrySelectedFile}
                onClose={props.closePreviewModal}
                className="w-full h-full"
              />
            )
          ) : (
            <div className="flex-1 min-w-0 overflow-y-auto px-3 pb-4 pt-2">
              {/* 交付物 */}
              <section>
                <div className="group/header flex h-10 items-center">
                  <button
                    type="button"
                    onClick={() => setOutputsOpen((v) => !v)}
                    className="flex h-full min-w-0 flex-1 items-center gap-2 rounded-[var(--radius-control)] px-1 text-left transition-colors hover:bg-[var(--c-hover-bg)] cursor-pointer"
                  >
                    <ChevronDown
                      size={16}
                      className={`shrink-0 text-[var(--c-text-tertiary)] transition-transform ${outputsOpen ? '' : '-rotate-90'}`}
                    />
                    <FolderIcon size={18} className="text-[var(--c-text-secondary)]" />
                    <span className="truncate text-[length:var(--fs-md)] font-medium text-[var(--c-text)]">
                      交付物
                    </span>
                  </button>
                  <div className="flex shrink-0 items-center gap-0.5 pl-1 opacity-0 pointer-events-none transition-opacity group-hover/header:opacity-100 group-hover/header:pointer-events-auto focus-within:opacity-100 focus-within:pointer-events-auto">
                    <button
                      type="button"
                      onClick={() => startCreateFolder(outboxRoot)}
                      className={GROUP_HEADER_BTN}
                      title="新建文件夹"
                      aria-label="新建文件夹"
                    >
                      <Plus size={16} />
                    </button>
                    <button
                      type="button"
                      onClick={() => void loadOutputs()}
                      disabled={outputsLoading}
                      className={GROUP_HEADER_BTN}
                      title="刷新交付物"
                      aria-label="刷新交付物"
                    >
                      <RefreshIcon size={16} className={outputsLoading ? 'animate-spin' : ''} />
                    </button>
                  </div>
                </div>
                {outputsOpen && (
                  <div className="mt-1">
                    {outputsError ? (
                      <div className="px-2 py-2 text-[length:var(--fs-sm)] text-[var(--c-error)]">
                        {outputsError}
                      </div>
                    ) : outputsLoading && outputs.length === 0 && outputDirs.length === 0 ? (
                      <div className="px-2 py-3 text-[length:var(--fs-sm)] text-[var(--c-text-tertiary)]">
                        加载中…
                      </div>
                    ) : outputsEmpty ? (
                      <div className="mx-1 rounded-[var(--radius-md)] border border-dashed border-[var(--c-border)] px-3 py-6 text-center text-[length:var(--fs-sm)] text-[var(--c-text-tertiary)]">
                        本次对话暂无交付物
                      </div>
                    ) : (
                      renderChildren(tree, true)
                    )}
                  </div>
                )}
              </section>

              {/* 我的上传 */}
              <section className="mt-5">
                <div className="group/header flex h-10 items-center">
                  <button
                    type="button"
                    onClick={() => setUploadsOpen((v) => !v)}
                    className="flex h-full min-w-0 flex-1 items-center gap-2 rounded-[var(--radius-control)] px-1 text-left transition-colors hover:bg-[var(--c-hover-bg)] cursor-pointer"
                  >
                    <ChevronDown
                      size={16}
                      className={`shrink-0 text-[var(--c-text-tertiary)] transition-transform ${uploadsOpen ? '' : '-rotate-90'}`}
                    />
                    <UploadIcon size={18} className="text-[var(--c-text-secondary)]" />
                    <span className="truncate text-[length:var(--fs-md)] font-medium text-[var(--c-text)]">
                      我的上传
                    </span>
                  </button>
                  <div className="flex shrink-0 items-center gap-0.5 pl-1 opacity-0 pointer-events-none transition-opacity group-hover/header:opacity-100 group-hover/header:pointer-events-auto focus-within:opacity-100 focus-within:pointer-events-auto">
                    <button
                      type="button"
                      onClick={() => void loadUploads()}
                      disabled={uploadsLoading}
                      className={GROUP_HEADER_BTN}
                      title="刷新上传文件"
                      aria-label="刷新上传文件"
                    >
                      <RefreshIcon size={16} className={uploadsLoading ? 'animate-spin' : ''} />
                    </button>
                  </div>
                </div>
                {uploadsOpen && (
                  <div className="mt-1">
                    {uploadsError ? (
                      <div className="px-2 py-2 text-[length:var(--fs-sm)] text-[var(--c-error)]">
                        {uploadsError}
                      </div>
                    ) : uploadsLoading && uploads.length === 0 && uploadDirs.length === 0 ? (
                      <div className="px-2 py-3 text-[length:var(--fs-sm)] text-[var(--c-text-tertiary)]">
                        加载中…
                      </div>
                    ) : uploads.length === 0 && uploadDirs.length === 0 ? (
                      <div className="mx-1 rounded-[var(--radius-md)] border border-dashed border-[var(--c-border)] px-3 py-6 text-center text-[length:var(--fs-sm)] text-[var(--c-text-tertiary)]">
                        暂无上传文件
                      </div>
                    ) : (
                      renderChildren(uploadTree, false)
                    )}
                  </div>
                )}
              </section>
            </div>
          )}
        </>
      ) : null}

      {creatingFolderIn !== null &&
        createPortal(
          <div
          className="overlay-scrim fixed inset-0 z-[120] grid place-items-center px-4"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) cancelCreateFolder()
          }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label="新建文件夹"
            className="overlay-panel w-full max-w-[520px] rounded-[16px] px-7 pt-6 pb-6"
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                e.preventDefault()
                cancelCreateFolder()
              }
            }}
          >
            <div className="flex items-center justify-between">
              <h2 className="text-[19px] font-semibold leading-[26px] text-[var(--c-text)]">
                新建文件夹
              </h2>
              <button
                type="button"
                onClick={cancelCreateFolder}
                className="grid h-8 w-8 place-items-center rounded-[8px] text-[var(--c-text-tertiary)] transition-colors hover:bg-[var(--c-hover-bg)] hover:text-[var(--c-text)] cursor-pointer"
                aria-label="关闭"
              >
                <X size={18} />
              </button>
            </div>

            <label
              htmlFor="right-panel-new-folder"
              className="mt-7 block text-[14px] font-medium text-[var(--c-text)]"
            >
              文件夹名称
            </label>
            <input
              id="right-panel-new-folder"
              autoFocus
              value={folderName}
              maxLength={30}
              onChange={(e) => setFolderName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  void submitCreateFolder()
                } else if (e.key === 'Escape') {
                  e.preventDefault()
                  cancelCreateFolder()
                }
              }}
              placeholder="请输入文件夹名称..."
              className="mt-2 h-[54px] w-full rounded-[12px] border border-[var(--c-border)] bg-[var(--c-surface-2)] px-4 text-[15px] text-[var(--c-text)] placeholder:text-[var(--c-text-tertiary)] transition-colors hover:border-[var(--c-border-strong)] focus:border-[var(--c-accent)] focus:outline-none focus:ring-2 focus:ring-[var(--c-accent)]/25"
            />
            <div className="mt-1.5 text-right text-[13px] leading-[18px] text-[var(--c-text-tertiary)]">
              {folderName.length}/30
            </div>

            <div className="mt-6 flex items-center justify-end gap-3">
              <button
                type="button"
                onClick={cancelCreateFolder}
                className="btn btn-lg btn-secondary"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void submitCreateFolder()}
                disabled={!folderName.trim() || creatingFolderBusy}
                className="btn btn-lg btn-primary"
              >
                {creatingFolderBusy ? '创建中…' : '创建'}
                <CornerDownLeft size={14} className="opacity-70" />
              </button>
            </div>
          </div>
          </div>,
          document.body,
        )}

      {menu &&
        createPortal(
          <div
            ref={menuRef}
            className="overlay-panel fixed z-[100] min-w-[168px] max-h-[60vh] overflow-y-auto py-1 rounded-[var(--radius-card)]"
            style={{ left: menu.left, top: menu.top, transform: 'translateX(-100%)' }}
            onClick={(e) => e.stopPropagation()}
          >
            {menu.type === 'file' ? (
              !movePicker ? (
                <>
                  {menuItem(Eye, '打开预览', () => menu.file && handlePreview(menu.file))}
                  {menuItem(Download, '下载', () => menu.file && void handleDownload(menu.file))}
                  {menuItem(Pencil, '重命名', () =>
                    startRename(menu.group, 'file', menu.path, menu.name),
                  )}
                  {menu.group === 'output' &&
                    menuItem(FolderIcon, '移动到文件夹', () => setMovePicker(true), {
                      disabled: moveTargets.length === 0,
                    })}
                  {menuItem(
                    Trash2,
                    '删除',
                    () => requestDelete('file', menu.group, menu.path, menu.name),
                    { danger: true },
                  )}
                </>
              ) : (
                <>
                  <div className="px-3 py-1.5 text-[length:var(--fs-xs)] text-[var(--c-text-tertiary)]">
                    移动到
                  </div>
                  {moveTargets.map((dir) => (
                    <button
                      key={dir}
                      type="button"
                      className={MENU_ITEM}
                      title={dir}
                      onClick={() => void handleMove(menu.file as PanelFile, dir)}
                    >
                      <FolderIcon size={15} />
                      <span className="truncate">
                        {dir === outboxRoot ? '根目录' : dir.slice(outboxRoot.length + 1)}
                      </span>
                    </button>
                  ))}
                </>
              )
            ) : (
              <>
                {menuItem(Pencil, '重命名', () =>
                  startRename('output', 'dir', menu.path, menu.name),
                )}
                {menuItem(
                  Trash2,
                  '删除',
                  () => requestDelete('dir', 'output', menu.path, menu.name),
                  { danger: true },
                )}
              </>
            )}
          </div>,
          document.body,
        )}

      <ConfirmModal
        open={pendingDelete !== null}
        title={
          pendingDelete?.type === 'dir'
            ? `删除文件夹「${pendingDelete.name}」`
            : `删除「${pendingDelete?.name ?? ''}」`
        }
        message={
          pendingDelete?.type === 'dir'
            ? '文件夹及其中的所有内容都会被删除，历史消息中指向这些文件的卡片将失效。'
            : '删除后不可恢复，历史消息中指向该文件的卡片将失效。'
        }
        confirmLabel="删除"
        destructive
        onConfirm={() => void confirmDelete()}
        onCancel={() => setPendingDelete(null)}
      />
    </aside>
  )
}
