/**
 * 文献知识库 · 右栏阅读器
 *
 * 深读区：摘要全文 + 关键词 + DOI + 原文跳转。宽屏下吸顶，随中栏滚动切换。
 */

import { ExternalLink, Tag } from 'lucide-react'
import { Btn, Num, Panel, Pill } from '../../components'
import type { LiteratureEntry } from '../../types/view'

export function LiteratureReader({ entry }: { entry: LiteratureEntry }) {
  return (
    <Panel className="overflow-hidden mw-sticky">
      <div className="px-5 py-4 border-b border-dashed border-[var(--c-border)] bg-[var(--c-accent-soft)]">
        <div className="flex items-center gap-2 flex-wrap">
          <Pill tone="accent" className="font-semibold">
            {entry.journal}
          </Pill>
          <Num className="text-[length:var(--fs-xs)] mw-faint">{entry.year}</Num>
          <span className="text-[length:var(--fs-xs)] mw-faint">· {entry.category}</span>
        </div>
        <h3 className="text-[length:var(--fs-lg)] font-bold mw-strong leading-snug mt-2">{entry.title}</h3>
        <p className="text-[length:var(--fs-sm)] mw-faint mt-1.5">{entry.authors}</p>
      </div>

      <div className="p-5">
        <p className="text-[length:var(--fs-xs)] mw-faint tracking-wider mb-1.5">摘要</p>
        <p className="text-[length:var(--fs-md)] mw-muted leading-relaxed">{entry.abstract}</p>

        <p className="text-[length:var(--fs-xs)] mw-faint tracking-wider mt-4 mb-2">关键词</p>
        <div className="flex flex-wrap gap-1.5">
          {entry.keywords.map((k) => (
            <Pill key={k} tone="neutral">
              <Tag className="h-2.5 w-2.5" />
              {k}
            </Pill>
          ))}
        </div>

        <div className="mw-glass rounded-lg px-3 py-2 mt-4 inline-flex items-center gap-2">
          <span className="text-[length:var(--fs-xs)] mw-faint">DOI</span>
          <Num className="text-[length:var(--fs-sm)] text-[var(--c-accent)]">{entry.doi}</Num>
        </div>

        <div className="flex flex-wrap gap-2 mt-5">
          <Btn
            variant="primary"
            size="sm"
            onClick={() => window.open(`https://doi.org/${entry.doi}`, '_blank', 'noopener,noreferrer')}
          >
            <ExternalLink className="h-3.5 w-3.5" />
            查看原文
          </Btn>
        </div>
      </div>
    </Panel>
  )
}
