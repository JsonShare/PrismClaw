import Taro from '@tarojs/taro'

import type { AttachmentBlock } from '../types'
import { ApiError, apiUrl, getToken } from './request'

const IS_H5 = process.env.TARO_ENV === 'h5'
/** 与 PC 端一致：单条消息最多 10 个附件 */
export const MAX_ATTACHMENTS = 10
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
  message?: string
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

function nativeErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message
  if (error && typeof error === 'object') {
    const message = (error as { errMsg?: unknown }).errMsg
    if (typeof message === 'string') return message
  }
  return ''
}

/** 小程序取消选择是正常操作；其余错误转成可直接展示的 Error。 */
async function settlePick<T>(operation: () => Promise<T>, fallback: string): Promise<T | null> {
  try {
    return await operation()
  } catch (error) {
    const raw = nativeErrorMessage(error)
    if (/cancel/i.test(raw)) return null
    const detail = raw.replace(/^[\w.]+:fail:?\s*/, '')
    throw new Error(detail || fallback)
  }
}

/** H5 下用隐藏 input 取文件；取消选择时通过 cancel 与页面回到前台事件回收 */
function pickH5(
  configure: (input: HTMLInputElement) => void,
  limit: number,
): Promise<LocalFile[]> {
  return new Promise((resolve) => {
    const input = document.createElement('input')
    input.type = 'file'
    input.style.display = 'none'
    configure(input)
    let settled = false
    let returnTimer: ReturnType<typeof setTimeout> | null = null

    const onChange = () => {
      const picked = input.files ? Array.from(input.files) : []
      finish(
        picked.slice(0, limit).map((file) => ({
          id: newId(),
          name: file.name,
          size: file.size,
          mimeType: file.type || guessMime(file.name),
          source: file,
        })),
      )
    }
    const onCancel = () => finish([])
    const onReturn = () => {
      if (returnTimer) clearTimeout(returnTimer)
      // 部分浏览器 change 晚于 focus / visibilitychange，延迟后再判断选择结果
      returnTimer = setTimeout(() => {
        returnTimer = null
        if (settled) return
        if ((input.files?.length ?? 0) > 0) onChange()
        else finish([])
      }, 500)
    }
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') onReturn()
    }
    const finish = (files: LocalFile[]) => {
      if (settled) return
      settled = true
      if (returnTimer) clearTimeout(returnTimer)
      input.removeEventListener('change', onChange)
      input.removeEventListener('cancel', onCancel)
      window.removeEventListener('focus', onReturn)
      document.removeEventListener('visibilitychange', onVisibilityChange)
      input.remove()
      resolve(files)
    }

    // iOS / 内置浏览器可能不触发 focus，优先使用 input 的原生取消事件
    input.addEventListener('cancel', onCancel)
    // 用 addEventListener：属性式 onchange 在部分内置浏览器里不回调多选结果
    input.addEventListener('change', onChange)
    window.addEventListener('focus', onReturn)
    document.addEventListener('visibilitychange', onVisibilityChange)
    document.body.appendChild(input)
    input.click()
  })
}

/** 选择本地文件：H5 走隐藏 input，小程序走聊天文件选择器 */
export async function pickLocalFiles(limit = MAX_ATTACHMENTS): Promise<LocalFile[]> {
  if (IS_H5) {
    return pickH5((input) => {
      input.multiple = true
      input.setAttribute('multiple', 'multiple')
    }, limit)
  }

  const res = await settlePick(
    () => Taro.chooseMessageFile({ count: limit, type: 'all' }),
    '选择文件失败',
  )
  if (!res) return []
  return (res.tempFiles || []).map((file) => ({
    id: newId(),
    name: file.name || file.path.split('/').pop() || '未命名文件',
    size: file.size || 0,
    mimeType: file.type || guessMime(file.name || file.path),
    source: file.path,
  }))
}

/** 拍照或从相册选图：H5 用 capture 属性唤起相机，小程序走 chooseMedia */
export async function pickPhotos(
  from: 'camera' | 'album',
  limit = MAX_ATTACHMENTS,
): Promise<LocalFile[]> {
  if (IS_H5) {
    return pickH5((input) => {
      input.accept = 'image/*'
      if (from === 'album') {
        input.multiple = true
        input.setAttribute('multiple', 'multiple')
      }
      if (from === 'camera') input.setAttribute('capture', 'environment')
    }, limit)
  }

  const res = await settlePick(
    () => Taro.chooseMedia({
      count: from === 'camera' ? 1 : limit,
      mediaType: ['image'],
      sourceType: [from],
    }),
    '选择图片失败',
  )
  if (!res) return []
  return (res.tempFiles || []).map((file) => {
    const name = file.tempFilePath.split('/').pop() || '照片.jpg'
    return {
      id: newId(),
      name,
      size: file.size || 0,
      // 部分机型返回无扩展名的临时路径，但 chooseMedia 已保证这里是图片
      mimeType: IMAGE_MIME_BY_EXT[extOf(name)] || 'image/jpeg',
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
      let data: UploadResponse & { detail?: string }
      try {
        data = JSON.parse(res.data) as UploadResponse & { detail?: string }
      } catch {
        throw new Error('服务器返回了无法识别的上传结果')
      }
      if (res.statusCode < 200 || res.statusCode >= 300) {
        throw new ApiError(res.statusCode, data.detail || `上传失败（${res.statusCode}）`)
      }
      merged.files.push(...(data.files || []))
      merged.skipped.push(...(data.skipped || []))
      if (data.session_id) merged.session_id = data.session_id
    } catch (error) {
      merged.skipped.push({
        name: file.name,
        code: 'transfer_failed',
        message: nativeErrorMessage(error) || '网络连接失败',
      })
    }
  }
  return merged
}

function skippedMessage(item: SkippedItem): string {
  if (item.message) return `「${item.name}」${item.message}`
  switch (item.code) {
    case 'blocked_type':
      return `「${item.name}」类型不支持`
    case 'noise':
      return `「${item.name}」是系统文件，无法上传`
    case 'disk_full':
      return `「${item.name}」上传失败，磁盘空间不足`
    case 'invalid_path':
      return `「${item.name}」路径无效`
    default:
      return `「${item.name}」上传失败，请重试`
  }
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
  // 任意附件失败都保留当前草稿并明确提示，避免悄悄发送不完整的附件组合
  if (res.skipped.length) {
    throw new Error(res.skipped.map(skippedMessage).join('；'))
  }

  const uploadedByName = new Map<string, UploadItem[]>()
  for (const item of res.files) {
    const sameName = uploadedByName.get(item.original_name) || []
    sameName.push(item)
    uploadedByName.set(item.original_name, sameName)
  }
  const blocks: AttachmentBlock[] = []
  for (const file of files) {
    // 同名文件按上传顺序逐一消费，不能让多个附件都指向最后一个同名文件
    const uploaded = uploadedByName.get(file.name)?.shift()
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

  if (!blocks.length) throw new Error('文件上传失败，请重试')
  return blocks
}
