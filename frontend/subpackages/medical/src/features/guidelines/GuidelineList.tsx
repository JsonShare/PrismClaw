/**
 * 指南知识库 · 中栏行流
 *
 * 行内收藏按钮独立于整行选中（stopPropagation），避免「想收藏却跳转了详情」。
 */

import { Bookmark, ClipboardList } from 'lucide-react'
import { EmptyState, Num, Panel } from '../../components'
import type { GuidelineEntry } from '../../types/view'
import { OrgBadge } from './GuidelineFacets'

interface GuidelineListProps {
  rows: GuidelineEntry[]
  selectedId: string | null
  /** 已收藏 id 集合 */
  favorites: ReadonlySet<string>
  onToggleFavorite: (entry: GuidelineEntry) => void
  onSelect: (entry: GuidelineEntry) => void
}

export function GuidelineList({
  rows,
  selectedId,
  favorites,
  onToggleFavorite,
  onSelect,
}: GuidelineListProps) {
  if (rows.length === 0) {
    return (
      <EmptyState icon={ClipboardList} title="没有匹配的指南，试试其他关键词" />
    )
  }

  return (
    <div className="space-y-2">
      {rows.map((g) => (
        <Panel
          key={g.id}
          hover
          active={selectedId === g.id}
          role="button"
          tabIndex={0}
          onClick={() => onSelect(g)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              onSelect(g)
            }
          }}
          className="px-4 py-3 cursor-pointer"
        >
          <div className="flex items-start gap-3">
            <OrgBadge cat={g.cat} className="mt-0.5 h-6 w-9 text-[length:var(--fs-xs)]" />

            <div className="flex-1 min-w-0">
              <h3 className="text-[length:var(--fs-md)] font-semibold mw-strong leading-snug">{g.title}</h3>
              <p className="text-[length:var(--fs-sm)] mw-muted mt-1 line-clamp-2 leading-relaxed">
                {g.abstract}
              </p>
              <p className="text-[length:var(--fs-xs)] mw-faint mt-1.5">{g.points.length} 条核心推荐</p>
            </div>

            <div className="flex flex-col items-end gap-1.5 shrink-0">
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation()
                  onToggleFavorite(g)
                }}
                title={favorites.has(g.id) ? '取消收藏' : '收藏'}
                aria-label={favorites.has(g.id) ? '取消收藏' : '收藏'}
                className="rounded-full p-1.5 hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer"
              >
                <Bookmark
                  className={`h-4 w-4 ${
                    favorites.has(g.id)
                      ? 'text-[var(--c-accent)] fill-[var(--c-accent)]'
                      : 'text-[var(--c-text-secondary)]'
                  }`}
                />
              </button>
              <Num className="text-[length:var(--fs-xs)] mw-faint">{g.ver}</Num>
            </div>
          </div>
        </Panel>
      ))}
    </div>
  )
}
