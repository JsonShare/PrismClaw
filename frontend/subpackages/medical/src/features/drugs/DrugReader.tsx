/**
 * 药品知识库 · 右栏说明书阅读器
 *
 * 三档切换：概述（适应症 / 机制 / 用法）→ 用法（相互作用提示）→ 安全（不良反应 / 禁忌）。
 * 主题色跟随药品分类 tone，切换药品时配色随之变化。
 */

import { AlertCircle, AlertTriangle, Pill as PillIcon } from 'lucide-react'
import { Btn, Num, Panel, Pill, toneClass, useToast } from '../../components'
import { DRUG_CATEGORY_TONE } from '../../data/mock-data'
import type { DrugEntry } from '../../types/view'

/** 阅读器分档 */
export type DrugTab = 'overview' | 'usage' | 'safety'

const TABS: Array<{ key: DrugTab; label: string }> = [
  { key: 'overview', label: '概述' },
  { key: 'usage', label: '用法' },
  { key: 'safety', label: '安全' },
]

interface DrugReaderProps {
  drug: DrugEntry
  tab: DrugTab
  onTabChange: (tab: DrugTab) => void
}

export function DrugReader({ drug, tab, onTabChange }: DrugReaderProps) {
  const { toast } = useToast()
  const tone = toneClass(DRUG_CATEGORY_TONE[drug.cat])

  return (
    <Panel className={`overflow-hidden mw-sticky ${tone}`}>
      {/* 头部：药品身份 */}
      <div
        className="px-5 py-4 border-b border-dashed border-[var(--c-border)]"
        style={{ background: 'color-mix(in srgb, var(--mw-tone) 6%, transparent)' }}
      >
        <div className="flex items-center gap-3">
          <div
            className="h-11 w-11 rounded-lg flex items-center justify-center shrink-0"
            style={{ background: 'color-mix(in srgb, var(--mw-tone) 14%, transparent)' }}
          >
            <PillIcon className="h-5 w-5 text-[var(--mw-tone)]" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 flex-wrap">
              <h3 className="text-[length:var(--fs-lg)] font-bold mw-strong truncate">{drug.name}</h3>
              {drug.essential && (
                <Pill tone="success" className="shrink-0">
                  基药
                </Pill>
              )}
              <Pill tone="neutral" className="shrink-0">
                妊娠 {drug.pregnancy}
              </Pill>
            </div>
            <p className="text-[length:var(--fs-sm)] mw-faint truncate mt-0.5">{drug.generic}</p>
          </div>
        </div>
      </div>

      {/* 分档切换 */}
      <div className="flex gap-1 p-2 border-b border-dashed border-[var(--c-border)]">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => onTabChange(t.key)}
            className={`flex-1 py-1.5 rounded-full text-[length:var(--fs-sm)] font-medium transition-all cursor-pointer ${
              tab === t.key
                ? 'text-[var(--c-surface)] bg-[var(--mw-tone)]'
                : 'mw-faint hover:bg-[var(--c-surface-3)] hover:text-[var(--c-text)]'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="p-5">
        {tab === 'overview' && (
          <div className="space-y-4">
            <div>
              <p className="text-[length:var(--fs-sm)] mw-faint tracking-wider mb-2">适应症</p>
              <div className="flex flex-wrap gap-1.5">
                {drug.indications.map((i) => (
                  <span
                    key={i}
                    className="text-[length:var(--fs-xs)] rounded-full px-2 py-0.5 bg-[var(--mw-tone-soft)] text-[var(--mw-tone)]"
                  >
                    {i}
                  </span>
                ))}
              </div>
            </div>

            <div>
              <p className="text-[length:var(--fs-sm)] mw-faint tracking-wider mb-2">作用机制</p>
              <p className="text-[length:var(--fs-md)] mw-muted leading-relaxed">{drug.mechanism}</p>
            </div>

            <Panel className="p-3.5">
              <p className="text-[length:var(--fs-sm)] mw-faint">用法用量</p>
              <Num className="text-[length:var(--fs-md)] font-semibold mw-strong mt-0.5 block">
                {drug.dosage}
              </Num>
            </Panel>
          </div>
        )}

        {tab === 'usage' && (
          <div className="space-y-4">
            <Panel className="p-3.5">
              <p className="text-[length:var(--fs-sm)] mw-faint">标准用法用量</p>
              <Num className="text-[length:var(--fs-md)] font-semibold mw-strong mt-0.5 block">
                {drug.dosage}
              </Num>
            </Panel>

            <div>
              <p className="text-[length:var(--fs-sm)] mw-faint tracking-wider mb-2">
                药物相互作用提示
              </p>
              <div className="space-y-2">
                {drug.interactions.map((it) => (
                  <div
                    key={it}
                    className="flex items-start gap-2 rounded-lg bg-[var(--c-warning-soft)] px-3 py-2.5"
                  >
                    <AlertTriangle className="h-3.5 w-3.5 text-[var(--c-warning)] shrink-0 mt-0.5" />
                    <p className="text-[length:var(--fs-sm)] mw-muted leading-relaxed">{it}</p>
                  </div>
                ))}
              </div>

              <Btn
                size="sm"
                className="mt-3 w-full justify-center"
                onClick={() => toast('已定位到左栏相互作用核查工具', 'info')}
              >
                在核查工具中组合验证
              </Btn>
            </div>
          </div>
        )}

        {tab === 'safety' && (
          <div className="space-y-4">
            <div>
              <p className="text-[length:var(--fs-sm)] mw-faint tracking-wider mb-2">常见不良反应</p>
              <div className="flex flex-wrap gap-1.5">
                {drug.sideEffects.map((se) => (
                  <Pill key={se} tone="warning">
                    {se}
                  </Pill>
                ))}
              </div>
            </div>

            <div>
              <p className="text-[length:var(--fs-sm)] mw-faint tracking-wider mb-2">禁忌与慎用</p>
              <div className="space-y-2">
                {drug.contraindications.map((c) => (
                  <div
                    key={c}
                    className="flex items-start gap-2 rounded-lg bg-[var(--c-danger-soft)] px-3 py-2.5"
                  >
                    <AlertCircle className="h-3.5 w-3.5 text-[var(--c-danger)] shrink-0 mt-0.5" />
                    <p className="text-[length:var(--fs-sm)] mw-muted leading-relaxed">{c}</p>
                  </div>
                ))}
              </div>
            </div>

            <p className="text-[length:var(--fs-xs)] mw-faint leading-relaxed">
              说明书信息仅供参考，临床用药请以最新版说明书及医嘱为准。
            </p>
          </div>
        )}
      </div>
    </Panel>
  )
}
