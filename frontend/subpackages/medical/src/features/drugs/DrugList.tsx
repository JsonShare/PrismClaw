/**
 * 药品知识库 · 中栏行流
 *
 * 每行：分类色图标 + 药名（基药标记）+ 通用名 → 剂量 → 分类徽章 → 适应症前三枚。
 */

import { Pill as PillIcon } from 'lucide-react'
import { Num, Panel, Pill, toneClass } from '../../components'
import { DRUG_CATEGORY_TONE } from '../../data/mock-data'
import type { DrugEntry } from '../../types/view'

interface DrugListProps {
  rows: DrugEntry[]
  selectedId: string | null
  onSelect: (drug: DrugEntry) => void
}

export function DrugList({ rows, selectedId, onSelect }: DrugListProps) {
  return (
    <div className="space-y-2">
      {rows.map((d) => {
        const tone = toneClass(DRUG_CATEGORY_TONE[d.cat])
        return (
          <Panel
            key={d.id}
            hover
            active={selectedId === d.id}
            role="button"
            tabIndex={0}
            onClick={() => onSelect(d)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onSelect(d)
              }
            }}
            className={`px-4 py-3 cursor-pointer ${tone}`}
          >
            <div className="flex items-center gap-3">
              <div
                className="h-9 w-9 rounded-lg flex items-center justify-center shrink-0"
                style={{ background: 'color-mix(in srgb, var(--mw-tone) 12%, transparent)' }}
              >
                <PillIcon className="h-4 w-4 text-[var(--mw-tone)]" />
              </div>

              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  <p className="text-[length:var(--fs-md)] font-bold mw-strong truncate">{d.name}</p>
                  {d.essential && (
                    <Pill tone="success" className="shrink-0">
                      基药
                    </Pill>
                  )}
                </div>
                <p className="text-[length:var(--fs-sm)] mw-faint truncate mt-0.5">{d.generic}</p>
              </div>

              <div className="flex flex-col items-end gap-1 shrink-0">
                <span
                  className="text-[length:var(--fs-xs)] font-semibold px-2 py-0.5 rounded-full bg-[var(--mw-tone-soft)] text-[var(--mw-tone)]"
                >
                  {d.cat}
                </span>
                <Num className="text-[length:var(--fs-sm)] mw-muted">{d.dosage}</Num>
              </div>
            </div>

            <div className="flex flex-wrap gap-1.5 mt-2.5 pl-12">
              {d.indications.slice(0, 3).map((i) => (
                <span
                  key={i}
                  className="text-[length:var(--fs-xs)] mw-muted bg-[var(--c-surface-3)] rounded-full px-2 py-0.5"
                >
                  {i}
                </span>
              ))}
              {d.indications.length > 3 && (
                <span className="text-[length:var(--fs-xs)] mw-faint py-0.5">
                  +{d.indications.length - 3}
                </span>
              )}
            </div>
          </Panel>
        )
      })}
    </div>
  )
}
