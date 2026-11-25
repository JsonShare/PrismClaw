/**
 * 参考来源（reference sources）归一化。
 *
 * 实时 event:reference 与历史回放 reference_sources 都是结构化对象，
 * 这里做一次形状校验，渲染层只处理 ReferenceSource。
 */

import type { ReferenceSource } from '../../types'

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

/** 副标题：机构 · 年份 */
export function referenceSubtitle(source: ReferenceSource): string {
  return [source.publisher, source.year].filter((v): v is string => !!v && v.trim().length > 0).join(' · ')
}

export function referenceDisplayTitle(source: ReferenceSource): string {
  const title = source.title?.trim()
  if (title && title !== source.url) return title
  if (source.url) {
    try {
      return new URL(source.url).hostname.replace(/^www\./, '')
    } catch {
      return source.url
    }
  }
  return title || '未命名来源'
}

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
