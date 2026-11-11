import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { LoaderCircle } from 'lucide-react'
import type { SkillPickerItem } from '../../hooks/chat/useSkillList'
import { pluginDescriptionOf, pluginGroups, standaloneSkills } from '../../lib/chat/skillGroups'
import { monogram } from './monogram'

// ---------------------------------------------------------------------------
// AbilityMenu - 输入框 `/` 能力面板
//
// 技能与专家同源（同一份 /api/skills/list），分两组展示：
//   - 技能：无插件前缀的独立技能；
//   - 专家：插件技能按 id 前缀聚合。
// 选中只由父组件设置 chip，不向正文插入文本——chip 是技能/插件的唯一真源。
// 面板不渲染搜索框：查询串来自输入框本身，焦点始终留在 textarea。
// ---------------------------------------------------------------------------

export type AbilityKind = 'skill' | 'plugin'

export interface AbilityPick {
  kind: AbilityKind
  name: string
}

export interface AbilityMenuHandle {
  /** 返回 true 表示已消费该按键（调用方需 preventDefault） */
  handleKeyDown: (e: React.KeyboardEvent) => boolean
}

interface AbilityItem extends AbilityPick {
  key: string
  label: string
  description: string
  emoji: string | null
}

export interface AbilityMenuProps {
  query: string
  /** 已激活的能力（可多个，用于标记「已选」） */
  selected: AbilityPick[]
  skills: SkillPickerItem[]
  loading: boolean
  onPick: (pick: AbilityPick) => void
}

const AbilityMenu = forwardRef<AbilityMenuHandle, AbilityMenuProps>(function AbilityMenu(
  { query, selected, skills, loading, onPick },
  ref,
) {
  const [activeIndex, setActiveIndex] = useState(0)
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([])

  const skillItems: AbilityItem[] = standaloneSkills(skills, query).map((s) => ({
    key: `skill:${s.name}`,
    kind: 'skill',
    name: s.name,
    label: s.nameZh || s.name,
    description: s.description,
    emoji: s.emoji,
  }))

  const pluginItems: AbilityItem[] = pluginGroups(skills, query).map((g) => ({
    key: `plugin:${g.name}`,
    kind: 'plugin',
    name: g.name,
    label: g.name,
    description: pluginDescriptionOf(g.skills),
    emoji: null,
  }))

  const items = [...skillItems, ...pluginItems]
  const safeIndex = items.length === 0 ? 0 : Math.min(activeIndex, items.length - 1)

  useEffect(() => {
    setActiveIndex(0)
  }, [query])

  useEffect(() => {
    itemRefs.current[safeIndex]?.scrollIntoView({ block: 'nearest' })
  }, [safeIndex])

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
          case 'Tab':
            if (items.length > 0) onPick(items[safeIndex])
            return true
          default:
            return false
        }
      },
    }),
    [items, safeIndex, onPick],
  )

  const renderItem = (item: AbilityItem, index: number) => {
    const isSelected = selected.some((s) => s.kind === item.kind && s.name === item.name)
    return (
      <button
        key={item.key}
        ref={(el) => { itemRefs.current[index] = el }}
        type="button"
        // 保持 textarea 焦点，键盘导航状态才不会被 blur 打断
        onMouseDown={(e) => e.preventDefault()}
        onClick={() => onPick({ kind: item.kind, name: item.name })}
        className={"w-full flex items-start gap-2.5 px-2.5 py-1.5 rounded-[var(--radius-md)] text-left transition-colors cursor-pointer focus:outline-none "
          + (index === safeIndex
            ? "bg-[var(--c-surface-3)] text-[var(--c-text)]"
            : "text-[var(--c-text)] hover:bg-[var(--c-surface-2)]")}
      >
        <span className="shrink-0 w-6 h-6 rounded-[var(--radius-control)] bg-[var(--c-accent)] text-[var(--c-btn-primary-fg)] grid place-items-center text-[length:var(--fs-sm)] font-semibold mt-0.5">
          {item.emoji ?? monogram(item.label)}
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[length:var(--fs-sm)] font-medium truncate">{item.label}</div>
          {item.description && (
            <div className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)] truncate mt-0.5">
              {item.description}
            </div>
          )}
        </div>
        {isSelected && (
          <span className="shrink-0 text-[length:var(--fs-xs)] text-[var(--c-accent)] mt-1">已选</span>
        )}
      </button>
    )
  }

  return (
    <div
      className="overlay-panel absolute left-0 bottom-full mb-2 w-80 flex flex-col rounded-[var(--radius-card)] overflow-hidden z-50"
      style={{ maxWidth: 'calc(100vw - 4rem)', maxHeight: 'min(20rem, calc(100vh - 7rem))' }}
    >
      <div className="min-h-0 overflow-y-auto p-1">
        {loading && (
          <div className="flex items-center justify-center py-6 text-[var(--c-text-secondary)]">
            <LoaderCircle size={16} className="animate-spin mr-2" />
            加载中…
          </div>
        )}
        {!loading && items.length === 0 && (
          <div className="py-6 text-center text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">
            没有匹配的技能或专家
          </div>
        )}
        {!loading && skillItems.length > 0 && (
          <>
            <div className="px-2.5 pt-1.5 pb-1 text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]">技能</div>
            {skillItems.map((item, i) => renderItem(item, i))}
          </>
        )}
        {!loading && pluginItems.length > 0 && (
          <>
            <div className="px-2.5 pt-2 pb-1 text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]">专家</div>
            {pluginItems.map((item, i) => renderItem(item, skillItems.length + i))}
          </>
        )}
      </div>
    </div>
  )
})

export default AbilityMenu
