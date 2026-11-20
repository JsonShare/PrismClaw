/**
 * 医学知识库 · 文献流（设计稿「最新精选」）
 *
 * 行结构：研究类型 + 英文标题 / 中文标题 / 期刊徽标与标识标签 / IF + 相对时间。
 * 点击行展开摘要，避免丢失原有正文能力。
 */

import { useEffect, useMemo, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { listLiterature } from '../../api/client'
import type { LiteratureEntry } from '../../types/view'
import { ChipsRow } from './ChipsRow'
import { journalColor, journalShort, relativeDays, unique, useDebouncedValue } from './kb-utils'

const PAGE_SIZE = 20

/** 页头右侧的三枚期刊徽标（与设计稿一致，固定展示权威期刊） */
const HEADER_JOURNALS = ['N Engl J Med', 'JAMA', 'BMJ']

export function LiteratureFeed({ keyword }: { keyword: string }) {
  const debounced = useDebouncedValue(keyword)
  const [items, setItems] = useState<LiteratureEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [openId, setOpenId] = useState<string | null>(null)
  const [activeChip, setActiveChip] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setLoading(true)
    setOpenId(null)
    setActiveChip(null)
    listLiterature({ keyword: debounced.trim() || undefined, pageSize: PAGE_SIZE })
      .then((res) => {
        if (alive) setItems(res.items)
      })
      .catch(() => {
        if (alive) setItems([])
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [debounced])

  const chips = useMemo(() => {
    const journals = unique(items.map((i) => journalShort(i.journal)))
      .slice(0, 3)
      .map((j) => ({ key: `j:${j}`, label: j }))
    const types = unique(items.map((i) => i.studyType).filter(Boolean))
      .slice(0, 5)
      .map((t) => ({ key: `t:${t}`, label: t }))
    return [...journals, ...types]
  }, [items])

  const rows = useMemo(() => {
    if (!activeChip) return items
    const value = activeChip.slice(2)
    return items.filter((i) =>
      activeChip.startsWith('j:')
        ? journalShort(i.journal) === value
        : i.studyType === value,
    )
  }, [items, activeChip])

  return (
    <section>
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="text-[length:var(--fs-lg)] font-semibold mw-strong">最新精选</h2>
        <div className="flex items-center gap-2 text-[length:var(--fs-xs)] mw-faint">
          <span>内容源自权威期刊</span>
          <span className="flex -space-x-1">
            {HEADER_JOURNALS.map((j) => (
              <span
                key={j}
                title={j}
                className="flex h-4 w-4 items-center justify-center rounded-full text-[7px] font-bold text-white ring-1 ring-[var(--c-surface)]"
                style={{ background: journalColor(j) }}
              >
                {journalShort(j).slice(0, 1)}
              </span>
            ))}
          </span>
        </div>
      </div>

      <ChipsRow chips={chips} active={activeChip} onChange={setActiveChip} />

      {loading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-[var(--c-accent)]" />
        </div>
      ) : rows.length === 0 ? (
        <p className="py-16 text-center text-[length:var(--fs-sm)] mw-faint">
          未找到相关文献
        </p>
      ) : (
        <div className="mt-1 divide-y divide-[var(--c-border)]">
          {rows.map((item) => {
            const open = openId === item.id
            const titleEn = item.titleEn || item.title
            const tags = item.journalTags ?? []
            return (
              <div key={item.id}>
                <button
                  type="button"
                  onClick={() => setOpenId(open ? null : item.id)}
                  aria-expanded={open}
                  className="w-full cursor-pointer py-4 text-left"
                >
                  <div className="flex items-start gap-2">
                    {item.studyType && (
                      <span className="mt-0.5 shrink-0 text-[length:var(--fs-xs)] font-semibold text-[var(--c-success)]">
                        {item.studyType}
                      </span>
                    )}
                    <h3 className="min-w-0 flex-1 text-[length:var(--fs-md)] font-semibold leading-snug mw-strong line-clamp-2">
                      {titleEn}
                    </h3>
                  </div>
                  {item.titleCn && item.titleCn !== titleEn && (
                    <p className="mt-1 text-[length:var(--fs-sm)] mw-muted line-clamp-1">{item.titleCn}</p>
                  )}
                  <div className="mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-[length:var(--fs-xs)]">
                    <span
                      className="flex h-4 w-4 items-center justify-center rounded-full text-[7px] font-bold text-white"
                      style={{ background: journalColor(item.journal) }}
                    >
                      {journalShort(item.journal).slice(0, 1)}
                    </span>
                    <span className="font-medium mw-strong">{item.journal}</span>
                    {tags.map((tag) => (
                      <span
                        key={tag}
                        className="rounded border border-[var(--c-border)] px-1.5 py-0.5 mw-faint"
                      >
                        {tag}
                      </span>
                    ))}
                    <span className="ml-auto flex items-center gap-2 mw-faint">
                      {(item.impactFactor ?? 0) > 0 && <span>IF {item.impactFactor}</span>}
                      <span>{relativeDays(item.publishedAt)}</span>
                    </span>
                  </div>
                </button>
                {open && (
                  <div className="pb-4 pl-0 pr-2">
                    <p className="text-[length:var(--fs-sm)] leading-relaxed mw-muted">{item.abstract}</p>
                    <p className="mt-2 text-[length:var(--fs-xs)] mw-faint">
                      {item.authors}
                      {item.doi && <span className="ml-2">DOI: {item.doi}</span>}
                    </p>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}
