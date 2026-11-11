/**
 * 文献知识库 · 左栏收敛区
 *
 * 检索 / 瘤种分类 / 发表年份 —— 全部操作收纳于此，中栏只做扫描。
 * 分类右侧给出该分类条目数，收敛前即可判断命中量。
 * 分类列表与年份数据来自 API facets，静态分类定义保留在前端。
 */

import { FieldLabel, Panel, SearchInput } from '../../components'
import { LITERATURE_CATEGORIES } from '../../data/mock-data'
import type { LiteratureFacets as LF } from '../../types/view'

interface LiteratureFacetsProps {
  keyword: string
  onKeywordChange: (v: string) => void
  category: string
  onCategoryChange: (v: string) => void
  year: string
  onYearChange: (v: string) => void
  facets: LF
}

export function LiteratureFacets({
  keyword,
  onKeywordChange,
  category,
  onCategoryChange,
  year,
  onYearChange,
  facets,
}: LiteratureFacetsProps) {
  // 按 name 查找 facet 计数
  const countOf = (name: string) => {
    if (name === '全部') return facets.categories.reduce((s, c) => s + c.count, 0)
    return facets.categories.find((c) => c.name === name)?.count ?? 0
  }

  const yearOptions = ['全部', ...facets.years.map((y) => String(y.name))]

  return (
    <div className="flex flex-col gap-3">
      <Panel className="p-3">
        <FieldLabel>检索</FieldLabel>
        <SearchInput
          value={keyword}
          onChange={onKeywordChange}
          placeholder="研究名称 / 药物 / 靶点..."
        />
      </Panel>

      <Panel className="p-3">
        <FieldLabel>瘤种分类</FieldLabel>
        {LITERATURE_CATEGORIES.map((c) => {
          const active = category === c
          return (
            <button
              key={c}
              type="button"
              onClick={() => onCategoryChange(c)}
              className={`w-full flex items-center gap-2.5 rounded-lg px-2.5 py-2 transition-all cursor-pointer ${
                active ? 'bg-[var(--c-surface-2)]' : 'hover:bg-[var(--c-surface-3)]'
              }`}
            >
              <span
                className={`h-2 w-2 rounded-full shrink-0 ${
                  active ? 'bg-[var(--c-accent)]' : 'bg-[var(--c-text-tertiary)] opacity-40'
                }`}
              />
              <span
                className={`text-[length:var(--fs-sm)] flex-1 text-left ${
                  active ? 'font-semibold mw-strong' : 'mw-muted'
                }`}
              >
                {c}
              </span>
              <span className="text-[length:var(--fs-sm)] mw-num mw-faint">{countOf(c)}</span>
            </button>
          )
        })}
      </Panel>

      <Panel className="p-3">
        <FieldLabel>发表年份</FieldLabel>
        <div className="flex flex-wrap gap-1.5 px-1">
          {yearOptions.map((y) => (
            <button
              key={y}
              type="button"
              onClick={() => onYearChange(y)}
              className={`rounded-full px-3 py-1 text-[length:var(--fs-sm)] mw-num transition-all cursor-pointer ${
                year === y
                  ? 'bg-[var(--c-text)] text-[var(--c-surface)]'
                  : 'mw-glass mw-faint hover:text-[var(--c-text)]'
              }`}
            >
              {y}
            </button>
          ))}
        </div>
      </Panel>
    </div>
  )
}
