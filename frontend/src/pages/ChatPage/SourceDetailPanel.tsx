import { ExternalLink, X } from 'lucide-react'
import type { ReferenceSource } from '../../types'
import {
  normalizeReferenceUrl,
  referenceDisplayTitle,
  referenceKindLabel,
  referenceSubtitle,
} from '../../lib/chat/referenceSources'
import { openExternal } from '../../lib/system-adapter'

/**
 * 右侧栏的「来源详情」视图。
 *
 * 没有做网页内嵌预览：医疗/文献类站点普遍用 X-Frame-Options 或 CSP
 * 的 frame-ancestors 拒绝被 iframe 嵌入，硬塞只会得到白屏。因此这里
 * 呈现可核查的元信息（标题 / 机构 / 年份 / 类型 / 完整链接），并提供
 * 「打开原文」跳转。
 */
export function SourceDetailPanel({
  source,
  onClose,
}: {
  source: ReferenceSource
  onClose: () => void
}) {
  const subtitle = referenceSubtitle(source)
  const url = normalizeReferenceUrl(source.url)
  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between px-4 py-2.5 shrink-0 border-b border-[var(--c-border)]">
        <span className="text-[length:var(--fs-sm)] font-medium text-[var(--c-text)]">参考来源</span>
        <button type="button" onClick={onClose} className="btn btn-icon btn-ghost" aria-label="关闭">
          <X size={16} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        <div className="flex items-center gap-2">
          <span className="flex h-5 items-center rounded-[var(--radius-control)] bg-[var(--c-surface-3)]/70 px-1.5 text-[length:var(--fs-xs)] font-medium text-[var(--c-text-secondary)]">
            {referenceKindLabel(source.kind)}
          </span>
          {subtitle ? (
            <span className="truncate text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]">{subtitle}</span>
          ) : null}
        </div>

        <div className="text-[length:var(--fs-md)] font-semibold leading-snug text-[var(--c-text)] break-words">
          {referenceDisplayTitle(source)}
        </div>

        {url ? (
          <div className="space-y-2">
            <div className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]">链接</div>
            <a
              href={url}
              onClick={(e) => {
                e.preventDefault()
                openExternal(url)
              }}
              className="block break-all text-[length:var(--fs-sm)] text-[var(--c-accent)] hover:underline"
            >
              {url}
            </a>
          </div>
        ) : (
          <div className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]">
            该来源没有可访问的链接，仅在正文中被引用。
          </div>
        )}

        {url ? (
          <button
            type="button"
            onClick={() => openExternal(url)}
            className="inline-flex items-center gap-1.5 rounded-[var(--radius-control)] border border-[var(--c-border)]
                       px-3 py-1.5 text-[length:var(--fs-sm)] text-[var(--c-text)] transition-colors
                       hover:border-[var(--c-border-strong)] hover:bg-[var(--c-surface-2)]/60"
          >
            <ExternalLink size={13} />
            打开原文
          </button>
        ) : null}
      </div>
    </div>
  )
}
