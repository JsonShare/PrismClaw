import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react'
import { Check, FileText, Image as ImageIcon, LoaderCircle, Search } from 'lucide-react'
import type { Message } from '../../types/chat'
import { conversationRefs, type ContextRef } from '../../lib/chat/contextRefs'
import { displayTitle, middleEllipsis, relativeDir } from '../../lib/chat/displayName'
import { useWorkspaceFileSearch } from '../../hooks/chat/useWorkspaceFileSearch'
import { HairlineDivider } from '../ui/HairlineDivider'

// ---------------------------------------------------------------------------
// ContextPicker - 引用文件面板（`@` 触发 / ➕ 菜单入口）
//
// 两组：工作区文件（空查询为最近产出物）、对话中的文件。
// `@` 模式下查询串来自输入框，面板不渲染搜索框；面板模式自持搜索框，
// 供不便于键盘触发的移动端使用。选中即 chip，正文不留 `@` 标记。
// ---------------------------------------------------------------------------

export interface ContextPickerHandle {
  handleKeyDown: (e: React.KeyboardEvent) => boolean
}

export interface ContextPickerProps {
  open: boolean
  /** 有值 = 触发模式（查询串来自输入框）；undefined = 面板模式 */
  query?: string
  workspaceId: string
  token: string | null
  messages: Message[]
  selectedPaths: string[]
  maxSelected: number
  onToggle: (ref: ContextRef) => void
  onClose: () => void
  /** 触发面板贴输入框左缘；➕ 飞出面贴在菜单右侧 */
  position?: 'anchor' | 'flyout'
}

function matchesQuery(ref: ContextRef, query: string): boolean {
  if (!query) return true
  const q = query.toLowerCase()
  const title = displayTitle(ref).toLowerCase()
  const dir = relativeDir(ref.path).toLowerCase()
  return title.includes(q) || dir.includes(q)
}

const ContextPicker = forwardRef<ContextPickerHandle, ContextPickerProps>(function ContextPicker(
  {
    open,
    query,
    workspaceId,
    token,
    messages,
    selectedPaths,
    maxSelected,
    onToggle,
    onClose,
    position = 'anchor',
  },
  ref,
) {
  const triggerMode = query !== undefined
  const [search, setSearch] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const panelRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([])

  const effectiveQuery = triggerMode ? query : search
  const { files: workspaceFiles, loading, failed } = useWorkspaceFileSearch({
    token,
    workspaceId,
    query: effectiveQuery,
    enabled: open,
  })

  const conversationFiles = useMemo(
    () => (open ? conversationRefs(messages).filter((r) => matchesQuery(r, effectiveQuery)) : []),
    [open, messages, effectiveQuery],
  )

  // 同一文件可能既在产出物里又在对话附件里：按 path 去重，工作区组优先
  const conversationOnly = useMemo(() => {
    const workspacePaths = new Set(workspaceFiles.map((r) => r.path))
    return conversationFiles.filter((r) => !workspacePaths.has(r.path))
  }, [workspaceFiles, conversationFiles])

  const items = useMemo(
    () => [...workspaceFiles, ...conversationOnly],
    [workspaceFiles, conversationOnly],
  )
  const safeIndex = items.length === 0 ? 0 : Math.min(activeIndex, items.length - 1)

  useEffect(() => {
    setActiveIndex(0)
  }, [effectiveQuery])

  useEffect(() => {
    itemRefs.current[safeIndex]?.scrollIntoView({ block: 'nearest' })
  }, [safeIndex])

  // 面板模式：打开时聚焦搜索框，关闭时清空（下次打开从默认组开始）
  useEffect(() => {
    if (!open) {
      setSearch('')
      return
    }
    if (!triggerMode) requestAnimationFrame(() => inputRef.current?.focus())
  }, [open, triggerMode])

  // 点击外部关闭（触发模式由输入框统一管理，这里仅面板模式生效）
  useEffect(() => {
    if (!open || triggerMode) return
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) onClose()
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open, triggerMode, onClose])

  useImperativeHandle(
    ref,
    () => ({
      handleKeyDown: (e) => {
        switch (e.key) {
          case 'ArrowDown':
            if (items.length > 0) setActiveIndex((i) => (Math.min(i, items.length - 1) + 1) % items.length)
            return true
          case 'ArrowUp':
            if (items.length > 0) setActiveIndex((i) => (Math.min(i, items.length - 1) - 1 + items.length) % items.length)
            return true
          case 'Enter':
            if (items.length > 0) onToggle(items[safeIndex])
            return true
          default:
            return false
        }
      },
    }),
    [items, safeIndex, onToggle],
  )

  if (!open) return null

  const atLimit = selectedPaths.length >= maxSelected

  const renderItem = (item: ContextRef, index: number) => {
    const isSelected = selectedPaths.includes(item.path)
    const blocked = atLimit && !isSelected
    const title = displayTitle(item)
    const dir = relativeDir(item.path)
    return (
      <button
        key={item.path}
        ref={(el) => { itemRefs.current[index] = el }}
        type="button"
        onMouseDown={(e) => e.preventDefault()}
        onClick={() => { if (blocked) return; onToggle(item); if (triggerMode) onClose() }}
        title={dir ? `${dir}/${title}` : title}
        className={"group w-full flex items-start gap-2.5 px-2.5 py-1.5 rounded-[var(--radius-md)] text-left transition-colors focus:outline-none "
          + (blocked ? "opacity-40 cursor-not-allowed " : "cursor-pointer ")
          + (index === safeIndex
            ? "bg-[var(--c-surface-3)] text-[var(--c-text)]"
            : "text-[var(--c-text)] hover:bg-[var(--c-surface-2)]")}
      >
        <span className="shrink-0 w-5 h-5 rounded-[var(--radius-control)] bg-[var(--c-surface-2)] grid place-items-center mt-0.5">
          {item.type === 'image'
            ? <ImageIcon size={14} strokeWidth={2} className="text-[var(--c-text-secondary)]" />
            : <FileText size={14} strokeWidth={2} className="text-[var(--c-text-secondary)]" />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[length:var(--fs-sm)] font-medium truncate">{title}</div>
          {dir && dir !== title && (
            <div className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)] truncate mt-0.5">
              {middleEllipsis(dir, 48)}
            </div>
          )}
        </div>
        {isSelected && <Check size={14} strokeWidth={2.5} className="shrink-0 mt-1 text-[var(--c-accent)]" />}
      </button>
    )
  }

  return (
    <div
      ref={panelRef}
      className={"overlay-panel absolute bottom-full mb-2 w-80 flex flex-col rounded-[var(--radius-card)] overflow-hidden z-50 "
        + (position === 'flyout' ? 'left-[15.5rem]' : 'left-0')}
      style={{ maxWidth: 'min(calc(100vw - 2rem), 20rem)', maxHeight: 'min(20rem, 60vh)' }}
    >
      {!triggerMode && (
        <>
          <div className="shrink-0 px-2.5 pt-2.5 pb-1.5">
            <div className="relative">
              <Search
                size={14}
                strokeWidth={2}
                className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--c-text-secondary)] pointer-events-none"
              />
              <input
                ref={inputRef}
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={(e) => {
                  // 搜索框在 <form> 内，Enter 会触发隐式提交把消息发出去
                  if (e.key === 'Escape') {
                    e.preventDefault()
                    onClose()
                  } else if (e.key === 'Enter') {
                    e.preventDefault()
                    if (items.length > 0) onToggle(items[safeIndex])
                  } else if (e.key === 'ArrowDown') {
                    e.preventDefault()
                    if (items.length > 0) setActiveIndex((i) => (Math.min(i, items.length - 1) + 1) % items.length)
                  } else if (e.key === 'ArrowUp') {
                    e.preventDefault()
                    if (items.length > 0) setActiveIndex((i) => (Math.min(i, items.length - 1) - 1 + items.length) % items.length)
                  }
                }}
                placeholder="搜索工作区文件"
                className="w-full pl-8 pr-2.5 py-1.5 rounded-[var(--radius-md)] bg-[var(--c-surface-2)] text-[length:var(--fs-sm)] text-[var(--c-text)] placeholder:text-[var(--c-text-secondary)]/70 outline-none focus:bg-[var(--c-surface-3)] transition-colors"
              />
            </div>
          </div>
          <HairlineDivider className="mx-2 shrink-0" />
        </>
      )}

      <div className="min-h-0 overflow-y-auto p-1">
        {loading && (
          <div className="flex items-center justify-center py-6 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">
            <LoaderCircle size={14} className="animate-spin mr-2" />
            搜索中…
          </div>
        )}
        {!loading && items.length === 0 && (
          <div className="py-6 text-center text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">
            {failed ? '工作区文件加载失败' : effectiveQuery ? '没有匹配的文件' : '暂无可引用的文件'}
          </div>
        )}
        {!loading && workspaceFiles.length > 0 && (
          <>
            <div className="px-2.5 pt-1.5 pb-1 text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]">
              {effectiveQuery ? '工作区文件' : '最近产出物'}
            </div>
            {workspaceFiles.map((item, i) => renderItem(item, i))}
          </>
        )}
        {!loading && conversationOnly.length > 0 && (
          <>
            <div className="px-2.5 pt-2 pb-1 text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]">
              对话中的文件
            </div>
            {conversationOnly.map((item, i) => renderItem(item, workspaceFiles.length + i))}
          </>
        )}
      </div>

      {selectedPaths.length > 0 && (
        <div className="shrink-0 px-3 py-1.5 border-t border-[var(--c-border)]/50 text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]">
          已引用 {selectedPaths.length}/{maxSelected}
          {atLimit && <span className="ml-1 text-[var(--c-warning)]">已达上限</span>}
        </div>
      )}
    </div>
  )
})

export default ContextPicker
