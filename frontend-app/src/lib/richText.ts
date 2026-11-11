/** 与 PC 端 MarkdownRenderer 保持一致的富内容围栏语言 */
export const RICH_LANGUAGES = new Set([
  'json',
  'mermaid',
  'latex',
  'math',
  'datatable',
  'spreadsheet',
  'html-render',
  'html-preview',
  'image-preview',
  'pdf-preview',
  'markdown-preview',
])

const LANG_ALIASES: Record<string, string> = {
  ts: 'typescript',
  tsx: 'tsx',
  js: 'javascript',
  jsx: 'jsx',
  sh: 'bash',
  shell: 'bash',
  zsh: 'bash',
  py: 'python',
  yml: 'yaml',
  md: 'markdown',
  rb: 'ruby',
}

const LANG_LABELS: Record<string, string> = {
  javascript: 'JS',
  typescript: 'TS',
  tsx: 'TSX',
  jsx: 'JSX',
  python: 'PY',
  bash: 'SH',
  markdown: 'MD',
  yaml: 'YAML',
  json: 'JSON',
  sql: 'SQL',
  html: 'HTML',
  css: 'CSS',
  ruby: 'RB',
  text: 'TEXT',
  plaintext: 'TEXT',
  diff: 'DIFF',
}

/** highlight.js 认识的别名；返回空串表示按纯文本渲染 */
export function highlightLang(lang: string): string {
  const value = lang.trim().toLowerCase()
  if (!value) return ''
  return LANG_ALIASES[value] || value
}

export function langLabel(lang: string): string {
  const value = lang.trim().toLowerCase()
  if (!value) return 'TEXT'
  return LANG_LABELS[value] || value.toUpperCase()
}

export interface PreviewItem {
  src: string
  title?: string
  label?: string
}

export interface PreviewSpec {
  src?: string
  title?: string
  label?: string
  items?: PreviewItem[]
}

/** 解析 image/pdf/html/markdown 预览块的 JSON 契约（与 PC normalizePreviewSpec 一致） */
export function normalizePreviewSpec(source: string): PreviewSpec | null {
  let parsed: unknown
  try {
    parsed = JSON.parse(source)
  } catch {
    return null
  }
  if (!parsed || typeof parsed !== 'object') return null
  const record = parsed as Record<string, unknown>
  const rawItems = Array.isArray(record.items) ? record.items : []
  const items = rawItems
    .filter(
      (item): item is PreviewItem =>
        Boolean(item) && typeof item === 'object' && typeof (item as PreviewItem).src === 'string',
    )
    .map((item) => ({ ...item, src: item.src.trim() }))
    .filter((item) => item.src.length > 0)
  if (items.length) return { items, title: record.title as string | undefined }
  if (typeof record.src !== 'string' || !record.src.trim()) return null
  return {
    src: record.src.trim(),
    title: record.title as string | undefined,
    label: record.label as string | undefined,
  }
}

export interface DataTableSpec {
  title?: string
  columns: string[]
  rows: string[][]
}

type ColumnDef = { key: string; label?: string }

/** 解析 datatable / spreadsheet 的 JSON 契约（与 PC normalizeDataSpec 一致） */
export function normalizeDataSpec(source: string): DataTableSpec | null {
  let parsed: unknown
  try {
    parsed = JSON.parse(source)
  } catch {
    return null
  }
  if (!parsed || typeof parsed !== 'object') return null
  const spec = parsed as Record<string, unknown>
  const rawRows = Array.isArray(spec.rows) ? spec.rows : []
  const objectKeys = rawRows.flatMap((row) =>
    row && typeof row === 'object' && !Array.isArray(row) ? Object.keys(row) : [],
  )
  const rawColumns = Array.isArray(spec.columns) && spec.columns.length
    ? spec.columns
    : Array.from(new Set(objectKeys))
  if (!rawColumns.length) return null
  const defs: ColumnDef[] = rawColumns.map((column, index) => {
    if (column && typeof column === 'object' && !Array.isArray(column)) {
      const def = column as ColumnDef
      const key = String(def.key || def.label || `column_${index + 1}`)
      return { key, label: String(def.label || key) }
    }
    return { key: String(column) }
  })
  const rows = rawRows.map((row) => {
    if (Array.isArray(row)) return defs.map((_, index) => cellText(row[index]))
    const record = (row ?? {}) as Record<string, unknown>
    return defs.map((def) => cellText(record[def.key]))
  })
  return {
    title: typeof spec.title === 'string' ? spec.title : undefined,
    columns: defs.map((def) => def.label || def.key),
    rows,
  }
}

function cellText(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

/** 可直接交给浏览器/下载接口的地址 */
export function resolveAssetUrl(src: string): string {
  if (/^https?:\/\//i.test(src) || src.startsWith('//')) return src
  if (src.startsWith('/api/')) return src
  return ''
}
