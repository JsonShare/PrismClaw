import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Check, LoaderCircle, Search } from 'lucide-react'
import { monogram } from './monogram'

export interface SkillCatalogItem {
  id: string
  name: string
  nameZh: string | null
  description: string
  pluginName: string
  /** 所属包类型：agent=专家、team=专家团；纯技能包为 null */
  expertType: string | null
}

export interface SkillSelectorProps {
  open: boolean
  onClose: () => void
  selected: string[]
  onChange: (skillId: string) => void
  skills: SkillCatalogItem[]
  loading: boolean
}

export default function SkillSelector({ open, onClose, selected, onChange, skills, loading }: SkillSelectorProps) {
  const [query, setQuery] = useState('')
  const panelRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return skills
    return skills.filter((skill) =>
      skill.name.toLowerCase().includes(q)
      || (skill.nameZh ?? '').toLowerCase().includes(q)
      || skill.description.toLowerCase().includes(q)
      || skill.pluginName.toLowerCase().includes(q),
    )
  }, [skills, query])

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

  const handleSelect = useCallback((skill: SkillCatalogItem) => {
    onChange(skill.id)
    // 单选即关闭：多选只能重新打开弹窗再选一次，避免误连点
    onClose()
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
            placeholder="搜索技能"
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
            {query ? '没有匹配的技能' : '暂无可用技能'}
          </div>
        )}
        {!loading && filtered.map((skill) => {
          const isSelected = selected.includes(skill.id)
          const label = skill.nameZh || skill.name
          return (
            <button
              key={skill.id}
              type="button"
              title={skill.description || label}
              onClick={() => handleSelect(skill)}
              className={`flex w-full cursor-pointer items-start gap-3 rounded-[var(--radius-md)]
               px-2.5 py-2.5 text-left transition-colors focus:outline-none ${
                isSelected ? 'bg-[var(--c-select-bg)]' : 'hover:bg-[var(--c-hover-bg)] focus:bg-[var(--c-hover-bg)]'
              }`}
            >
              <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-[9px]
               bg-[var(--c-accent-soft)] text-[length:var(--fs-sm)] font-semibold text-[var(--c-accent)]">
                {monogram(label)}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[length:var(--fs-md)] font-medium text-[var(--c-text)]">
                  {label}
                </span>
                {skill.description && (
                  // 最多 3 行，超出省略；完整描述靠整行的 title 悬浮查看。
                  // 不要加 block：line-clamp 依赖 display:-webkit-box，两者抢同一属性。
                  <span className="mt-0.5 line-clamp-3 text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]">
                    {skill.description}
                  </span>
                )}
              </span>
              {isSelected && <Check size={16} strokeWidth={2.5} className="mt-1 shrink-0 text-[#12A150]" />}
            </button>
          )
        })}
      </div>
    </div>
  )
}
