import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Check, LoaderCircle, Search } from 'lucide-react'
import { authedUrl } from '../../lib/system-adapter'
import { monogram } from './monogram'

interface LocalizedText {
  en?: string
  zh?: string
}

export interface ExpertCatalogItem {
  id: string
  expertType: 'agent' | 'team'
  displayName: LocalizedText
  description: string | LocalizedText
  /** 服务端下发的头像 URL（`/api/experts/<id>/avatar`），需附加 `_token` 后使用。 */
  avatarUrl: string
}

export interface PluginSelectorProps {
  open: boolean
  onClose: () => void
  selected: string | null
  onChange: (expertId: string) => void
  experts: ExpertCatalogItem[]
  loading: boolean
  token: string | null
}

export function expertLocalizedText(value: string | LocalizedText): string {
  if (typeof value === 'string') return value
  return value.zh || value.en || ''
}

export default function PluginSelector({ open, onClose, selected, onChange, experts, loading, token }: PluginSelectorProps) {
  const [query, setQuery] = useState('')
  const panelRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return experts.filter((expert) => {
      if (!q) return true
      return expertLocalizedText(expert.displayName).toLowerCase().includes(q)
        || expertLocalizedText(expert.description).toLowerCase().includes(q)
    })
  }, [experts, query])

  // 分组展示：专家团在前、专家在后，空组不渲染
  const groups = useMemo(() => {
    const teams = filtered.filter((item) => item.expertType === 'team')
    const agents = filtered.filter((item) => item.expertType !== 'team')
    return [
      { key: 'team', label: '专家团', items: teams },
      { key: 'agent', label: '专家', items: agents },
    ].filter((group) => group.items.length > 0)
  }, [filtered])

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) onClose()
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open, onClose])

  useEffect(() => {
    if (open) requestAnimationFrame(() => inputRef.current?.focus())
  }, [open])

  const handleSelect = useCallback((expertId: string) => {
    onChange(expertId)
    onClose()
    setQuery('')
  }, [onChange, onClose])

  if (!open) return null

  return (
    <div
      ref={panelRef}
      className="overlay-panel absolute left-[248px] bottom-full z-50 mb-2 flex w-[360px] flex-col
       overflow-hidden rounded-[var(--radius-card)]"
      style={{ maxWidth: 'calc(100vw - 24rem)', maxHeight: 'min(26rem, calc(100vh - 6rem))' }}
    >
      <div className="shrink-0 px-3 pb-2 pt-3">
        <div className="relative">
          <Search
            size={16}
            strokeWidth={2}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--c-text-secondary)]"
          />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索专家"
            className="h-10 w-full rounded-[var(--radius-md)] bg-[var(--c-surface-2)] pl-9 pr-3
             text-[length:var(--fs-sm)] text-[var(--c-text)] outline-none
             placeholder:text-[var(--c-text-tertiary)] focus:bg-[var(--c-surface-3)] transition-colors"
          />
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {loading && (
          <div className="flex items-center justify-center py-6 text-[var(--c-text-secondary)]">
            <LoaderCircle size={16} className="mr-2 animate-spin" />加载中…
          </div>
        )}
        {!loading && filtered.length === 0 && (
          <div className="py-6 text-center text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">
            {query ? '没有匹配的专家' : '暂无可用专家'}
          </div>
        )}
        {!loading && groups.map((group) => (
          <div key={group.key}>
            <div className="px-2.5 pb-1 pt-2 text-[length:var(--fs-xs)] font-semibold tracking-[0.08em] text-[var(--c-text-tertiary)]">
              {group.label}
            </div>
            {group.items.map((expert) => {
              const name = expertLocalizedText(expert.displayName) || expert.id
              const isSelected = selected === expert.id
              return (
                <button
                  key={expert.id}
                  type="button"
                  title={name}
                  onClick={() => handleSelect(expert.id)}
                  className={`flex w-full cursor-pointer items-center gap-3 rounded-[var(--radius-md)]
                   px-2.5 py-2 text-left transition-colors focus:outline-none ${
                    isSelected ? 'bg-[var(--c-select-bg)]' : 'hover:bg-[var(--c-hover-bg)] focus:bg-[var(--c-hover-bg)]'
                  }`}
                >
                  <span className="grid h-9 w-9 shrink-0 place-items-center overflow-hidden rounded-full
                   bg-[var(--c-accent)] text-[length:var(--fs-xs)] font-semibold text-[var(--c-btn-primary-fg)]">
                    {expert.avatarUrl
                      ? <img src={authedUrl(expert.avatarUrl, token)} alt="" className="h-full w-full object-cover" />
                      : monogram(name)}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[length:var(--fs-md)] font-medium text-[var(--c-text)]">
                    {name}
                  </span>
                  {isSelected && <Check size={16} strokeWidth={2.5} className="shrink-0 text-[#12A150]" />}
                </button>
              )
            })}
          </div>
        ))}
      </div>
    </div>
  )
}
