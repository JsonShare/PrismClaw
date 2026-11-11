import Taro from '@tarojs/taro'

import { apiUrl, getToken, http, withToken } from './request'

export type FileCategory = 'document' | 'spreadsheet' | 'slide' | 'image' | 'web' | 'code' | 'other'

export interface OutputFile {
  workspace_id: string
  workspace_name: string
  rel_path: string
  name: string
  ext: string
  type: FileCategory
  size_bytes: number
  mtime_ms: number
  source_session_id?: string | null
  source_session_title?: string | null
}

export interface OutputsResponse {
  items: OutputFile[]
}

export async function listOutputs(workspaceId: string): Promise<OutputsResponse> {
  const res = await http.get<OutputsResponse>('/api/workspaces/outputs', {
    workspace_id: workspaceId || 'all',
  })
  return { items: Array.isArray(res?.items) ? res.items : [] }
}

export async function deleteOutput(file: OutputFile): Promise<void> {
  await http.post(
    '/api/files/delete',
    { path: file.rel_path },
    { workspace_id: file.workspace_id },
  )
}

export function outputDownloadUrl(file: { workspace_id: string; rel_path: string }): string {
  const url = apiUrl(
    `/api/files/download?workspace_id=${encodeURIComponent(file.workspace_id)}&path=${encodeURIComponent(file.rel_path)}`,
  )
  return withToken(url)
}

/** 内嵌预览地址：后端以 inline 方式返回原始内容 */
export function outputRawUrl(file: { workspace_id: string; rel_path: string }): string {
  const url = apiUrl(
    `/api/files/raw?workspace_id=${encodeURIComponent(file.workspace_id)}&path=${encodeURIComponent(file.rel_path)}`,
  )
  return withToken(url)
}

const IMAGE_EXT = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'heic'])
const TEXT_EXT = new Set([
  'md', 'markdown', 'txt', 'log', 'json', 'csv', 'tsv', 'yaml', 'yml', 'ini', 'conf', 'toml',
  'py', 'ts', 'tsx', 'js', 'jsx', 'java', 'go', 'rs', 'c', 'cpp', 'h', 'hpp', 'sh', 'sql',
  'xml', 'css', 'scss',
])

export function fileExtOf(name: string): string {
  const idx = name.lastIndexOf('.')
  return idx >= 0 ? name.slice(idx + 1).toLowerCase() : ''
}

export function isImageFile(ext: string): boolean {
  return IMAGE_EXT.has(ext.toLowerCase())
}

export function isTextFile(ext: string): boolean {
  return TEXT_EXT.has(ext.toLowerCase())
}

/** 预览文本内容，超过 200KB 只取前 200KB */
export async function readTextPreview(
  file: { workspace_id: string; rel_path: string },
  maxBytes = 200 * 1024,
): Promise<string> {
  const res = await fetch(outputRawUrl(file), {
    headers: getToken() ? { Authorization: `Bearer ${getToken()}` } : undefined,
  })
  if (!res.ok) throw new Error(`读取失败（${res.status}）`)
  const text = await res.text()
  return text.length > maxBytes ? `${text.slice(0, maxBytes)}\n\n…（内容过长，仅显示前 200KB）` : text
}

/** Taro.openDocument 仅支持这几种显式声明的文档类型 */
const OPENABLE_EXT = new Set(['doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'pdf'])

/** 下载并打开文件：H5 直接新窗口，小程序走 downloadFile + openDocument。 */
export async function openOutput(file: {
  workspace_id: string
  rel_path: string
  ext: string
}): Promise<void> {
  const url = outputDownloadUrl(file)
  if (process.env.TARO_ENV === 'h5') {
    const win = window.open(url, '_blank')
    if (!win) throw new Error('浏览器拦截了新窗口，请允许弹窗后重试')
    return
  }
  const res = await Taro.downloadFile({ url })
  if (res.statusCode !== 200) throw new Error('下载失败')
  const ext = (file.ext || '').toLowerCase()
  await Taro.openDocument({
    filePath: res.tempFilePath,
    fileType: OPENABLE_EXT.has(ext) ? (ext as keyof Taro.openDocument.FileType) : undefined,
    showMenu: true,
  })
}
