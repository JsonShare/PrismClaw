/**
 * 医学知识库 · 通用筛选芯片行
 *
 * 设计稿里是浅灰胶囊 + 右侧「全部筛选」；选中态用反色，避免引入新品牌色。
 */

import { SlidersHorizontal } from 'lucide-react'
import type { FilterChip } from './kb-utils'

interface ChipsRowProps {
  chips: FilterChip[]
  active: string | null
  onChange: (key: string | null) => void
}

export function ChipsRow({ chips, active, onChange }: ChipsRowProps) {
  return (
    <div className="mt-4 flex items-center gap-2 overflow-x-auto pb-1">
      {chips.map((chip) => {
        const on = active === chip.key
        return (
          <button
            key={chip.key}
            type="button"
            onClick={() => onChange(on ? null : chip.key)}
            className={`h-8 shrink-0 cursor-pointer rounded-lg px-3 text-[length:var(--fs-sm)] transition-colors ${
              on
                ? 'bg-[var(--c-text)] text-[var(--c-surface)]'
                : 'bg-[var(--c-surface-3)] mw-muted hover:text-[var(--c-text)]'
            }`}
          >
            {chip.label}
          </button>
        )
      })}
      <button
        type="button"
        onClick={() => onChange(null)}
        className="ml-auto flex h-8 shrink-0 cursor-pointer items-center gap-1.5 rounded-lg border border-[var(--c-border)] px-3 text-[length:var(--fs-sm)] mw-muted transition-colors hover:text-[var(--c-text)]"
      >
        <SlidersHorizontal className="h-3.5 w-3.5" />
        全部筛选
      </button>
    </div>
  )
}
