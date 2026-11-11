import { Link2 } from 'lucide-react'
import {
  KNOWN_DOMAINS,
  SOURCE_ICON_CHAR,
  SOURCE_ICON_COLOR,
} from '../../theme/source-brand-colors'

/* ─── 检索结果卡片（设计图标准）────────────────────────────────────────
 * 浅灰圆角卡片 + 品牌色 favicon + 深色标题 + 右对齐灰色域名；
 * 未知来源回退为灰色链形圆标。固定高度约可见 4 行，内部滚动看全部。
 * 时间轴联网检索展开（StepDetailList.WebSearchExpandContent）复用本组件。
 * ──────────────────────────────────────────────────────────────────── */

export interface SearchResultItem {
  id: string
  /** 显示文本（公司名 / 带标签内容 / 普通文本） */
  title: string
  /** 来源域名 */
  domain: string
  /** 来源图标类型（向后兼容；缺省按 domain 品牌表 → 灰色链形回退） */
  sourceIcon?: 'link' | 'company' | 'zhipin' | 'tianyancha' | 'baidu' | 'tencent' | 'hexin' | 'generic'
}

export interface SearchResultGroup {
  id: string
  items: SearchResultItem[]
}

export interface SearchResultsProps {
  groups: SearchResultGroup[]
  className?: string
}

/* ─── 来源样式解析：显式 sourceIcon → 已知域名品牌 → 灰色链形回退 ─── */

function resolveBrand(
  domain: string,
  type?: SearchResultItem['sourceIcon'],
): { char: string; color: string } | null {
  if (type && type !== 'link' && type !== 'generic' && SOURCE_ICON_CHAR[type]) {
    return { char: SOURCE_ICON_CHAR[type], color: SOURCE_ICON_COLOR[type] }
  }
  if (KNOWN_DOMAINS[domain]) return KNOWN_DOMAINS[domain]
  return null
}

/* ─── Source Favicon（16px 品牌色 squircle；未知来源灰色链形圆标） ─── */

function SourceFavicon({
  domain,
  type,
}: {
  domain: string
  type?: SearchResultItem['sourceIcon']
}) {
  const brand = resolveBrand(domain, type)
  if (!brand) {
    return (
      <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-[var(--c-border)] bg-[var(--c-surface-3)]/50">
        <Link2 size={9} strokeWidth={2} className="text-[var(--c-text-secondary)]/70" />
      </span>
    )
  }
  const bg = `color-mix(in srgb, ${brand.color} 14%, transparent)`
  const ring = `color-mix(in srgb, ${brand.color} 30%, transparent)`
  return (
    <span
      className="flex h-4 w-4 shrink-0 items-center justify-center rounded-[var(--radius-control)] font-semibold"
      style={{
        backgroundColor: bg,
        color: brand.color,
        boxShadow: `inset 0 0 0 1px ${ring}`,
        fontSize: brand.char.length > 1 ? 8 : 10,
        lineHeight: 1,
      }}
    >
      {brand.char}
    </span>
  )
}

/* ─── Result Item Row ─── */

function ResultRow({ item }: { item: SearchResultItem }) {
  return (
    <div className="flex items-center gap-2.5 px-4 py-2 transition-colors hover:bg-[var(--c-surface-3)]/45">
      <SourceFavicon domain={item.domain} type={item.sourceIcon} />

      {/* 中间标题：block truncate 确保长文本被截断而非溢出 */}
      <div className="min-w-0 flex-1">
        <div className="block truncate text-[length:var(--fs-sm)] leading-snug text-[var(--c-text)]/90" title={item.title}>
          {item.title}
        </div>
      </div>

      {/* 右侧域名 */}
      <span className="shrink-0 whitespace-nowrap text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]/60 tabular-nums">
        {item.domain}
      </span>
    </div>
  )
}

/* ─── Result Group（浅灰圆角卡片，固定高度≈4行，内部滚动） ─── */

function ResultGroup({ group }: { group: SearchResultGroup }) {
  return (
    <div className="rounded-[var(--radius-md)] border border-[var(--c-border)] bg-[var(--c-exec-block)] py-1 max-h-[135px] overflow-y-auto overflow-x-hidden">
      {group.items.map((item) => (
        <ResultRow key={item.id} item={item} />
      ))}
    </div>
  )
}

/* ─── Main Component ─── */

export default function SearchResults({ groups, className }: SearchResultsProps) {
  if (groups.length === 0) return null

  return (
    <div className={`flex flex-col gap-3 ${className ?? ''}`}>
      {groups.map((group) => (
        <ResultGroup key={group.id} group={group} />
      ))}
    </div>
  )
}
