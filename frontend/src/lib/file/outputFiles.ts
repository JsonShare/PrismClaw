import { apiUrl } from '../system-adapter'

export type OutputFileType =
  | 'document'
  | 'spreadsheet'
  | 'slide'
  | 'image'
  | 'web'
  | 'code'
  | 'other'

export interface OutputFile {
  workspace_id: string
  workspace_name: string
  rel_path: string
  name: string
  ext: string
  type: OutputFileType
  size_bytes: number
  mtime_ms: number
  source_session_id: string | null
  source_session_title: string | null
}

export const OUTPUT_TYPE_LABELS: Record<OutputFileType, string> = {
  document: '文档',
  spreadsheet: '表格',
  slide: '幻灯片',
  image: '图片',
  web: '网页',
  code: '代码',
  other: '其他',
}

/** 类型筛选下拉选项（预设清单，对应 OUTPUT_TYPE_LABELS）。 */
export const OUTPUT_TYPE_FILTERS: { value: OutputFileType | 'all'; label: string }[] = [
  { value: 'all', label: '全部类型' },
  { value: 'document', label: '文档' },
  { value: 'spreadsheet', label: '表格' },
  { value: 'slide', label: '幻灯片' },
  { value: 'image', label: '图片' },
  { value: 'web', label: '网页' },
  { value: 'code', label: '代码' },
  { value: 'other', label: '其他' },
]

export type OutputSortKey = 'mtime' | 'size' | 'session' | 'workspace'

function fmtDecimal(n: number): string {
  const fixed = n.toFixed(1)
  return fixed.endsWith('.0') ? String(Math.round(n)) : fixed
}

export function formatBytes(bytes: number): string {
  if (!bytes || bytes < 0) return '0 B'
  const kb = 1024
  if (bytes >= kb * 1024) {
    return `${fmtDecimal(bytes / (kb * 1024))} MB`
  }
  if (bytes >= kb) {
    return `${fmtDecimal(bytes / kb)} KB`
  }
  return `${bytes} B`
}

export function formatTime(ms: number): string {
  if (!ms) return ''
  const d = new Date(ms)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/** 「定位会话」跳回会话所需的预览/下载 URL（同源）。 */
export function outputRawUrl(workspaceId: string, relPath: string): string {
  return `${apiUrl('/api/files/raw')}?workspace_id=${encodeURIComponent(workspaceId)}&path=${encodeURIComponent(relPath)}`
}

export function outputDownloadUrl(workspaceId: string, relPath: string): string {
  return `${apiUrl('/api/files/download')}?workspace_id=${encodeURIComponent(workspaceId)}&path=${encodeURIComponent(relPath)}`
}

export interface OutputsResult {
  items: OutputFile[]
  /** 后端扫描到上限后截断（超出部分不在 items 里） */
  truncated: boolean
}

/**
 * 拉取产出物列表，保留后端 ``truncated`` 标记。
 * ``workspaceId`` 为空时不再回退 ``all`` —— 聚合结果的 rel_path 相对各自工作区，
 * 无法用于发送/预览，直接返回空更安全。
 */
export async function fetchOutputsResult(
  workspaceId: string,
  token: string | null,
): Promise<OutputsResult> {
  if (!workspaceId) return { items: [], truncated: false }
  const url = `${apiUrl('/api/workspaces/outbox')}?workspace_id=${encodeURIComponent(workspaceId)}`
  const headers: Record<string, string> = {}
  if (token) {
    headers.Authorization = `Bearer ${token}`
  }
  const res = await fetch(url, { headers })
  if (!res.ok) {
    throw new Error(`加载产出物失败：${res.status}`)
  }
  const data = (await res.json()) as { items: OutputFile[]; truncated?: boolean }
  return { items: data.items || [], truncated: Boolean(data.truncated) }
}

export async function fetchOutputs(workspaceId: string, token: string | null): Promise<OutputFile[]> {
  return (await fetchOutputsResult(workspaceId, token)).items
}
