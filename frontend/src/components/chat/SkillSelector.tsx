import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { LoaderCircle, Check, Search } from 'lucide-react'
import type { SkillPickerItem } from '../../hooks/chat/useSkillList'
import { monogram } from './monogram'
import { useItemTooltip } from './useItemTooltip'
import { HairlineDivider } from '../ui/HairlineDivider'

// ---------------------------------------------------------------------------
// SkillSelector - chat input skill picker (panel mode)
//
// 不再包含触发按钮，仅渲染弹出面板。由外部 PlusMenu 触发显示。
// 视觉对齐参考设计：扁平搜索框（左侧搜索图标）、列表项 hover 浅灰、选中态浅灰 + Check。
// ---------------------------------------------------------------------------

export interface SkillSelectorProps {
  open: boolean
  onClose: () => void
  /** 已激活的技能（可多个；点已选中的项即取消） */
  selected: string[]
  onChange: (skillName: string) => void
  skills: SkillPickerItem[]
  loading: boolean
}

export default function SkillSelector({ open, onClose, selected, onChange, skills, loading }: SkillSelectorProps) {
  const [query, setQuery] = useState('')
  const panelRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const { show: showTip, hide: hideTip, render: tipEl } = useItemTooltip()

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return skills
    return skills.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        (s.nameZh ?? '').toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q),
    )
  }, [skills, query])

  // 点击外部关闭
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open, onClose])

  // 打开时自动聚焦搜索框
  useEffect(() => {
    if (open) requestAnimationFrame(() => inputRef.current?.focus())
  }, [open])

  // 面板关闭时清除可能残留的 tooltip（按钮卸载不一定触发 mouseleave）
  useEffect(() => {
    if (!open) hideTip()
  }, [open, hideTip])

  const handleSelect = useCallback(
    (skill: SkillPickerItem) => {
      onChange(skill.name)
      onClose()
      setQuery('')
    },
    [onChange, onClose],
  )

  return (
    <>
      {open && (
    <div
      ref={panelRef}
      className="overlay-panel absolute left-[15.5rem] bottom-full mb-2 w-80 flex flex-col rounded-[var(--radius-card)] overflow-hidden z-50"
      style={{ maxWidth: 'calc(100vw - 18rem)', maxHeight: 'min(26rem, calc(100vh - 6rem))' }}
    >
      {/* 扁平搜索框：融入 panel 顶部，左侧搜索图标，无明显边框 */}
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
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索技能"
            className="w-full pl-8 pr-2.5 py-1.5 rounded-[var(--radius-md)] bg-[var(--c-surface-2)] text-[length:var(--fs-sm)] text-[var(--c-text)] placeholder:text-[var(--c-text-secondary)]/70 outline-none focus:bg-[var(--c-surface-3)] transition-colors"
          />
        </div>
      </div>

      {/* 搜索框与列表的分隔 */}
      <HairlineDivider className="mx-2 shrink-0" />

      <div className="min-h-0 flex-1 overflow-y-auto p-1">
        {loading && (
          <div className="flex items-center justify-center py-6 text-[var(--c-text-secondary)]">
            <LoaderCircle size={16} className="animate-spin mr-2" />
            加载中…
          </div>
        )}
        {!loading && filtered.length === 0 && (
          <div className="py-6 text-center text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">
            {query ? '没有匹配的技能' : '暂无可用技能'}
          </div>
        )}
        {!loading && filtered.map((skill) => {
          const isSelected = selected.includes(skill.name)
          return (
            <button
              key={skill.id}
              type="button"
              onClick={() => handleSelect(skill)}
              onMouseEnter={(e) => showTip(e, skill.description)}
              onMouseLeave={hideTip}
              className={"w-full flex items-start gap-2 px-2.5 py-1.5 rounded-[var(--radius-md)] text-left transition-colors cursor-pointer focus:outline-none "
                + (isSelected
                  ? "bg-[var(--c-surface-3)] text-[var(--c-text)]"
                  : "text-[var(--c-text)] hover:bg-[var(--c-surface-2)] focus:bg-[var(--c-surface-2)]")}
            >
              <span className="shrink-0 w-5 h-5 rounded-[var(--radius-control)] bg-[var(--c-accent)] text-[var(--c-btn-primary-fg)] grid place-items-center text-[length:var(--fs-xs)] font-semibold mt-0.5">
                {monogram(skill.nameZh || skill.name)}
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-[length:var(--fs-sm)] font-medium truncate">{skill.nameZh || skill.name}</div>
                {skill.description && (
                  <div data-clamp-desc className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)] line-clamp-2 mt-0.5">
                    {skill.description}
                  </div>
                )}
              </div>
              {isSelected && (
                <Check size={13} strokeWidth={2.5} className="shrink-0 mt-0.5 text-[var(--c-accent)]" />
              )}
            </button>
          )
        })}
      </div>
    </div>
      )}
      {tipEl}
    </>
  )
}
