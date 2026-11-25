import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import iconKnowledge from '../../images/other_icon/knowledge.png'
import type { ReferenceSource } from '../../types'
import {
  referenceDisplayTitle,
  referenceIsRag,
  referenceKindLabel,
  referenceSubtitle,
} from '../../lib/chat/referenceSources'

/**
 * 参考来源面板（回答末尾）。
 *
 * 折叠态是「参考来源 N 篇」一行入口；展开后按序号逐条列出结构化来源：
 * 类型标签 / 标题 / 作者·期刊·日期 / 影响因子（RAG 来源）。
 * 网页来源点击后在新标签页打开；RAG 来源由右侧栏以 file-viewer 展示详情。
 */

/** 首屏展示条数，超出折叠为「展开更多 N 篇」 */
const INITIAL_VISIBLE = 5

interface Props {
  sources: ReferenceSource[]
  onOpenSource?: (source: ReferenceSource) => void
  className?: string
}

function hostOf(url?: string | null): string {
  if (!url) return ''
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return ''
  }
}

/** 列表第二行：网页显示域名·日期，RAG 显示作者·期刊·日期。 */
function sourceMeta(source: ReferenceSource): string {
  if (referenceIsRag(source)) return referenceSubtitle(source)
  return [hostOf(source.url), source.year].filter(Boolean).join(' · ')
}

function SourceRow({
  source,
  index,
  onOpen,
}: {
  source: ReferenceSource
  index: number
  onOpen?: () => void
}) {
  const meta = sourceMeta(source)
  const clickable = Boolean(onOpen)
  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        disabled={!clickable}
        title={source.url ?? referenceDisplayTitle(source)}
        className={`flex w-full min-w-0 items-start gap-3 px-4 py-3.5 text-left transition-colors
                    ${clickable ? 'hover:bg-[var(--c-surface-2)]/60 cursor-pointer' : 'cursor-default'}`}
      >
        <span className="w-4 shrink-0 pt-0.5 text-right text-[length:var(--fs-sm)] tabular-nums text-[var(--c-text-secondary)]/60">
          {index}.
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
            <span className="shrink-0 rounded-[var(--radius-control)] bg-[var(--c-accent)]/10 px-1.5 py-0.5 text-[length:var(--fs-xs)] font-medium text-[var(--c-accent)]">
              {referenceKindLabel(source.kind)}
            </span>
            <span className="min-w-0 text-[length:var(--fs-md)] font-semibold leading-snug text-[var(--c-text)]">
              {referenceDisplayTitle(source)}
            </span>
          </span>
          {meta ? (
            <span className="mt-1 block truncate text-[length:var(--fs-sm)] leading-snug text-[var(--c-text-secondary)]/80">
              {meta}
            </span>
          ) : null}
          {source.impactFactor ? (
            <span className="mt-1.5 inline-flex items-center gap-1 rounded-[var(--radius-control)] border border-[var(--c-border)] px-1.5 py-0.5 text-[length:var(--fs-xs)] leading-none">
              <span className="font-semibold tabular-nums text-[var(--c-text)]">{source.impactFactor}</span>
              <span className="text-[var(--c-text-secondary)]/80">影响因子</span>
            </span>
          ) : null}
        </span>
      </button>
    </li>
  )
}

export default function ReferenceSources({
  sources,
  onOpenSource,
  className,
}: Props) {
  const [expanded, setExpanded] = useState(false)
  const [showAll, setShowAll] = useState(false)
  if (!sources || sources.length === 0) return null

  const visible = showAll ? sources : sources.slice(0, INITIAL_VISIBLE)
  const remaining = sources.length - visible.length

  return (
    <div className={`mt-5 px-1 ${className ?? ''}`}>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 rounded-[var(--radius-control)] py-1.5 text-left
                   text-[length:var(--fs-md)] text-[var(--c-accent)] transition-colors hover:underline"
      >
        <img src={iconKnowledge} alt="" aria-hidden="true" className="h-[1em] w-auto shrink-0" />
        <span>参考来源 {sources.length} 篇</span>
        <ChevronDown
          size={14}
          className={`shrink-0 text-[var(--c-accent)]/70 transition-transform ${expanded ? '' : '-rotate-90'}`}
        />
      </button>

      {expanded ? (
        <div className="mt-2 overflow-hidden rounded-[var(--radius-card)] border border-[var(--c-border)] bg-[var(--c-surface)]">
          <ol>
            {visible.map((source, idx) => (
              <SourceRow
                key={`${source.url ?? source.doi ?? source.title}-${idx}`}
                source={source}
                index={idx + 1}
                onOpen={
                  onOpenSource && (source.url || referenceIsRag(source))
                    ? () => onOpenSource(source)
                    : undefined
                }
              />
            ))}
          </ol>
          {remaining > 0 ? (
            <button
              type="button"
              onClick={() => setShowAll(true)}
              className="flex w-full items-center justify-center gap-1 border-t border-[var(--c-border)] px-4 py-2.5
                         text-[length:var(--fs-sm)] text-[var(--c-text-secondary)] transition-colors
                         hover:bg-[var(--c-surface-2)]/60 hover:text-[var(--c-text)]"
            >
              展开更多 {remaining} 篇
              <ChevronDown size={14} />
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
