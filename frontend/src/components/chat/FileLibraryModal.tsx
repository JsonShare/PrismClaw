import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronRight, LoaderCircle, Search, TriangleAlert } from 'lucide-react'
import { FolderIcon, RefreshIcon } from '../icons/SystemIcons'
import { apiUrl } from '../../lib/system-adapter'
import { fetchOutputsResult, formatBytes, formatTime } from '../../lib/file/outputFiles'
import FileIcon from '../file/FileIcon'
import { PrimaryButton } from '../ui/PrimaryButton'
import { ModalShell } from '../modals/ModalShell'
import { useToast } from '../../lib/toast'

export interface FileLibrarySelection {
  path: string
  name: string
  sizeBytes?: number
}

interface FileLibraryItem extends FileLibrarySelection {
  key: string
  ext: string
  mtimeMs: number
  /** 组内相对路径，用于拼目录树（不是选中时提交的 path） */
  relPath: string
  group: 'upload' | 'output'
}

/** 目录树节点：目录只负责分组，文件才可选 */
interface TreeNode {
  name: string
  relPath: string
  dirs: TreeNode[]
  files: FileLibraryItem[]
}

interface Props {
  open: boolean
  onClose: () => void
  /** 工作区跟随输入框下方已选的工作区，弹框内不再提供切换 */
  workspaceId: string
  token: string | null
  sessionId?: string | null
  /** 最多可选文件数（与单条消息附件上限一致） */
  maxSelect?: number
  onConfirm: (items: FileLibrarySelection[]) => void
}

const GROUP_LABEL: Record<FileLibraryItem['group'], string> = {
  upload: '上传文件',
  output: '交付产物',
}

const GROUP_ORDER = ['upload', 'output'] as const

function extOf(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : ''
}

/** 上传文件：路径形如 `.../<session>/inbox/<相对路径>`，取 inbox 之后的部分 */
function uploadRelPath(path: string, name: string): string {
  const marker = '/inbox/'
  const idx = path.lastIndexOf(marker)
  return idx >= 0 ? path.slice(idx + marker.length) : name
}

/** 交付产物：rel_path 已相对工作区，去掉 outbox 前缀后作为树内相对路径 */
function outputRelPath(relPath: string): string {
  return relPath.replace(/^\/+/, '').replace(/^outbox\//, '')
}

/** 把一组文件按 relPath 拼成目录树（无文件的空目录不会出现，选择场景无意义） */
function buildTree(items: FileLibraryItem[]): TreeNode {
  const root: TreeNode = { name: '', relPath: '', dirs: [], files: [] }
  const map = new Map<string, TreeNode>([['', root]])

  const ensureDir = (rel: string): TreeNode => {
    const hit = map.get(rel)
    if (hit) return hit
    const idx = rel.lastIndexOf('/')
    const parentRel = idx >= 0 ? rel.slice(0, idx) : ''
    const parent = ensureDir(parentRel)
    const node: TreeNode = { name: rel.slice(idx + 1), relPath: rel, dirs: [], files: [] }
    parent.dirs.push(node)
    map.set(rel, node)
    return node
  }

  for (const item of items) {
    const idx = item.relPath.lastIndexOf('/')
    const parentRel = idx >= 0 ? item.relPath.slice(0, idx) : ''
    ensureDir(parentRel).files.push(item)
  }

  const sort = (node: TreeNode) => {
    node.dirs.sort((a, b) => a.name.localeCompare(b.name, 'zh'))
    node.files.sort((a, b) => a.name.localeCompare(b.name, 'zh'))
    node.dirs.forEach(sort)
  }
  sort(root)
  return root
}

/**
 * 从文件库选择引用文件（只读选择，不提供重命名/删除等编辑能力）。
 *
 * 数据源（均为已有接口，不新增职责）：
 * - `GET /api/uploads?workspace_id=` —— 不传 session_id 时后端聚合当前工作区
 *   所有会话的 inbox，避免历史上传在列表里"消失"。
 * - `GET /api/workspaces/outbox?workspace_id=` —— 交付产物（跨会话聚合）。
 *
 * 两组都按目录树展示，结构与右栏文件区一致；工作区跟随输入框下方，弹框内不可切换。
 */
export default function FileLibraryModal({
  open,
  onClose,
  workspaceId,
  token,
  sessionId,
  maxSelect = 10,
  onConfirm,
}: Props) {
  const [items, setItems] = useState<FileLibraryItem[]>([])
  const [loading, setLoading] = useState(false)
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [collapsedDirs, setCollapsedDirs] = useState<Set<string>>(new Set())
  const [failedSources, setFailedSources] = useState<string[]>([])
  const [truncated, setTruncated] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)
  const [submitting, setSubmitting] = useState(false)
  const { toast } = useToast()
  const searchRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!open) return
    setQuery('')
    setSelected(new Set())
    setCollapsedDirs(new Set())
    const raf = requestAnimationFrame(() => searchRef.current?.focus())
    return () => cancelAnimationFrame(raf)
  }, [open])

  // 弹层期间锁 body 滚动（与系统其它模态框的行为一致）
  useEffect(() => {
    if (!open) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = prev
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [open, onClose])

  // 切换工作区后已选文件属于旧工作区，必须清空，否则会引用到当前工作区不存在的路径
  useEffect(() => {
    if (!open) return
    setSelected(new Set())
  }, [open, workspaceId])

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setLoading(true)
    setFailedSources([])
    setTruncated(false)
    void (async () => {
      const next: FileLibraryItem[] = []
      const failed: string[] = []
      let wasTruncated = false

      try {
        const res = await fetch(
          apiUrl(`/api/uploads?workspace_id=${encodeURIComponent(workspaceId)}`),
          { headers: token ? { Authorization: `Bearer ${token}` } : {} },
        )
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data: { files?: { original_name: string; path: string; size_bytes: number }[] } = await res.json()
        for (const f of data.files ?? []) {
          next.push({
            key: `upload:${f.path}`,
            path: f.path,
            name: f.original_name,
            sizeBytes: f.size_bytes,
            ext: extOf(f.original_name),
            mtimeMs: 0,
            group: 'upload',
            relPath: uploadRelPath(f.path, f.original_name),
          })
        }
      } catch {
        failed.push(GROUP_LABEL.upload)
      }

      try {
        const out = await fetchOutputsResult(workspaceId, token)
        wasTruncated = out.truncated
        for (const f of out.items) {
          next.push({
            key: `output:${f.rel_path}`,
            path: f.rel_path,
            name: f.name,
            sizeBytes: f.size_bytes,
            ext: f.ext || extOf(f.name),
            mtimeMs: f.mtime_ms ?? 0,
            group: 'output',
            relPath: outputRelPath(f.rel_path),
          })
        }
      } catch {
        failed.push(GROUP_LABEL.output)
      }

      if (cancelled) return
      setItems(next)
      setFailedSources(failed)
      setTruncated(wasTruncated)
      setLoading(false)
    })()
    return () => {
      cancelled = true
    }
  }, [open, workspaceId, token, reloadKey])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return items
    return items.filter((item) => item.name.toLowerCase().includes(q) || item.path.toLowerCase().includes(q))
  }, [items, query])

  const grouped = useMemo(() => {
    return GROUP_ORDER.map((group) => {
      const groupItems = filtered.filter((item) => item.group === group)
      return { group, items: groupItems, tree: buildTree(groupItems) }
    }).filter((entry) => entry.items.length > 0)
  }, [filtered])

  const toggle = useCallback(
    (path: string) => {
      if (!selected.has(path) && selected.size >= maxSelect) {
        toast(`最多选择 ${maxSelect} 个文件`, 'info')
        return
      }
      setSelected((prev) => {
        const next = new Set(prev)
        if (next.has(path)) next.delete(path)
        else next.add(path)
        return next
      })
    },
    [selected, maxSelect, toast],
  )

  const toggleGroup = useCallback(
    (list: FileLibraryItem[]) => {
      const allSelected = list.every((item) => selected.has(item.path))
      if (allSelected) {
        const removing = new Set(list.map((item) => item.path))
        setSelected((prev) => new Set([...prev].filter((path) => !removing.has(path))))
        return
      }
      const pending = list.filter((item) => !selected.has(item.path))
      const room = Math.max(0, maxSelect - selected.size)
      if (pending.length > room) toast(`最多选择 ${maxSelect} 个文件`, 'info')
      setSelected((prev) => {
        const next = new Set(prev)
        for (const item of pending.slice(0, room)) next.add(item.path)
        return next
      })
    },
    [selected, maxSelect, toast],
  )

  const toggleDir = useCallback((relPath: string) => {
    setCollapsedDirs((prev) => {
      const next = new Set(prev)
      if (next.has(relPath)) next.delete(relPath)
      else next.add(relPath)
      return next
    })
  }, [])

  /**
   * 确认前先把选中文件复制进 `.refs/`（有会话时为会话 inbox 下），
   * 让引用不依赖源会话目录的生命周期；复制失败的文件回退原路径，
   * 源会话还在时仍可读取。
   */
  const handleConfirm = useCallback(async () => {
    if (submitting) return
    const picked = items.filter((item) => selected.has(item.path))
    if (picked.length === 0) return
    const fallback = picked.map(({ path, name, sizeBytes }) => ({ path, name, sizeBytes }))
    const normalizePath = (path: string) => path.replace(/^\/+/, '')
    setSubmitting(true)
    try {
      const qs = new URLSearchParams({ workspace_id: workspaceId })
      if (sessionId) qs.set('session_id', sessionId)
      const res = await fetch(apiUrl(`/api/files/copy?${qs.toString()}`), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ paths: picked.map((item) => item.path) }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data: {
        files?: { source_path: string; path: string; name: string; size_bytes: number }[]
      } = await res.json()
      const copied = new Map(
        (data.files ?? []).map((f) => [
          normalizePath(f.source_path),
          { path: f.path, name: f.name, sizeBytes: f.size_bytes },
        ]),
      )
      if (copied.size < picked.length) {
        toast(`有 ${picked.length - copied.size} 个文件复制失败，已保留原路径`, 'error')
      }
      onConfirm(
        picked.map(
          (item) => copied.get(normalizePath(item.path)) ?? { path: item.path, name: item.name, sizeBytes: item.sizeBytes },
        ),
      )
      onClose()
    } catch {
      toast('文件复制失败，已保留原路径', 'error')
      onConfirm(fallback)
      onClose()
    } finally {
      setSubmitting(false)
    }
  }, [items, selected, submitting, workspaceId, sessionId, token, toast, onConfirm, onClose])

  const allFailed = failedSources.length === 2 && items.length === 0

  /** 目录/文件行：目录可折叠，文件整行可选中（只读，无编辑入口） */
  const renderNode = (node: TreeNode, depth: number): React.ReactNode => {
    return (
      <>
        {node.dirs.map((dir) => {
          const collapsed = collapsedDirs.has(dir.relPath)
          return (
            <div key={`d:${dir.relPath}`}>
              <button
                type="button"
                onClick={() => toggleDir(dir.relPath)}
                aria-expanded={!collapsed}
                className="flex h-9 w-full cursor-pointer items-center gap-1.5 rounded-[var(--radius-md)] pr-2
                 text-left text-[length:var(--fs-sm)] text-[var(--c-text)] transition-colors hover:bg-[var(--c-hover-bg)]"
                style={{ paddingLeft: 6 + depth * 14 }}
              >
                <ChevronRight
                  size={14}
                  strokeWidth={2.2}
                  className={`shrink-0 text-[var(--c-text-tertiary)] transition-transform ${collapsed ? '' : 'rotate-90'}`}
                />
                <FolderIcon size={16} className="shrink-0 text-[var(--c-text-secondary)]" />
                <span className="min-w-0 truncate">{dir.name}</span>
                <span className="shrink-0 text-[length:var(--fs-xs)] text-[var(--c-text-tertiary)]">
                  {countFiles(dir)}
                </span>
              </button>
              {!collapsed && renderNode(dir, depth + 1)}
            </div>
          )
        })}
        {node.files.map((item) => {
          const checked = selected.has(item.path)
          return (
            <button
              key={item.key}
              type="button"
              aria-pressed={checked}
              onClick={() => toggle(item.path)}
              className={`flex h-9 w-full cursor-pointer items-center gap-2.5 rounded-[var(--radius-md)] pr-2 text-left
               transition-colors hover:bg-[var(--c-hover-bg)] ${checked ? 'bg-[var(--c-select-bg)]' : ''}`}
              style={{ paddingLeft: 24 + depth * 14 }}
            >
              <span
                aria-hidden
                className={`grid h-[16px] w-[16px] shrink-0 place-items-center rounded-[4px] border transition-colors ${
                  checked
                    ? 'border-[var(--c-accent)] bg-[var(--c-accent)] text-[var(--c-btn-primary-fg)]'
                    : 'border-[var(--c-border-strong)] bg-transparent'
                }`}
              >
                {checked && <Check size={11} strokeWidth={3} />}
              </span>
              <FileIcon filename={item.name} ext={item.ext} size={18} className="shrink-0" />
              <span className="min-w-0 flex-1 truncate text-[length:var(--fs-sm)] text-[var(--c-text)]">
                {item.name}
              </span>
              <span className="shrink-0 text-[length:var(--fs-xs)] text-[var(--c-text-tertiary)]">
                {item.mtimeMs ? formatTime(item.mtimeMs) : formatBytes(item.sizeBytes ?? 0)}
              </span>
            </button>
          )
        })}
      </>
    )
  }

  return createPortal(
    <ModalShell
      open={open}
      onClose={onClose}
      title="从文件库选择"
      maxWidth="max-w-[720px]"
      panelClassName="modal-opaque"
    >
      <div className="flex h-[min(640px,calc(100vh-200px))] flex-col">
        <div className="flex items-center gap-2 px-6 pb-3">
          <div className="relative min-w-0 flex-1">
            <Search
              size={16}
              strokeWidth={2}
              className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-[var(--c-text-secondary)]"
            />
            <input
              ref={searchRef}
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索文件"
              className="h-11 w-full rounded-[var(--radius-md)] border border-[var(--c-border)] bg-transparent
               pl-10 pr-3 text-[length:var(--fs-md)] text-[var(--c-text)] outline-none
               placeholder:text-[var(--c-text-tertiary)] focus:border-[var(--c-accent)]"
            />
          </div>
        </div>

        {failedSources.length > 0 && (
          <div
            className="mx-6 mb-2 flex items-center justify-between gap-3 rounded-[var(--radius-md)]
             border border-[var(--c-error)]/30 bg-[var(--c-error)]/10 px-3 py-2
             text-[length:var(--fs-sm)] text-[var(--c-error)]"
          >
            <span className="flex min-w-0 items-center gap-1.5">
              <TriangleAlert size={14} className="shrink-0" />
              <span className="truncate">{failedSources.join('、')}加载失败</span>
            </span>
            <button
              type="button"
              onClick={() => setReloadKey((k) => k + 1)}
              className="flex shrink-0 items-center gap-1 cursor-pointer hover:underline"
            >
              <RefreshIcon size={12} />
              重试
            </button>
          </div>
        )}

        {truncated && (
          <div className="mx-6 mb-2 text-[length:var(--fs-xs)] text-[var(--c-text-tertiary)]">
            交付产物数量超出上限，列表只显示最近的部分文件
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-2">
          {loading && items.length === 0 && (
            <div className="flex items-center justify-center py-16 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">
              <LoaderCircle size={16} className="mr-2 animate-spin" />
              加载中…
            </div>
          )}
          {!loading && allFailed && (
            <div className="flex flex-col items-center justify-center gap-3 py-16 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">
              <span>文件列表加载失败</span>
              <button
                type="button"
                onClick={() => setReloadKey((k) => k + 1)}
                className="btn btn-md btn-secondary"
              >
                重新加载
              </button>
            </div>
          )}
          {!loading && !allFailed && grouped.length === 0 && (
            <div className="py-16 text-center text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">
              {query ? '没有匹配的文件' : '当前工作区还没有文件'}
            </div>
          )}
          {grouped.map(({ group, items: groupItems, tree }) => {
            const allSelected = groupItems.every((item) => selected.has(item.path))
            return (
              <div key={group} className="mb-2">
                <div className="flex items-center gap-2 px-3 py-2">
                  <button
                    type="button"
                    aria-label={`全选${GROUP_LABEL[group]}`}
                    aria-pressed={allSelected}
                    onClick={() => toggleGroup(groupItems)}
                    className={`grid h-[16px] w-[16px] shrink-0 cursor-pointer place-items-center rounded-[4px] border transition-colors ${
                      allSelected
                        ? 'border-[var(--c-accent)] bg-[var(--c-accent)] text-[var(--c-btn-primary-fg)]'
                        : 'border-[var(--c-border-strong)] bg-transparent'
                    }`}
                  >
                    {allSelected && <Check size={11} strokeWidth={3} />}
                  </button>
                  <FolderIcon size={16} className="text-[var(--c-text-secondary)]" />
                  <span className="text-[length:var(--fs-sm)] font-medium text-[var(--c-text)]">
                    {GROUP_LABEL[group]}
                  </span>
                  <span className="text-[length:var(--fs-xs)] text-[var(--c-text-tertiary)]">{groupItems.length}</span>
                </div>
                {renderNode(tree, 0)}
              </div>
            )
          })}
        </div>

        <div className="flex items-center justify-between border-t border-[var(--c-border)] px-6 py-4">
          <span className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">
            已选 {selected.size} 个文件
          </span>
          <div className="flex items-center gap-3">
            <button type="button" onClick={onClose} className="btn btn-md btn-secondary">
              取消
            </button>
            <PrimaryButton size="md" onClick={handleConfirm} disabled={selected.size === 0 || submitting}>
              {submitting ? '正在引用…' : '确认添加'}
            </PrimaryButton>
          </div>
        </div>
      </div>
    </ModalShell>,
    document.body,
  )
}

/** 目录下（含子目录）的文件数，显示在目录名右侧 */
function countFiles(node: TreeNode): number {
  return node.files.length + node.dirs.reduce((sum, dir) => sum + countFiles(dir), 0)
}
