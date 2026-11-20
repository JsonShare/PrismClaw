/**
 * 医学知识库 · 指南流（设计稿「最新精选」+ 学科 Tab）
 *
 * 学科 Tab 由返回数据中出现的学科生成；发布机构/类型/地区走筛选芯片。
 * 点击行展开摘要与核心推荐。
 */

import { useEffect, useMemo, useState } from 'react'
import { ArrowDownWideNarrow, Loader2 } from 'lucide-react'
import { listGuidelines } from '../../api/client'
import type { GuidelineEntry } from '../../types/view'
import { ChipsRow } from './ChipsRow'
import { relativeDays, unique, useDebouncedValue } from './kb-utils'

const PAGE_SIZE = 20

type SortKey = 'year' | 'name'

export function GuidelineFeed({ keyword }: { keyword: string }) {
  const debounced = useDebouncedValue(keyword)
  const [items, setItems] = useState<GuidelineEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [sort, setSort] = useState<SortKey>('year')
  const [specialty, setSpecialty] = useState('全部')
  const [openId, setOpenId] = useState<string | null>(null)
  const [activeChip, setActiveChip] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setLoading(true)
    setOpenId(null)
    setActiveChip(null)
    listGuidelines({ keyword: debounced.trim() || undefined, sort, pageSize: PAGE_SIZE })
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
  }, [debounced, sort])

  const specialties = useMemo(
    () => ['全部', ...unique(items.map((i) => i.specialty).filter(Boolean)).slice(0, 5)],
    [items],
  )
  /** 重新取数后原学科可能已不存在，回落到「全部」 */
  const curSpecialty = specialties.includes(specialty) ? specialty : '全部'

  const chips = useMemo(() => {
    const types = unique(items.map((i) => i.docType).filter(Boolean)).map((t) => ({
      key: `d:${t}`,
      label: t,
    }))
    const regions = unique(items.map((i) => i.region).filter(Boolean)).map((r) => ({
      key: `r:${r}`,
      label: r,
    }))
    return [...types, ...regions]
  }, [items])

  const rows = useMemo(
    () =>
      items.filter((i) => {
        if (curSpecialty !== '全部' && i.specialty !== curSpecialty) return false
        if (!activeChip) return true
        const value = activeChip.slice(2)
        return activeChip.startsWith('d:') ? i.docType === value : i.region === value
      }),
    [items, curSpecialty, activeChip],
  )

  return (
    <section>
      <div className="flex items-center justify-between gap-4">
        <div className="flex min-w-0 items-center gap-6">
          <h2 className="shrink-0 text-[length:var(--fs-lg)] font-semibold mw-strong">最新精选</h2>
          <div className="flex items-center gap-5 overflow-x-auto">
            {specialties.map((s) => {
              const on = s === curSpecialty
              return (
                <button
                  key={s}
                  type="button"
                  onClick={() => {
                    setSpecialty(s)
                    setOpenId(null)
                  }}
                  className={`relative shrink-0 cursor-pointer pb-1.5 text-[length:var(--fs-sm)] transition-colors ${
                    on ? 'font-semibold mw-strong' : 'mw-muted hover:text-[var(--c-text)]'
                  }`}
                >
                  {s}
                  {on && (
                    <span className="absolute -bottom-[3px] left-0 h-0.5 w-5 rounded-full bg-[var(--c-success)]" />
                  )}
                </button>
              )
            })}
          </div>
        </div>
        <button
          type="button"
          title={sort === 'year' ? '按发布时间排序' : '按名称排序'}
          onClick={() => setSort(sort === 'year' ? 'name' : 'year')}
          className="shrink-0 cursor-pointer p-1 mw-faint transition-colors hover:text-[var(--c-text)]"
        >
          <ArrowDownWideNarrow className="h-4 w-4" />
        </button>
      </div>

      <ChipsRow chips={chips} active={activeChip} onChange={setActiveChip} />

      {loading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-[var(--c-accent)]" />
        </div>
      ) : rows.length === 0 ? (
        <p className="py-16 text-center text-[length:var(--fs-sm)] mw-faint">未找到相关指南</p>
      ) : (
        <div className="mt-1 divide-y divide-[var(--c-border)]">
          {rows.map((item) => {
            const open = openId === item.id
            return (
              <div key={item.id}>
                <button
                  type="button"
                  onClick={() => setOpenId(open ? null : item.id)}
                  aria-expanded={open}
                  className="w-full cursor-pointer py-4 text-left"
                >
                  <div className="flex items-start gap-2">
                    {item.docType && (
                      <span className="mt-0.5 shrink-0 text-[length:var(--fs-xs)] font-semibold text-[var(--c-success)]">
                        {item.docType}
                      </span>
                    )}
                    <h3 className="min-w-0 flex-1 text-[length:var(--fs-md)] font-semibold leading-snug mw-strong line-clamp-2">
                      {item.title}
                    </h3>
                  </div>
                  {item.titleCn && item.titleCn !== item.title && (
                    <p className="mt-1 text-[length:var(--fs-sm)] mw-muted line-clamp-1">{item.titleCn}</p>
                  )}
                  <div className="mt-2.5 flex items-center gap-2 text-[length:var(--fs-xs)]">
                    <span className="min-w-0 truncate mw-muted">{item.authors || item.org}</span>
                    {item.specialty && (
                      <span className="shrink-0 rounded border border-[var(--c-border)] px-1.5 py-0.5 mw-faint">
                        {item.specialty}
                      </span>
                    )}
                    <span className="ml-auto shrink-0 mw-faint">
                      {item.ver ? `${item.ver} · ` : ''}
                      {relativeDays(item.publishedAt)}
                    </span>
                  </div>
                </button>
                {open && (
                  <div className="pb-4">
                    <p className="text-[length:var(--fs-sm)] leading-relaxed mw-muted">{item.abstract}</p>
                    {item.points.length > 0 && (
                      <ul className="mt-2 space-y-1">
                        {item.points.map((p) => (
                          <li
                            key={p}
                            className="flex gap-2 text-[length:var(--fs-sm)] mw-muted"
                          >
                            <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-[var(--c-success)]" />
                            {p}
                          </li>
                        ))}
                      </ul>
                    )}
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
