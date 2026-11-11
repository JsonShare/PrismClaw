import { useMemo, useState, useEffect, useRef } from 'react'
import { ChevronDown, Search, Layers, HardDrive, Clock, FolderKanban, MoreHorizontal, Download, Trash2 } from 'lucide-react'
import FileIcon from '../../../components/file/FileIcon'
import { FilePreviewWithReview } from '../../../components/file/FilePreviewWithReview'
import {
  fetchOutputs,
  formatBytes,
  formatTime,
  outputRawUrl,
  outputDownloadUrl,
  OUTPUT_TYPE_LABELS,
  OUTPUT_TYPE_FILTERS,
  type OutputFile,
  type OutputSortKey,
} from '../../../lib/file/outputFiles'
import { apiUrl } from '../../../lib/system-adapter'

interface OutputsPageProps {
  token?: string | null
  onSelectSession?: (sessionId: string) => void
}

const SORT_OPTIONS: { value: OutputSortKey; label: string; icon: 'time' | 'size' | 'session' | 'workspace' }[] = [
  { value: 'mtime', label: '按更新时间', icon: 'time' },
  { value: 'size', label: '按文件大小', icon: 'size' },
  { value: 'session', label: '按任务分组', icon: 'session' },
  { value: 'workspace', label: '按工作区分组', icon: 'workspace' },
]

function SortIcon({ type }: { type: 'time' | 'size' | 'session' | 'workspace' }) {
  const cls = 'w-3.5 h-3.5'
  if (type === 'time') return <Clock className={cls} />
  if (type === 'size') return <HardDrive className={cls} />
  if (type === 'session') return <Layers className={cls} />
  return <FolderKanban className={cls} />
}

export default function OutputsPage({ token, onSelectSession: _onSelectSession }: OutputsPageProps) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [files, setFiles] = useState<OutputFile[]>([])
  const [typeFilter, setTypeFilter] = useState<string>('all')
  const [sortKey, setSortKey] = useState<OutputSortKey>('mtime')
  const [query, setQuery] = useState('')
  const [showSearch, setShowSearch] = useState(false)
  const [preview, setPreview] = useState<OutputFile | null>(null)
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set())
  const [openMenuKey, setOpenMenuKey] = useState<string | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const items = await fetchOutputs('all', token ?? null)
      setFiles(items)
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 点击外部关闭菜单
  useEffect(() => {
    if (!openMenuKey) return
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpenMenuKey(null)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [openMenuKey])

  const isGrouped = sortKey === 'session' || sortKey === 'workspace'

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    let list = files.filter((f) => {
      if (typeFilter !== 'all' && f.type !== typeFilter) return false
      if (q && !f.name.toLowerCase().includes(q)) return false
      return true
    })
    if (sortKey === 'size') {
      list = [...list].sort((a, b) => b.size_bytes - a.size_bytes)
    } else if (sortKey === 'session') {
      list = [...list].sort((a, b) => {
        const aTitle = a.source_session_title || a.source_session_id || ''
        const bTitle = b.source_session_title || b.source_session_id || ''
        return bTitle.localeCompare(aTitle)
      })
    } else if (sortKey === 'workspace') {
      list = [...list].sort((a, b) => {
        return (b.workspace_name || '').localeCompare(a.workspace_name || '')
      })
    } else {
      list = [...list].sort((a, b) => b.mtime_ms - a.mtime_ms)
    }
    return list
  }, [files, typeFilter, query, sortKey])

  const groups = useMemo(() => {
    if (!isGrouped) return null
    const map = new Map<string, { key: string; label: string; files: OutputFile[] }>()
    for (const f of filtered) {
      let groupKey: string
      let groupLabel: string
      if (sortKey === 'workspace') {
        groupKey = f.workspace_id
        groupLabel = f.workspace_name || '未命名工作区'
      } else {
        groupKey = f.source_session_id || '未关联任务'
        groupLabel = f.source_session_title || f.source_session_id || '未关联任务'
      }
      const existing = map.get(groupKey)
      if (existing) {
        existing.files.push(f)
      } else {
        map.set(groupKey, { key: groupKey, label: groupLabel, files: [f] })
      }
    }
    return Array.from(map.values())
  }, [filtered, sortKey, isGrouped])

  const toggleGroup = (key: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev)
      if (next.has(key)) {
        next.delete(key)
      } else {
        next.add(key)
      }
      return next
    })
  }

  const handleDownload = (f: OutputFile) => {
    const url = outputDownloadUrl(f.workspace_id, f.rel_path)
    const a = document.createElement('a')
    a.href = url
    a.download = f.name
    if (token) {
      // 带 token 下载：通过 fetch 获取 blob 再触发下载
      const headers: Record<string, string> = {}
      headers.Authorization = `Bearer ${token}`
      fetch(url, { headers })
        .then((r) => r.blob())
        .then((blob) => {
          const blobUrl = URL.createObjectURL(blob)
          const a2 = document.createElement('a')
          a2.href = blobUrl
          a2.download = f.name
          document.body.appendChild(a2)
          a2.click()
          document.body.removeChild(a2)
          URL.revokeObjectURL(blobUrl)
        })
    } else {
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
    }
    setOpenMenuKey(null)
  }

  const handleDelete = async (f: OutputFile) => {
    if (!confirm(`确定要删除「${f.name}」吗？`)) {
      setOpenMenuKey(null)
      return
    }
    try {
      const headers: Record<string, string> = { 'Content-Type': 'application/json' }
      if (token) {
        headers.Authorization = `Bearer ${token}`
      }
      const url = `${apiUrl('/api/files/delete')}?workspace_id=${encodeURIComponent(f.workspace_id)}`
      const res = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          path: f.rel_path,
          recursive: false,
        }),
      })
      if (!res.ok) throw new Error(String(res.status))
      // 从列表中移除
      setFiles((prev) => prev.filter((x) => !(x.workspace_id === f.workspace_id && x.rel_path === f.rel_path)))
    } catch (e) {
      setError(e instanceof Error ? `删除失败：${e.message}` : '删除失败')
    } finally {
      setOpenMenuKey(null)
    }
  }

  const currentSortOption = SORT_OPTIONS.find((o) => o.value === sortKey) || SORT_OPTIONS[0]

  const rowKey = (f: OutputFile) => `${f.workspace_id}/${f.rel_path}`

  const renderRow = (f: OutputFile) => {
    const key = rowKey(f)
    const isMenuOpen = openMenuKey === key
    return (
      <div
        key={key}
        className="grid grid-cols-[minmax(0,1fr)_72px_80px_160px_40px] items-center gap-6 py-3 hover:bg-[var(--c-surface-2)]/50 transition-colors group"
      >
        <div
          role="button"
          tabIndex={0}
          onClick={() => setPreview(f)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              setPreview(f)
            }
          }}
          className="flex items-center gap-3 min-w-0 cursor-pointer focus:outline-none"
        >
          <FileIcon filename={f.name} ext={f.ext} size={20} />
          <span className="truncate text-[length:var(--fs-md)] text-[var(--c-text)] font-normal" title={f.name}>
            {f.name}
          </span>
        </div>
        <span className="text-[length:var(--fs-md)] text-[var(--c-text-secondary)]">
          {OUTPUT_TYPE_LABELS[f.type]}
        </span>
        <span className="text-[length:var(--fs-md)] text-[var(--c-text-secondary)] text-right">
          {formatBytes(f.size_bytes)}
        </span>
        <span className="text-[length:var(--fs-md)] text-[var(--c-text-secondary)] text-right">
          {formatTime(f.mtime_ms)}
        </span>
        {/* 操作列 */}
        <div className="relative flex justify-end" ref={isMenuOpen ? menuRef : undefined}>
          <button
            onClick={(e) => {
              e.stopPropagation()
              setOpenMenuKey(isMenuOpen ? null : key)
            }}
            className="w-7 h-7 flex items-center justify-center rounded-[var(--radius-control)] text-[var(--c-text-tertiary)] hover:text-[var(--c-text)] hover:bg-[var(--c-surface-2)] transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100 focus:outline-none"
            title="更多操作"
          >
            <MoreHorizontal size={16} />
          </button>
          {isMenuOpen && (
            <div className="overlay-panel absolute right-0 top-full mt-1 z-20 min-w-[120px] py-1 rounded-[var(--radius-card)]">
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  handleDownload(f)
                }}
                className="w-full flex items-center gap-2 px-3 py-2 text-left text-[length:var(--fs-md)] text-[var(--c-text)] hover:bg-[var(--c-surface-2)] transition-colors"
              >
                <Download size={14} />
                <span>下载</span>
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  handleDelete(f)
                }}
                className="w-full flex items-center gap-2 px-3 py-2 text-left text-[length:var(--fs-md)] text-[var(--c-error)] hover:bg-[var(--c-surface-2)] transition-colors"
              >
                <Trash2 size={14} />
                <span>删除</span>
              </button>
            </div>
          )}
        </div>
      </div>
    )
  }

  const renderGroupHeader = (group: { key: string; label: string; files: OutputFile[] }) => {
    const isCollapsed = collapsedGroups.has(group.key)
    return (
      <button
        key={`header-${group.key}`}
        onClick={() => toggleGroup(group.key)}
        className="w-full flex items-center gap-2 py-3 text-left hover:bg-[var(--c-surface-2)]/40 transition-colors"
      >
        <ChevronDown
          className={`w-4 h-4 text-[var(--c-text-tertiary)] transition-transform duration-200 ${isCollapsed ? '-rotate-90' : ''}`}
        />
        <span className="text-[length:var(--fs-md)] text-[var(--c-text)] font-medium truncate flex-1">
          {group.label}
        </span>
        <span className="text-[length:var(--fs-sm)] text-[var(--c-text-tertiary)] shrink-0">
          {group.files.length} 个文件
        </span>
      </button>
    )
  }

  return (
    <div className="flex-1 min-w-0 min-h-0 flex flex-col relative app-panel overflow-hidden">
      <div className="flex-1 overflow-hidden flex flex-col min-w-0">
      {/* 页面标题 */}
      <div className="px-8 pt-7 pb-5 shrink-0">
        <div className="max-w-[1200px] mx-auto w-full">
          <h1 className="text-[length:var(--fs-xl)] font-semibold text-[var(--c-text)] tracking-tight">文件库</h1>
          <p className="text-[length:var(--fs-md)] text-[var(--c-text-secondary)] mt-1.5">
            快捷查看、管理你的文件产出物。
          </p>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6">
        <div className="max-w-[1200px] mx-auto w-full pb-10">
          {/* 工具栏 */}
          <div className="flex items-center justify-between gap-3 pb-4 mb-4">
          <div className="relative">
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              className="appearance-none h-9 pl-3 pr-8 rounded-[var(--radius-control)] border border-[var(--c-border)] bg-[var(--c-surface)] text-[var(--c-text)] text-[length:var(--fs-md)] cursor-pointer hover:border-[var(--c-border-strong)] focus:outline-none focus:ring-1 focus:ring-[var(--c-accent)]/20 focus:border-[var(--c-accent)]/60 transition-colors"
            >
              {OUTPUT_TYPE_FILTERS.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
            <ChevronDown size={14} className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-[var(--c-text-tertiary)]" />
          </div>

          <div className="flex items-center gap-2">
            {/* 分组/排序下拉 */}
            <div className="relative">
              <div className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--c-text-secondary)] flex items-center pointer-events-none">
                <SortIcon type={currentSortOption.icon} />
              </div>
              <select
                value={sortKey}
                onChange={(e) => setSortKey(e.target.value as OutputSortKey)}
                className="appearance-none h-9 pl-8 pr-8 rounded-[var(--radius-control)] border border-[var(--c-border)] bg-[var(--c-surface)] text-[var(--c-text)] text-[length:var(--fs-md)] cursor-pointer hover:border-[var(--c-border-strong)] focus:outline-none focus:ring-1 focus:ring-[var(--c-accent)]/20 focus:border-[var(--c-accent)]/60 transition-colors"
              >
                {SORT_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
              <ChevronDown size={14} className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-[var(--c-text-tertiary)]" />
            </div>

            {/* 搜索框 / 搜索按钮 */}
            {showSearch ? (
              <div className="relative w-[220px]">
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="搜索文件名"
                  autoFocus
                  onBlur={() => !query && setShowSearch(false)}
                  className="w-full h-9 pl-3 pr-8 rounded-[var(--radius-md)] border border-[var(--c-border)] bg-[var(--c-surface)] text-[length:var(--fs-md)] text-[var(--c-text)] placeholder:text-[var(--c-text-tertiary)] outline-none transition-colors hover:border-[var(--c-border-strong)] focus:border-[var(--c-accent)]/60 focus:ring-1 focus:ring-[var(--c-accent)]/20"
                />
                <Search size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-[var(--c-text-tertiary)]" />
              </div>
            ) : (
              <button
                onClick={() => setShowSearch(true)}
                className="w-9 h-9 flex items-center justify-center rounded-[var(--radius-control)] border border-[var(--c-border)] bg-[var(--c-surface)] text-[var(--c-text-secondary)] hover:text-[var(--c-text)] hover:border-[var(--c-border-strong)] transition-colors"
                title="搜索"
              >
                <Search size={16} />
              </button>
            )}
          </div>
        </div>

        {/* 文件列表区域 */}
        <div>
          {error && (
            <div className="py-12 text-center text-[var(--c-error)] text-[length:var(--fs-md)]">
              {error}
            </div>
          )}
          {!error && !loading && files.length === 0 && (
            <div className="py-16 text-center text-[length:var(--fs-md)] text-[var(--c-text-tertiary)]">
              暂无产出物。
            </div>
          )}
          {!error && !loading && files.length > 0 && filtered.length === 0 && (
            <div className="py-16 text-center text-[length:var(--fs-md)] text-[var(--c-text-tertiary)]">
              没有匹配的文件。
            </div>
          )}
          {loading && (
            <div className="py-12 text-center text-[length:var(--fs-md)] text-[var(--c-text-tertiary)]">
              加载中…
            </div>
          )}

          {/* 分组视图 */}
          {groups && !loading && !error && groups.length > 0 && (
            <div>
              {groups.map((group) => (
                <div key={group.key}>
                  {renderGroupHeader(group)}
                  {!collapsedGroups.has(group.key) && (
                    <div className="pl-6">
                      {group.files.map(renderRow)}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* 平铺视图 */}
          {!groups && !loading && !error && filtered.length > 0 && (
            <div>
              {filtered.map(renderRow)}
            </div>
          )}
        </div>
        </div>
      </div>

      {preview && (
        <PreviewModal
          file={preview}
          token={token}
          onClose={() => setPreview(null)}
        />
      )}
      </div>
    </div>
  )
}

function PreviewModal({
  file,
  token,
  onClose,
}: {
  file: OutputFile
  token?: string | null
  onClose: () => void
}) {
  const [blob, setBlob] = useState<Blob | null>(null)

  useEffect(() => {
    let cancelled = false
    const headers: Record<string, string> = {}
    if (token) {
      headers.Authorization = `Bearer ${token}`
    }
    fetch(outputRawUrl(file.workspace_id, file.rel_path), { headers })
      .then((r) => {
        if (!r.ok) throw new Error(String(r.status))
        return r.blob()
      })
      .then((b: Blob) => {
        if (!cancelled) setBlob(b)
      })
      .catch(() => {
        if (!cancelled) setBlob(null)
      })
    return () => { cancelled = true }
  }, [file, token])

  return (
    <div
      className="overlay-scrim fixed inset-0 z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        // 必须给确定高度（不能只用 max-h）：FilePreviewWithReview 根节点是 h-full，
        // 父级高度为 auto 时 h-full 解析为 auto，内部 overflow-y-auto 失效，
        // 表现为预览内容被 85vh 裁切且无法上下滚动。
        className="overlay-panel w-full max-w-4xl h-[85vh] flex flex-col rounded-[var(--radius-card)] overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {blob ? (
          <FilePreviewWithReview
            file={blob}
            fileName={file.name}
            token={token ?? undefined}
            onClose={onClose}
            className="w-full h-full"
          />
        ) : (
          <div className="flex-1 min-h-0 flex items-center justify-center text-[var(--c-text-tertiary)] text-[length:var(--fs-sm)]">
            加载中…
          </div>
        )}
      </div>
    </div>
  )
}
