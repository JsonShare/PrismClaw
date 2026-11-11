/**
 * 指南知识库 · 左栏收敛区
 *
 * 发布机构 facet 用机构缩写色块（NCCN 蓝 / CSCO 粉 / ESMO 绿）替代抽象色点，
 * 让「来源机构」在扫描路径上一眼可辨。
 * 分类计数来自 API facets，静态机构元数据保留在前端。
 */

import { FileText } from 'lucide-react'
import { FieldLabel, Panel, SearchInput, toneClass } from '../../components'
import { GUIDELINE_CATEGORIES, GUIDELINE_ORG_META } from '../../data/mock-data'
import type { GuidelineCategory, GuidelineFacets as GF } from '../../types/view'

/** 排序方式 */
export type GuidelineSort = 'year' | 'name'

const SORT_OPTIONS: Array<{ value: GuidelineSort; label: string }> = [
  { value: 'year', label: '最新发布' },
  { value: 'name', label: '名称排序' },
]

interface GuidelineFacetsProps {
  keyword: string
  onKeywordChange: (v: string) => void
  category: string
  onCategoryChange: (v: string) => void
  sort: GuidelineSort
  onSortChange: (v: GuidelineSort) => void
  facets: GF
}

/** 机构缩写徽章：全站统一的 32×20 色块 */
export function OrgBadge({ cat, className }: { cat: GuidelineCategory | '全部'; className?: string }) {
  if (cat === '全部') {
    return (
      <span
        className={`h-5 w-8 rounded bg-[var(--c-surface-3)] flex items-center justify-center shrink-0 ${className ?? ''}`}
      >
        <FileText className="h-3 w-3 mw-faint" />
      </span>
    )
  }
  const meta = GUIDELINE_ORG_META[cat]
  return (
    <span
      className={`h-5 w-8 rounded text-[length:var(--fs-xs)] font-bold flex items-center justify-center shrink-0 ${toneClass(
        meta.tone,
      )} bg-[var(--mw-tone)] text-[var(--c-surface)] ${className ?? ''}`}
    >
      {meta.org}
    </span>
  )
}

export function GuidelineFacets({
  keyword,
  onKeywordChange,
  category,
  onCategoryChange,
  sort,
  onSortChange,
  facets,
}: GuidelineFacetsProps) {
  const countOf = (c: string) => {
    if (c === '全部') return facets.categories.reduce((s, fc) => s + fc.count, 0)
    return facets.categories.find((fc) => fc.name === c)?.count ?? 0
  }

  return (
    <div className="flex flex-col gap-3">
      <Panel className="p-3">
        <FieldLabel>检索</FieldLabel>
        <SearchInput value={keyword} onChange={onKeywordChange} placeholder="指南名称 / 核心推荐..." />
      </Panel>

      <Panel className="p-3">
        <FieldLabel>发布机构</FieldLabel>
        {(['全部', ...GUIDELINE_CATEGORIES] as const).map((c) => {
          const active = category === c
          const meta = c === '全部' ? null : GUIDELINE_ORG_META[c]
          return (
            <button
              key={c}
              type="button"
              onClick={() => onCategoryChange(c)}
              className={`w-full flex items-center gap-2.5 rounded-lg px-2.5 py-2 transition-all cursor-pointer ${
                active ? 'bg-[var(--c-surface-2)]' : 'hover:bg-[var(--c-surface-3)]'
              }`}
            >
              <OrgBadge cat={c} />
              <span
                className={`text-[length:var(--fs-sm)] flex-1 text-left ${
                  active ? 'font-semibold mw-strong' : 'mw-muted'
                }`}
              >
                {meta ? meta.full : '全部'}
              </span>
              <span className="text-[length:var(--fs-sm)] mw-num mw-faint">{countOf(c)}</span>
            </button>
          )
        })}
      </Panel>

      <Panel className="p-3">
        <FieldLabel>排序</FieldLabel>
        <div className="flex flex-wrap gap-1.5 px-1">
          {SORT_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              onClick={() => onSortChange(o.value)}
              className={`rounded-full px-3 py-1 text-[length:var(--fs-sm)] transition-all cursor-pointer ${
                sort === o.value
                  ? 'bg-[var(--c-text)] text-[var(--c-surface)]'
                  : 'mw-glass mw-faint hover:text-[var(--c-text)]'
              }`}
            >
              {o.label}
            </button>
          ))}
        </div>
      </Panel>
    </div>
  )
}
