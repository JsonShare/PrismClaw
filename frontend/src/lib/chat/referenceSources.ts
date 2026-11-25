/**
 * 参考来源（reference sources）的归一化与展示辅助。
 *
 * 数据来自两处，形态一致：
 * - 实时：`event:reference` 帧的 `sources`
 * - 回放：`GET /messages|/sync` 的 `reference_sources`（CustomEntry 落库）
 *
 * 后端统一产出结构化对象；这里做一次形状校验，渲染层只处理 ReferenceSource。
 */

import type { ReferenceOrigin, ReferenceSection, ReferenceSource } from '../../types'

/** 来源是否来自知识库检索（RAG）。 */
export function referenceIsRag(source: ReferenceSource | null | undefined): boolean {
  return source?.origin === 'rag'
}

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

/** 把 RAG 来源转成 Markdown 文档，交给右侧栏既有的文件预览管线渲染。 */
export function buildReferenceMarkdown(source: ReferenceSource): string {
  const lines: string[] = [`# ${referenceDisplayTitle(source)}`]
  const meta: [string, string | null | undefined][] = [
    ['发布时间', source.year],
    ['期刊 / 来源', source.publisher],
    ['作者', source.authors],
    ['影响因子', source.impactFactor],
    ['DOI', source.doi],
  ]
  const metaLines = meta
    .filter(([, value]) => Boolean(value))
    .map(([label, value]) => `- **${label}**：${value}`)
  if (metaLines.length > 0) lines.push('', ...metaLines)

  for (const section of source.sections ?? []) {
    if (section.title) lines.push('', `## ${section.title}`)
    if (section.content) lines.push('', section.content)
  }
  if (source.snippet) {
    lines.push(
      '',
      '## 检索片段',
      '',
      '> 以下为检索返回的片段，可能经过截断，请结合上下文核对适用范围。',
      '',
      source.snippet,
    )
  }
  if (source.abstract) lines.push('', '## 摘要', '', source.abstract)
  if (lines.length === 1) lines.push('', '该来源由知识库检索命中，暂无可展示的片段。')
  return lines.join('\n')
}

/** 卡片副标题：机构 · 年份（缺失项自动省略）。 */
export function referenceSubtitle(source: ReferenceSource): string {
  return [source.authors, source.publisher, source.year]
    .filter((v): v is string => !!v && v.trim().length > 0)
    .join(' · ')
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

/** 校验并归一一条来源对象；缺少标题和合法 URL 时返回 null。 */
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
  // 未标注 origin 时按内容推断，兼容只下发 RAG 字段的后端实现。
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
