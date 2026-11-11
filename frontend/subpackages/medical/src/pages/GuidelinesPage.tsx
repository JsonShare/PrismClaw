/**
 * 指南知识库页 · 三栏（facet 220 / 行流 520 / 阅读器 1fr）
 *
 * 收藏态按 id 集合维护（Set<string>），行流与阅读器共用同一份状态。
 */

import { useCallback, useEffect, useState } from 'react'
import { FileText, Loader2 } from 'lucide-react'
import { EmptyState, PagePager, useToast } from '../components'
import { listGuidelines } from '../api/client'
import {
  GuidelineFacets,
  type GuidelineSort,
} from '../features/guidelines/GuidelineFacets'
import { GuidelineList } from '../features/guidelines/GuidelineList'
import { GuidelineReader } from '../features/guidelines/GuidelineReader'
import type { GuidelineEntry, GuidelineFacets as GF } from '../types/view'

const PAGE_SIZE = 8

const EMPTY_FACETS: GF = { categories: [] }

export function GuidelinesPage() {
  const { toast } = useToast()
  const [keyword, setKeyword] = useState('')
  const [category, setCategory] = useState('全部')
  const [sort, setSort] = useState<GuidelineSort>('year')
  const [selected, setSelected] = useState<GuidelineEntry | null>(null)
  const [page, setPage] = useState(1)
  const [favorites, setFavorites] = useState<ReadonlySet<string>>(() => new Set())

  const [rows, setRows] = useState<GuidelineEntry[]>([])
  const [total, setTotal] = useState(0)
  const [facets, setFacets] = useState<GF>(EMPTY_FACETS)
  const [loading, setLoading] = useState(true)

  const fetchGuidelines = useCallback(async () => {
    setLoading(true)
    try {
      const res = await listGuidelines({
        keyword: keyword.trim() || undefined,
        category: category === '全部' ? undefined : category,
        sort,
        page,
        pageSize: PAGE_SIZE,
      })
      setRows(res.items)
      setTotal(res.total)
      setFacets(res.facets)
    } finally {
      setLoading(false)
    }
  }, [keyword, category, sort, page])

  useEffect(() => { void fetchGuidelines() }, [fetchGuidelines])

  const toggleFavorite = (g: GuidelineEntry) => {
    setFavorites((prev) => {
      const next = new Set(prev)
      if (next.has(g.id)) {
        next.delete(g.id)
        toast('已取消收藏', 'info')
      } else {
        next.add(g.id)
        toast('已收藏指南', 'success')
      }
      return next
    })
  }

  const resetPage = (fn: (v: string) => void) => (v: string) => {
    fn(v)
    setPage(1)
  }

  return (
    <div>
      <h1 className="text-[length:var(--fs-xl)] font-bold mw-strong tracking-tight">指南知识库</h1>
      <p className="text-[length:var(--fs-sm)] mw-faint mt-1">
        临床实践指南速查 · NCCN / CSCO / ESMO · 版本实时更新
      </p>

      <div className="mw-cols-kb mt-4">
        <GuidelineFacets
          keyword={keyword}
          onKeywordChange={resetPage(setKeyword)}
          category={category}
          onCategoryChange={resetPage(setCategory)}
          sort={sort}
          onSortChange={(v) => {
            setSort(v)
            setPage(1)
          }}
          facets={facets}
        />

        <div className="min-w-0">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-5 w-5 animate-spin text-[var(--c-accent)]" />
            </div>
          ) : (
            <GuidelineList
              rows={rows}
              selectedId={selected?.id ?? null}
              favorites={favorites}
              onToggleFavorite={toggleFavorite}
              onSelect={setSelected}
            />
          )}
          <PagePager page={page} total={total} pageSize={PAGE_SIZE} onChange={setPage} />
        </div>

        {selected ? (
          <GuidelineReader
            entry={selected}
            favorite={favorites.has(selected.id)}
            onToggleFavorite={toggleFavorite}
          />
        ) : (
          <EmptyState
            icon={FileText}
            title="从左侧列表选择指南开始阅读"
            hint="核心推荐逐条呈现，支持收藏与下载"
            className="mw-sticky"
          />
        )}
      </div>
    </div>
  )
}
