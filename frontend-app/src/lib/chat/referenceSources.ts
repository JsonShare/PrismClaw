/**
 * 参考来源（reference sources）归一化。
 *
 * 实时 event:reference 与历史回放 reference_sources 都是结构化对象，
 * 这里做一次形状校验，渲染层只处理 ReferenceSource。
 */

import type { ReferenceOrigin, ReferenceSection, ReferenceSource } from '../../types'

/** 来源是否来自知识库检索（RAG）。 */
export function referenceIsRag(source: ReferenceSource | null | undefined): boolean {
  return source?.origin === 'rag'
}

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

/** 副标题：作者 · 机构 · 年份 */
export function referenceSubtitle(source: ReferenceSource): string {
  return [source.authors, source.publisher, source.year]
    .filter((v): v is string => !!v && v.trim().length > 0)
    .join(' · ')
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

function cleanStr(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function normalizeSections(raw: unknown): ReferenceSection[] | null {
  if (!Array.isArray(raw)) return null
  const out: ReferenceSection[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const entry = item as Record<string, unknown>
    const title = cleanStr(entry.title) ?? ''
    const content = cleanStr(entry.content) ?? ''
    if (!title && !content) continue
    out.push({ title, content })
    if (out.length >= 12) break
  }
  return out.length > 0 ? out : null
}

export function normalizeReferenceSource(raw: unknown): ReferenceSource | null {
  if (!raw || typeof raw !== 'object') return null
  const item = raw as Record<string, unknown>
  const url = normalizeReferenceUrl(item.url)
  const title = cleanStr(item.title)
  if (!url && !title) return null

  const snippet = cleanStr(item.snippet)
  const abstract = cleanStr(item.abstract)
  const sections = normalizeSections(item.sections)
  const authors = cleanStr(item.authors)
  const doi = cleanStr(item.doi)
  const rawOrigin = cleanStr(item.origin)?.toLowerCase()
  const origin: ReferenceOrigin =
    rawOrigin === 'rag' || rawOrigin === 'web'
      ? rawOrigin
      : !url && (snippet || abstract || sections || doi || authors)
        ? 'rag'
        : 'web'

  return {
    title: title ?? (url as string),
    url,
    publisher: cleanStr(item.publisher),
    year: cleanStr(item.year),
    kind: cleanStr(item.kind) ?? 'web',
    origin,
    authors,
    impactFactor: cleanStr(item.impact_factor),
    doi,
    snippet,
    abstract,
    sections,
  }
}
