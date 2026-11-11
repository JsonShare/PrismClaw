import { useEffect, useMemo, useRef, useState } from 'react'
import { MessageSquare, Zap } from 'lucide-react'
import clsx from 'clsx'
import type { Conversation, Theme } from '../../types'
import { fuzzyFilter } from '../../lib/fuzzy'

export type PaletteAction =
  | { kind: 'new-session' }
  | { kind: 'toggle-theme' }
  | { kind: 'toggle-sidebar' }
  | { kind: 'show-shortcuts' }

export type PaletteSelection =
  | { kind: 'session'; sessionId: string }
  | { kind: 'action'; action: PaletteAction }

interface ActionEntry {
  action: PaletteAction
  label: string
  shortcut?: string
}

interface Props {
  open: boolean
  conversations: Conversation[]
  theme: Theme
  onClose: () => void
  onSelect: (selection: PaletteSelection) => void
}

interface FlatRow {
  group: 'sessions' | 'actions'
  key: string
  label: string
  hint?: string
  selection: PaletteSelection
}

export default function CommandPalette({
  open,
  conversations,
  theme,
  onClose,
  onSelect,
}: Props) {
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  const actions = useMemo<ActionEntry[]>(
    () => [
      { action: { kind: 'new-session' }, label: '新建会话', shortcut: 'mod+n' },
      { action: { kind: 'toggle-theme' }, label: '切换主题' },
      { action: { kind: 'toggle-sidebar' }, label: '切换侧边栏', shortcut: 'mod+\\' },
      { action: { kind: 'show-shortcuts' }, label: '显示快捷键', shortcut: 'mod+/' },
    ],
    [],
  )

  const rows = useMemo<FlatRow[]>(() => {
    const sessionRows: FlatRow[] = fuzzyFilter(
      query,
      conversations,
      (c) => c.title || '新对话',
    ).map(({ item: c }) => ({
      group: 'sessions',
      key: `session:${c.id}`,
      label: c.title || '新对话',
      selection: { kind: 'session', sessionId: c.id },
    }))

    const actionRows: FlatRow[] = fuzzyFilter(
      query,
      actions,
      (a) => a.label,
    ).map(({ item: a }) => ({
      group: 'actions',
      key: `action:${a.action.kind}`,
      label: a.label,
      hint: a.shortcut,
      selection: { kind: 'action', action: a.action },
    }))

    return [...sessionRows, ...actionRows]
  }, [actions, conversations, query])

  useEffect(() => {
    if (!open) return
    setQuery('')
    setActiveIndex(0)
    requestAnimationFrame(() => inputRef.current?.focus())
  }, [open])

  useEffect(() => {
    setActiveIndex(0)
  }, [query])

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
      } else if (event.key === 'ArrowDown') {
        event.preventDefault()
        setActiveIndex((i) => (rows.length === 0 ? 0 : (i + 1) % rows.length))
      } else if (event.key === 'ArrowUp') {
        event.preventDefault()
        setActiveIndex((i) => (rows.length === 0 ? 0 : (i - 1 + rows.length) % rows.length))
      } else if (event.key === 'Enter') {
        event.preventDefault()
        const row = rows[activeIndex]
        if (row) {
          onSelect(row.selection)
          onClose()
        }
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [activeIndex, onClose, onSelect, open, rows])

  useEffect(() => {
    if (!open) return
    const el = listRef.current?.querySelector(
      `[data-index="${activeIndex}"]`,
    ) as HTMLElement | null
    el?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex, open])

  if (!open) return null

  const groups: Array<{ id: FlatRow['group']; label: string }> = [
    { id: 'sessions', label: '会话' },
    { id: 'actions', label: '操作' },
  ]

  return (
    <div
      className="overlay-scrim fixed inset-0 z-40 flex items-start justify-center pt-[10vh] px-4 animate-in"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="overlay-panel w-full max-w-xl rounded-[var(--radius-card)] overflow-hidden">
        <div className="px-3 py-2">
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索会话、操作…"
            className="w-full bg-transparent text-[length:var(--fs-md)] text-[var(--c-text)] placeholder:text-[var(--c-text-secondary)]/75
                       focus:outline-none"
          />
        </div>

        <div ref={listRef} className="max-h-[60vh] overflow-y-auto py-1">
          {rows.length === 0 && (
            <div className="px-4 py-8 text-center text-[length:var(--fs-md)] text-[var(--c-text-secondary)]">
              无匹配结果
            </div>
          )}

          {groups.map((g) => {
            const groupRows = rows.filter((r) => r.group === g.id)
            if (groupRows.length === 0) return null
            return (
              <div key={g.id} className="py-1">
                <div className="px-3 py-1 text-[length:var(--fs-sm)] uppercase tracking-wider font-semibold text-[var(--c-text-secondary)]/75">
                  {g.label}
                </div>
                {groupRows.map((row) => {
                  const flatIdx = rows.indexOf(row)
                  const isActive = flatIdx === activeIndex
                  return (
                    <button
                      key={row.key}
                      type="button"
                      data-index={flatIdx}
                      onMouseEnter={() => setActiveIndex(flatIdx)}
                      onClick={() => {
                        onSelect(row.selection)
                        onClose()
                      }}
                      className={clsx(
                        'w-full flex items-center gap-2 px-3 py-2 text-left text-[length:var(--fs-md)] cursor-pointer transition-colors',
                        isActive
                          ? 'bg-[var(--c-accent)]/10 text-[var(--c-accent)]'
                          : 'text-[var(--c-text)]',
                      )}
                    >
                      <RowIcon group={row.group} theme={theme} />
                      <span className="flex-1 truncate">{row.label}</span>
                      {row.hint && (
                        <span className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/85 font-mono uppercase">
                          {row.hint}
                        </span>
                      )}
                    </button>
                  )
                })}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

function RowIcon({
  group,
  theme,
}: {
  group: FlatRow['group']
  theme: Theme
}) {
  void theme
  if (group === 'sessions') {
    return <MessageSquare size={14} className="text-[var(--c-text-secondary)] shrink-0" />
  }
  return <Zap size={14} className="text-[var(--c-text-secondary)] shrink-0" />
}
