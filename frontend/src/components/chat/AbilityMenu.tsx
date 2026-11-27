import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { LoaderCircle } from 'lucide-react'
import type { SkillCatalogItem } from './SkillSelector'
import { monogram } from './monogram'

// `/` 只提供技能快捷选择；专家选择统一收口到输入区 `+` 菜单。
export type AbilityKind = 'skill'

export interface AbilityPick {
  kind: AbilityKind
  name: string
}

export interface AbilityMenuHandle {
  handleKeyDown: (e: React.KeyboardEvent) => boolean
}

interface AbilityItem extends AbilityPick {
  key: string
  label: string
  description: string
}

export interface AbilityMenuProps {
  query: string
  selected: AbilityPick[]
  skills: SkillCatalogItem[]
  loading: boolean
  onPick: (pick: AbilityPick) => void
}

const AbilityMenu = forwardRef<AbilityMenuHandle, AbilityMenuProps>(function AbilityMenu(
  { query, selected, skills, loading, onPick },
  ref,
) {
  const [activeIndex, setActiveIndex] = useState(0)
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([])
  const q = query.trim().toLowerCase()
  const items: AbilityItem[] = skills
    .filter((skill) => !q || skill.name.toLowerCase().includes(q) || (skill.nameZh ?? '').toLowerCase().includes(q) || skill.description.toLowerCase().includes(q))
    .map((skill) => ({
      key: `skill:${skill.id}`,
      kind: 'skill',
      name: skill.id,
      label: skill.nameZh || skill.name,
      description: skill.description,
    }))
  const safeIndex = items.length === 0 ? 0 : Math.min(activeIndex, items.length - 1)

  useEffect(() => setActiveIndex(0), [query])
  useEffect(() => {
    itemRefs.current[safeIndex]?.scrollIntoView({ block: 'nearest' })
  }, [safeIndex])

  useImperativeHandle(ref, () => ({
    handleKeyDown: (e) => {
      switch (e.key) {
        case 'ArrowDown':
          if (items.length > 0) setActiveIndex((i) => (Math.min(i, items.length - 1) + 1) % items.length)
          return true
        case 'ArrowUp':
          if (items.length > 0) setActiveIndex((i) => (Math.min(i, items.length - 1) - 1 + items.length) % items.length)
          return true
        case 'Enter':
        case 'Tab':
          if (items.length > 0) onPick(items[safeIndex])
          return true
        default:
          return false
      }
    },
  }), [items, safeIndex, onPick])

  return (
    <div
      className="overlay-panel absolute left-0 bottom-full mb-2 w-80 flex flex-col rounded-[var(--radius-card)] overflow-hidden z-50"
      style={{ maxWidth: 'calc(100vw - 4rem)', maxHeight: 'min(20rem, calc(100vh - 7rem))' }}
    >
      <div className="min-h-0 overflow-y-auto p-1">
        {loading && (
          <div className="flex items-center justify-center py-6 text-[var(--c-text-secondary)]">
            <LoaderCircle size={16} className="animate-spin mr-2" />加载中…
          </div>
        )}
        {!loading && items.length === 0 && (
          <div className="py-6 text-center text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">没有匹配的技能</div>
        )}
        {!loading && items.map((item, index) => {
          const isSelected = selected.some((s) => s.kind === item.kind && s.name === item.name)
          return (
            <button
              key={item.key}
              ref={(el) => { itemRefs.current[index] = el }}
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => onPick({ kind: item.kind, name: item.name })}
              className={"w-full flex items-start gap-2.5 px-2.5 py-1.5 rounded-[var(--radius-md)] text-left transition-colors cursor-pointer focus:outline-none "
                + (index === safeIndex ? "bg-[var(--c-surface-3)]" : "hover:bg-[var(--c-surface-2)]")}
            >
              <span className="shrink-0 w-6 h-6 rounded-[var(--radius-control)] bg-[var(--c-accent)] text-[var(--c-btn-primary-fg)] grid place-items-center text-[length:var(--fs-sm)] font-semibold mt-0.5">
                {monogram(item.label)}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[length:var(--fs-sm)] font-medium truncate">{item.label}</span>
                {item.description && <span className="block text-[length:var(--fs-xs)] text-[var(--c-text-secondary)] truncate mt-0.5">{item.description}</span>}
              </span>
              {isSelected && <span className="shrink-0 text-[length:var(--fs-xs)] text-[var(--c-accent)] mt-1">已选</span>}
            </button>
          )
        })}
      </div>
    </div>
  )
})

export default AbilityMenu
