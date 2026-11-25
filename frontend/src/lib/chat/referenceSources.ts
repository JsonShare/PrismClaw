/**
 * 参考来源（reference sources）的归一化与展示辅助。
 *
 * 数据来自两处，形态一致：
 * - 实时：`event:reference` 帧的 `sources`
 * - 回放：`GET /messages|/sync` 的 `reference_sources`（CustomEntry 落库）
 *
 * 后端统一产出结构化对象；这里做一次形状校验，渲染层只处理 ReferenceSource。
 */

import type { ReferenceSource } from '../../types'

/** kind → 中文标签。未知类型落到「网页」。 */
const KIND_LABELS: Record<string, string> = {
  guideline: '指南',
  paper: '文献',
  drug: '药品',
  database: '数据库',
  news: '资讯',
  book: '书籍',
  report: '报告',
  web: '网页',
}

export function referenceKindLabel(kind?: string | null): string {
  return KIND_LABELS[(kind ?? 'web').toLowerCase()] ?? KIND_LABELS.web
}

/** 卡片副标题：机构 · 年份（缺失项自动省略）。 */
export function referenceSubtitle(source: ReferenceSource): string {
  return [source.publisher, source.year].filter((v): v is string => !!v && v.trim().length > 0).join(' · ')
}

/** 无标题时的展示名：优先域名，其次路径。 */
export function referenceDisplayTitle(source: ReferenceSource): string {
  const title = source.title?.trim()
  if (title && title !== source.url) return title
  if (source.url) {
    try {
      const url = new URL(source.url)
      return url.hostname.replace(/^www\./, '')
    } catch {
      return source.url
    }
  }
  return title || '未命名来源'
}

/** 仅允许可在系统浏览器中打开的 HTTP(S) 链接。 */
export function normalizeReferenceUrl(raw: unknown): string | null {
  if (typeof raw !== 'string') return null
  const value = raw.trim()
  if (!value || !/^https?:\/\//i.test(value)) return null
  try {
    const url = new URL(value)
    return (url.protocol === 'http:' || url.protocol === 'https:') && url.hostname ? value : null
  } catch {
    return null
  }
}

/** 校验并归一一条来源对象；缺少标题和合法 URL 时返回 null。 */
export function normalizeReferenceSource(raw: unknown): ReferenceSource | null {
  if (!raw || typeof raw !== 'object') return null
  const item = raw as Record<string, unknown>
  const url = normalizeReferenceUrl(item.url)
  const title = typeof item.title === 'string' && item.title.trim() ? item.title.trim() : null
  if (!url && !title) return null
  return {
    title: title ?? (url as string),
    url,
    publisher: typeof item.publisher === 'string' && item.publisher.trim() ? item.publisher.trim() : null,
    year: typeof item.year === 'string' && item.year.trim() ? item.year.trim() : null,
    kind: typeof item.kind === 'string' && item.kind.trim() ? item.kind.trim() : 'web',
  }
}
