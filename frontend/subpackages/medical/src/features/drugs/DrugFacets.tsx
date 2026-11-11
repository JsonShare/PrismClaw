/**
 * 药品知识库 · 左栏收敛区 + 相互作用核查工具
 *
 * 核查工具是窄切口工具：输入两种药物 → 输出行动提示（而非结论性诊断）。
 */

import { AlertCircle, AlertTriangle, CheckCircle2, ShieldCheck, type LucideIcon } from 'lucide-react'
import { FieldLabel, Panel, SearchInput, Select, toneClass, type Tone } from '../../components'
import { DRUG_CATEGORIES, DRUG_CATEGORY_TONE } from '../../data/mock-data'
import { checkInteraction } from '../../lib/format'
import type { DrugCategory, DrugFacets as DF, DrugOption, InteractionLevel } from '../../types/view'

const LEVEL_TONE: Record<InteractionLevel, Tone> = {
  high: 'danger',
  warn: 'warning',
  ok: 'success',
}

const LEVEL_ICON: Record<InteractionLevel, LucideIcon> = {
  high: AlertTriangle,
  warn: AlertCircle,
  ok: CheckCircle2,
}

interface DrugFacetsProps {
  facets: DF | null
  drugOptions: DrugOption[]
  keyword: string
  onKeywordChange: (v: string) => void
  category: string
  onCategoryChange: (v: string) => void
  essentialOnly: boolean
  onEssentialToggle: () => void
  /** 核查工具：药物 A / B 的名称 */
  checkA: string
  checkB: string
  onCheckAChange: (v: string) => void
  onCheckBChange: (v: string) => void
}

export function DrugFacets({
  facets,
  drugOptions,
  keyword,
  onKeywordChange,
  category,
  onCategoryChange,
  essentialOnly,
  onEssentialToggle,
  checkA,
  checkB,
  onCheckAChange,
  onCheckBChange,
}: DrugFacetsProps) {
  const result = checkInteraction(checkA, checkB, drugOptions)
  const essentialCount = facets?.essentialCount ?? 0

  const countOf = (c: string) => {
    if (!facets) return 0
    if (c === '全部') return facets.categories.reduce((s, fc) => s + fc.count, 0)
    return facets.categories.find((fc) => fc.name === c)?.count ?? 0
  }

  const resultTone = result ? toneClass(LEVEL_TONE[result.level]) : ''
  const ResultIcon = result ? LEVEL_ICON[result.level] : null

  return (
    <div className="flex flex-col gap-3">
      <Panel className="p-3">
        <FieldLabel>检索</FieldLabel>
        <SearchInput value={keyword} onChange={onKeywordChange} placeholder="药名 / 适应症..." />
      </Panel>

      <Panel className="p-3">
        <FieldLabel>药物分类</FieldLabel>
        {(['全部', ...DRUG_CATEGORIES] as const).map((c) => {
          const active = category === c
          const tone: Tone = c === '全部' ? 'neutral' : DRUG_CATEGORY_TONE[c as DrugCategory]
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
                className={`h-2 w-2 rounded-full shrink-0 ${toneClass(tone)} bg-[var(--mw-tone)] ${
                  c === '全部' ? 'opacity-40' : ''
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

        <button
          type="button"
          onClick={onEssentialToggle}
          className={`w-full flex items-center gap-2.5 rounded-lg px-2.5 py-2 mt-1 transition-all cursor-pointer ${
            essentialOnly ? 'bg-[var(--c-success-soft)]' : 'hover:bg-[var(--c-surface-3)]'
          }`}
        >
          <ShieldCheck
            className={`h-3.5 w-3.5 shrink-0 ${
              essentialOnly ? 'text-[var(--c-success)]' : 'text-[var(--c-text-secondary)]'
            }`}
          />
          <span
            className={`text-[length:var(--fs-sm)] flex-1 text-left ${
              essentialOnly ? 'font-semibold mw-strong' : 'mw-muted'
            }`}
          >
            仅看国家基药
          </span>
          <span
            className={`text-[length:var(--fs-sm)] mw-num ${
              essentialOnly ? 'text-[var(--c-success)] font-semibold' : 'mw-faint'
            }`}
          >
            {essentialCount}
          </span>
        </button>
      </Panel>

      {/* 相互作用核查：窄切口工具 */}
      <Panel className="p-3">
        <FieldLabel>相互作用核查</FieldLabel>
        <p className="text-[length:var(--fs-xs)] mw-faint px-2 mb-2.5">选择两种药物，即时输出核查提示</p>

        <div className="space-y-2">
          <Select
            value={checkA}
            onChange={onCheckAChange}
            options={drugOptions.map((d) => d.name)}
            className="w-full"
            aria-label="药物 A"
          />
          <Select
            value={checkB}
            onChange={onCheckBChange}
            options={drugOptions.map((d) => d.name)}
            className="w-full"
            aria-label="药物 B"
          />
        </div>

        {result && ResultIcon && (
          <div
            className={`rounded-lg border px-3 py-2.5 mt-3 flex items-start gap-2 ${resultTone} bg-[var(--mw-tone-soft)]`}
          >
            <ResultIcon className="h-3.5 w-3.5 shrink-0 mt-0.5 text-[var(--mw-tone)]" />
            <p className="text-[length:var(--fs-sm)] text-[var(--mw-tone)] leading-relaxed">
              {result.text}
            </p>
          </div>
        )}
      </Panel>
    </div>
  )
}
