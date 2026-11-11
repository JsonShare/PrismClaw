/**
 * 药品知识库页 · 三栏（facet 220 / 行流 520 / 说明书 1fr）
 */

import { useCallback, useEffect, useState } from 'react'
import { Loader2, Pill as PillIcon } from 'lucide-react'
import { EmptyState, Num, PagePager } from '../components'
import { listDrugs, listDrugOptions } from '../api/client'
import { DrugFacets } from '../features/drugs/DrugFacets'
import { DrugList } from '../features/drugs/DrugList'
import { DrugReader, type DrugTab } from '../features/drugs/DrugReader'
import type { DrugEntry, DrugFacets as DF, DrugOption } from '../types/view'

const PAGE_SIZE = 8

export function DrugsPage() {
  const [keyword, setKeyword] = useState('')
  const [category, setCategory] = useState('全部')
  const [essentialOnly, setEssentialOnly] = useState(false)
  const [selected, setSelected] = useState<DrugEntry | null>(null)
  const [tab, setTab] = useState<DrugTab>('overview')
  const [page, setPage] = useState(1)
  const [checkA, setCheckA] = useState('曲妥珠单抗')
  const [checkB, setCheckB] = useState('紫杉醇')

  const [rows, setRows] = useState<DrugEntry[]>([])
  const [total, setTotal] = useState(0)
  const [facets, setFacets] = useState<DF | null>(null)
  const [drugOptions, setDrugOptions] = useState<DrugOption[]>([])
  const [loading, setLoading] = useState(true)

  const fetchDrugs = useCallback(async () => {
    setLoading(true)
    try {
      const [paged, opts] = await Promise.all([
        listDrugs({ keyword: keyword || undefined, category: category === '全部' ? undefined : category, essentialOnly: essentialOnly || undefined, page, pageSize: PAGE_SIZE }),
        listDrugOptions(),
      ])
      setRows(paged.items)
      setTotal(paged.total)
      setFacets(paged.facets)
      setDrugOptions(opts)
    } finally {
      setLoading(false)
    }
  }, [keyword, category, essentialOnly, page])

  useEffect(() => { void fetchDrugs() }, [fetchDrugs])

  return (
    <div>
      <h1 className="text-[length:var(--fs-xl)] font-bold mw-strong tracking-tight">药品知识库</h1>
      <p className="text-[length:var(--fs-sm)] mw-faint mt-1">
        肿瘤常用药品速查 · 收录 <Num className="font-semibold mw-strong">{total}</Num>{' '}
        种药物完整说明书数据
      </p>

      <div className="mw-cols-kb mt-4">
        <DrugFacets
          facets={facets}
          drugOptions={drugOptions}
          keyword={keyword}
          onKeywordChange={(v) => {
            setKeyword(v)
            setPage(1)
          }}
          category={category}
          onCategoryChange={(v) => {
            setCategory(v)
            setPage(1)
          }}
          essentialOnly={essentialOnly}
          onEssentialToggle={() => {
            setEssentialOnly((v) => !v)
            setPage(1)
          }}
          checkA={checkA}
          checkB={checkB}
          onCheckAChange={setCheckA}
          onCheckBChange={setCheckB}
        />

        <div className="min-w-0">
          {loading && rows.length === 0 ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="h-5 w-5 animate-spin text-[var(--c-accent)]" />
            </div>
          ) : (
            <>
              <DrugList
                rows={rows}
                selectedId={selected?.id ?? null}
                onSelect={(d) => {
                  setSelected(d)
                  setTab('overview')
                }}
              />
              <PagePager page={page} total={total} pageSize={PAGE_SIZE} onChange={setPage} />
            </>
          )}
        </div>

        {selected ? (
          <DrugReader drug={selected} tab={tab} onTabChange={setTab} />
        ) : (
          <EmptyState
            icon={PillIcon}
            title="从左侧列表选择药品查看说明书"
            hint="概述 / 用法 / 安全三栏速查"
            className="mw-sticky"
          />
        )}
      </div>
    </div>
  )
}
