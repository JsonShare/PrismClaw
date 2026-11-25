import { useState } from 'react'
import { ChevronDown, ExternalLink, Link2 } from 'lucide-react'
import type { ReferenceSource } from '../../types'
import {
  referenceDisplayTitle,
  referenceKindLabel,
  referenceSubtitle,
} from '../../lib/chat/referenceSources'

/**
 * 参考来源面板（回答末尾）。
 *
 * 折叠态是「参考来源 N 篇」一行入口；展开后逐条给出结构化的来源卡片
 * （类型标签 / 标题 / 机构 · 年份 / 域名）。点击卡片不再新开标签页，
 * 而是交给右侧栏预览面板展示来源详情。
 */

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

function SourceRow({
  source,
  onOpen,
}: {
  source: ReferenceSource
  onOpen?: () => void
}) {
  const subtitle = referenceSubtitle(source)
  const host = hostOf(source.url)
  const clickable = Boolean(onOpen)
  return (
    <button
      type="button"
      onClick={onOpen}
      disabled={!clickable}
      title={source.url ?? referenceDisplayTitle(source)}
      className={`w-full min-w-0 rounded-[var(--radius-card)] border border-[var(--c-border)] bg-[var(--c-surface)]
                  px-3.5 py-3 flex items-start gap-3 text-left transition-colors
                  ${clickable ? 'hover:border-[var(--c-border-strong)] hover:bg-[var(--c-surface-2)]/60 cursor-pointer' : 'cursor-default'}`}
    >
      <span className="mt-0.5 flex h-5 shrink-0 items-center rounded-[var(--radius-control)] bg-[var(--c-surface-3)]/70 px-1.5 text-[length:var(--fs-xs)] font-medium text-[var(--c-text-secondary)]">
        {referenceKindLabel(source.kind)}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[length:var(--fs-sm)] font-medium leading-snug text-[var(--c-text)]">
          {referenceDisplayTitle(source)}
        </span>
        {(subtitle || host) && (
          <span className="mt-0.5 block truncate text-[length:var(--fs-xs)] leading-snug text-[var(--c-text-secondary)]/70">
            {[subtitle, host].filter(Boolean).join(' · ')}
          </span>
        )}
      </span>
      <span className="mt-0.5 shrink-0 text-[var(--c-text-secondary)]/50">
        {source.url ? <ExternalLink size={13} /> : <Link2 size={13} />}
      </span>
    </button>
  )
}

export default function ReferenceSources({ sources, onOpenSource, className }: Props) {
  const [expanded, setExpanded] = useState(false)
  if (!sources || sources.length === 0) return null

  return (
    <div className={`mt-5 px-3 ${className ?? ''}`}>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 rounded-[var(--radius-control)] py-1.5 text-left
                   text-[length:var(--fs-sm)] text-[var(--c-text-secondary)] transition-colors hover:text-[var(--c-text)]"
      >
        <Link2 size={13} className="shrink-0 text-[var(--c-text-secondary)]/70" />
        <span>参考来源 {sources.length} 篇</span>
        <ChevronDown
          size={14}
          className={`shrink-0 text-[var(--c-text-secondary)]/60 transition-transform ${expanded ? '' : '-rotate-90'}`}
        />
      </button>
      {expanded && (
        <div className="mt-2 space-y-2">
          {sources.map((source, idx) => (
            <SourceRow
              key={`${source.url ?? source.title}-${idx}`}
              source={source}
              onOpen={onOpenSource ? () => onOpenSource(source) : undefined}
            />
          ))}
        </div>
      )}
    </div>
  )
}
