import Taro from '@tarojs/taro'

import type { AttachmentBlock } from '../types'
import { ApiError, apiUrl, getToken } from './request'

const IS_H5 = process.env.TARO_ENV === 'h5'
/** 与 PC 端一致：单条消息最多 10 个附件 */
const MAX_FILES = 10
const IMAGE_MIME_BY_EXT: Record<string, string> = {
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  gif: 'image/gif',
  webp: 'image/webp',
  bmp: 'image/bmp',
  heic: 'image/heic',
}

/**
 * 本地待发送文件。H5 持有 File 对象；小程序只拿得到临时路径字符串，
 * 两者在发送时才真正上传（避免落地页选文件就先创建一个空会话）。
 */
export interface LocalFile {
  id: string
  name: string
  size: number
  mimeType: string
  source: File | string
}

interface UploadItem {
  original_name: string
  path: string
  size_bytes: number
  mime_type?: string | null
}

interface SkippedItem {
  name: string
  code: string
}

interface UploadResponse {
  files: UploadItem[]
  skipped: SkippedItem[]
  session_id?: string
}

function extOf(name: string): string {
  const idx = name.lastIndexOf('.')
  return idx >= 0 ? name.slice(idx + 1).toLowerCase() : ''
}

function guessMime(name: string): string {
  return IMAGE_MIME_BY_EXT[extOf(name)] || 'application/octet-stream'
}

function newId(): string {
  return `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

/** H5 下用隐藏 input 取文件；取消选择不会触发 change，靠 focus 兜底回收 */
function pickH5(configure: (input: HTMLInputElement) => void): Promise<LocalFile[]> {
  return new Promise((resolve) => {
    const input = document.createElement('input')
    input.type = 'file'
    input.style.display = 'none'
    configure(input)
    let settled = false
    const finish = (files: LocalFile[]) => {
      if (settled) return
      settled = true
      input.remove()
      resolve(files)
    }
    input.onchange = () => {
      const chosen = Array.from(input.files || []).slice(0, MAX_FILES)
      finish(
        chosen.map((file) => ({
          id: newId(),
          name: file.name,
          size: file.size,
          mimeType: file.type || guessMime(file.name),
          source: file,
        })),
      )
    }
    window.addEventListener('focus', () => setTimeout(() => finish([]), 500), { once: true })
    document.body.appendChild(input)
    input.click()
  })
}

/** 选择本地文件：H5 走隐藏 input，小程序走聊天文件选择器 */
export async function pickLocalFiles(): Promise<LocalFile[]> {
  if (IS_H5) {
    return pickH5((input) => {
      input.multiple = true
    })
  }

  const res = await Taro.chooseMessageFile({ count: MAX_FILES, type: 'all' })
  return (res.tempFiles || []).map((file) => ({
    id: newId(),
    name: file.name || file.path.split('/').pop() || '未命名文件',
    size: file.size || 0,
    mimeType: file.type || guessMime(file.name || file.path),
    source: file.path,
  }))
}

/** 拍照或从相册选图：H5 用 capture 属性唤起相机，小程序走 chooseMedia */
export async function pickPhotos(from: 'camera' | 'album'): Promise<LocalFile[]> {
  if (IS_H5) {
    return pickH5((input) => {
      input.accept = 'image/*'
      input.multiple = from === 'album'
      if (from === 'camera') input.setAttribute('capture', 'environment')
    })
  }

  const res = await Taro.chooseMedia({
    count: MAX_FILES,
    mediaType: ['image'],
    sourceType: [from],
  })
  return (res.tempFiles || []).map((file) => {
    const name = file.tempFilePath.split('/').pop() || '照片.jpg'
    return {
      id: newId(),
      name,
      size: file.size || 0,
      mimeType: guessMime(name),
      source: file.tempFilePath,
    }
  })
}

async function uploadH5(
  files: LocalFile[],
  query: string,
): Promise<UploadResponse> {
  const form = new FormData()
  for (const file of files) form.append('files', file.source as File, file.name)
  const res = await fetch(apiUrl(`/api/files/upload?${query}`), {
    method: 'POST',
    headers: { Authorization: `Bearer ${getToken()}` },
    body: form,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new ApiError(res.status, text || `上传失败（${res.status}）`)
  }
  return (await res.json()) as UploadResponse
}

async function uploadWeapp(
  files: LocalFile[],
  query: string,
): Promise<UploadResponse> {
  const merged: UploadResponse = { files: [], skipped: [] }
  for (const file of files) {
    try {
      const res = await Taro.uploadFile({
        url: apiUrl(`/api/files/upload?${query}`),
        filePath: file.source as string,
        name: 'files',
        header: { Authorization: `Bearer ${getToken()}` },
      })
      if (res.statusCode < 200 || res.statusCode >= 300) {
        throw new ApiError(res.statusCode, `上传失败（${res.statusCode}）`)
      }
      const data = JSON.parse(res.data) as UploadResponse
      merged.files.push(...(data.files || []))
      merged.skipped.push(...(data.skipped || []))
      if (data.session_id) merged.session_id = data.session_id
    } catch (error) {
      merged.skipped.push({ name: file.name, code: (error as Error).message || 'transfer_failed' })
    }
  }
  return merged
}

function readImageBase64(file: LocalFile): Promise<string> {
  if (IS_H5) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => {
        const result = typeof reader.result === 'string' ? reader.result : ''
        resolve(result.split(',')[1] || '')
      }
      reader.onerror = () => reject(new Error('读取图片失败'))
      reader.readAsDataURL(file.source as File)
    })
  }
  return new Promise((resolve, reject) => {
    Taro.getFileSystemManager().readFile({
      filePath: file.source as string,
      encoding: 'base64',
      success: (res) => resolve(String(res.data || '')),
      fail: () => reject(new Error('读取图片失败')),
    })
  })
}

/**
 * 上传并转换成后端 chat.send 的 attachments 结构。
 * 图片内联 base64（与 PC 端一致，保证视觉模型可用），其余走文件块。
 */
export async function uploadAttachments(
  files: LocalFile[],
  options: { workspaceId: string; sessionId: string },
): Promise<AttachmentBlock[]> {
  if (!files.length) return []
  const params = [
    `workspace_id=${encodeURIComponent(options.workspaceId || 'default')}`,
    'dir=',
    `session_id=${encodeURIComponent(options.sessionId)}`,
  ].join('&')
  const res = IS_H5 ? await uploadH5(files, params) : await uploadWeapp(files, params)

  const uploadedByName = new Map(res.files.map((item) => [item.original_name, item]))
  const blocks: AttachmentBlock[] = []
  for (const file of files) {
    const uploaded = uploadedByName.get(file.name)
    if (!uploaded) continue
    if (file.mimeType.startsWith('image/')) {
      const data = await readImageBase64(file)
      blocks.push({
        type: 'image',
        data,
        mime_type: file.mimeType,
        source_path: uploaded.path,
      })
    } else {
      blocks.push({
        type: 'file',
        path: uploaded.path,
        name: uploaded.original_name,
        mime_type: uploaded.mime_type || file.mimeType,
        size_bytes: uploaded.size_bytes,
      })
    }
  }

  if (!blocks.length) {
    const skipped = res.skipped[0]
    throw new Error(
      skipped ? `「${skipped.name}」无法上传` : '文件上传失败，请重试',
    )
  }
  return blocks
}
