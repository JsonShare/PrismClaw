/**
 * 文献知识库 · 中栏高密度行流
 *
 * 每行给出「期刊徽章 + 年份 + 瘤种 → 标题 → 作者 → 摘要两行」，
 * 供快速扫描；选中行交给右栏深读。
 */

import { Num, Panel, Pill } from '../../components'
import type { LiteratureEntry } from '../../types/view'

interface LiteratureListProps {
  rows: LiteratureEntry[]
  selectedId: string | null
  onSelect: (entry: LiteratureEntry) => void
}

export function LiteratureList({ rows, selectedId, onSelect }: LiteratureListProps) {
  return (
    <div className="space-y-2">
      {rows.map((p) => (
        <Panel
          key={p.id}
          hover
          active={selectedId === p.id}
          role="button"
          tabIndex={0}
          onClick={() => onSelect(p)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              onSelect(p)
            }
          }}
          className="px-4 py-3 cursor-pointer"
        >
          <div className="flex items-center gap-2 flex-wrap">
            <Pill tone="accent" className="font-semibold">
              {p.journal}
            </Pill>
            <Num className="text-[length:var(--fs-xs)] mw-faint">{p.year}</Num>
            <span className="text-[length:var(--fs-xs)] mw-faint">· {p.category}</span>
          </div>
          <h3 className="text-[length:var(--fs-md)] font-semibold mw-strong leading-snug mt-1.5">
            {p.title}
          </h3>
          <p className="text-[length:var(--fs-sm)] mw-faint mt-1 truncate">{p.authors}</p>
          <p className="text-[length:var(--fs-sm)] mw-muted mt-1.5 line-clamp-2 leading-relaxed">
            {p.abstract}
          </p>
        </Panel>
      ))}
    </div>
  )
}
