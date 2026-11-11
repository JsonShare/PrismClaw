/**
 * 文献知识库页 · 三栏（facet 220 / 行流 520 / 阅读器 1fr）
 */

import { useCallback, useEffect, useState } from 'react'
import { FileText, Loader2, TrendingUp } from 'lucide-react'
import { EmptyState, Num, PagePager } from '../components'
import { listLiterature } from '../api/client'
import { LiteratureFacets } from '../features/literature/LiteratureFacets'
import { LiteratureList } from '../features/literature/LiteratureList'
import { LiteratureReader } from '../features/literature/LiteratureReader'
import type { LiteratureEntry, LiteratureFacets as LF } from '../types/view'

const PAGE_SIZE = 8

const EMPTY_FACETS: LF = { categories: [], years: [] }

export function LiteraturePage() {
  const [keyword, setKeyword] = useState('')
  const [category, setCategory] = useState('全部')
  const [year, setYear] = useState('全部')
  const [selected, setSelected] = useState<LiteratureEntry | null>(null)
  const [page, setPage] = useState(1)

  const [rows, setRows] = useState<LiteratureEntry[]>([])
  const [total, setTotal] = useState(0)
  const [facets, setFacets] = useState<LF>(EMPTY_FACETS)
  const [loading, setLoading] = useState(true)

  const fetchLiterature = useCallback(async () => {
    setLoading(true)
    try {
      const res = await listLiterature({
        keyword: keyword.trim() || undefined,
        category: category === '全部' ? undefined : category,
        year: year === '全部' ? undefined : year,
        page,
        pageSize: PAGE_SIZE,
      })
      setRows(res.items)
      setTotal(res.total)
      setFacets(res.facets)
    } finally {
      setLoading(false)
    }
  }, [keyword, category, year, page])

  useEffect(() => { void fetchLiterature() }, [fetchLiterature])

  const resetPage = (fn: (v: string) => void) => (v: string) => {
    fn(v)
    setPage(1)
  }

  return (
    <div>
      <h1 className="text-[length:var(--fs-xl)] font-bold mw-strong tracking-tight">文献知识库</h1>
      <p className="text-[length:var(--fs-sm)] mw-faint mt-1 flex items-center gap-1.5">
        <TrendingUp className="h-3 w-3 text-[var(--c-success)]" />
        肿瘤临床研究文献 · 收录{' '}
        <Num className="font-semibold mw-strong">{total}</Num> 篇
      </p>

      <div className="mw-cols-kb mt-4">
        <LiteratureFacets
          keyword={keyword}
          onKeywordChange={resetPage(setKeyword)}
          category={category}
          onCategoryChange={resetPage(setCategory)}
          year={year}
          onYearChange={resetPage(setYear)}
          facets={facets}
        />

        <div className="min-w-0">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-5 w-5 animate-spin text-[var(--c-accent)]" />
            </div>
          ) : (
            <LiteratureList rows={rows} selectedId={selected?.id ?? null} onSelect={setSelected} />
          )}
          <PagePager page={page} total={total} pageSize={PAGE_SIZE} onChange={setPage} />
        </div>

        {selected ? (
          <LiteratureReader entry={selected} />
        ) : (
          <EmptyState
            icon={FileText}
            title="从左侧列表选择文献开始阅读"
            hint="摘要全文与原文链接"
            className="mw-sticky"
          />
        )}
      </div>
    </div>
  )
}
