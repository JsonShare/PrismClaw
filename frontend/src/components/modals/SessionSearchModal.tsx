import { useRef, useEffect } from 'react'
import { Search, MessageSquare, CornerDownLeft } from 'lucide-react'
import { PopoverShell } from './ModalShell'

export interface SessionSearchItem {
  id: string
  title: string
}

export interface SessionSearchModalProps {
  open: boolean
  onClose: () => void
  query: string
  onQueryChange: (v: string) => void
  results: SessionSearchItem[]
  activeIndex: number
  onActiveIndexChange: (idx: number) => void
  onSelect: (id: string) => void
  /** 总数（用于空态判断） */
  totalCount: number
}

/**
 * 会话搜索弹窗 — 从 Chat.tsx 提取。
 * 约转出 80 行 JSX。
 */
export function SessionSearchModal({
  open,
  onClose,
  query,
  onQueryChange,
  results,
  activeIndex,
  onActiveIndexChange,
  onSelect,
  totalCount,
}: SessionSearchModalProps) {
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open) {
      inputRef.current?.focus()
    }
  }, [open])

  return (
    <PopoverShell
      open={open}
      onClose={() => {
        onClose()
        onQueryChange('')
      }}
    >
      <div className="px-4 h-12 flex items-center gap-2">
        <Search size={16} className="text-[var(--c-text-secondary)]" />
        <input
          ref={inputRef}
          type="text"
          placeholder="搜索任务..."
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          className="flex-1 bg-transparent outline-none text-[length:var(--fs-md)] text-[var(--c-text)]
                     placeholder:text-[var(--c-text-secondary)]/75"
        />
      </div>

      <div className="px-4 py-2 text-[length:var(--fs-sm)] font-semibold text-[var(--c-text-secondary)]">
        最近
      </div>

      <div className="max-h-[360px] overflow-y-auto p-2">
        {totalCount === 0 ? (
          <div className="px-3 py-6 text-[length:var(--fs-md)] text-[var(--c-text-secondary)]">暂无任务</div>
        ) : results.length === 0 ? (
          <div className="px-3 py-6 text-[length:var(--fs-md)] text-[var(--c-text-secondary)]">未找到匹配任务</div>
        ) : (
          <div className="space-y-1">
            {results.slice(0, 20).map((c, idx) => {
              const selected = idx === activeIndex
              return (
                <button
                  key={c.id}
                  type="button"
                  onMouseEnter={() => onActiveIndexChange(idx)}
                  onClick={() => onSelect(c.id)}
                  className={`w-full flex items-center justify-between gap-3 px-3 py-2 rounded-[var(--radius-md)] transition-colors cursor-pointer ${
                    selected ? 'bg-[var(--c-surface)]' : 'hover:bg-[var(--c-surface-3)]'
                  }`}
                  title={c.title}
                >
                  <div className="min-w-0 flex items-center gap-3">
                    <div className="w-7 h-7 rounded-[var(--radius-md)] bg-[var(--c-select-bg)] border border-[var(--c-border)] grid place-items-center shadow-xs">
                      <MessageSquare size={14} className="text-[var(--c-text-secondary)]" />
                    </div>
                    <div className="min-w-0">
                      <div className="text-[length:var(--fs-md)] font-semibold text-[var(--c-text)] truncate">
                        {c.title}
                      </div>
                    </div>
                  </div>
                  {selected ? (
                    <CornerDownLeft size={16} className="text-[var(--c-text-secondary)]" />
                  ) : (
                    <span className="w-4" />
                  )}
                </button>
              )
            })}
          </div>
        )}
      </div>
    </PopoverShell>
  )
}
