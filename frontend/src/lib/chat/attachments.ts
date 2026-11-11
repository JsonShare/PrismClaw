/**
 * Frontend attachment helpers.
 *
 * Mirrors the backend attachment-routing vocabulary:
 * - split content blocks into text / image / file views
 * - map uploaded workspace files back into attachment blocks
 * - derive plain-text or display-friendly attachment summaries
 */
import type { AttachmentBlock, ContentBlock, FileBlock, ImageBlock } from '../../types'

export interface UploadedWorkspaceFile {
  original_name: string
  path: string
  size_bytes: number
  mime_type?: string | null
}

/**
 * 待选附件自己的上传状态。
 *
 * 附件一旦被选中就立即预上传（不再等发送），因此在「已选」到「已发送」之间
 * 存在明确的中间态：卡片需要能显示上传中/失败，并允许随时删除。
 */
export interface PendingUpload {
  status: 'uploading' | 'done' | 'error'
  /** 工作区相对路径，done 后才有 */
  path?: string
  /** 后端回传的原始文件名（含目录），用作 FileBlock.name */
  name?: string
  mimeType?: string | null
  sizeBytes?: number
  error?: string
}

export type PendingAttachment =
  | { kind: 'image'; id: string; file: File; block: ImageBlock; upload?: PendingUpload }
  | { kind: 'file'; id: string; file: File; upload?: PendingUpload }

/**
 * A file the upload pipeline rejected. Mirrors the backend ``SkippedItem``
 * model — the frontend holds ZERO filter constants; every skip reason is
 * communicated by this typed code, not by a client-side blocklist.
 */
export interface SkippedFile {
  name: string
  code: 'blocked_type' | 'noise' | 'disk_full' | 'invalid_path' | 'transfer_failed'
}

/**
 * Map a pipeline skip code to a user-facing message. Single source of truth
 * for skip messaging — the frontend never re-implements the blocklist / noise
 * filter, it only renders the reason the backend already decided on.
 */
export function skipCodeToMessage(code: SkippedFile['code'], name: string): string {
  switch (code) {
    case 'blocked_type':
      return `已忽略不支持的类型：${name}`
    case 'noise':
      return `已忽略系统文件：${name}`
    case 'disk_full':
      return `磁盘空间不足：${name}`
    case 'invalid_path':
      return `路径非法：${name}`
    case 'transfer_failed':
      return `传输失败：${name}`
    default:
      return `已跳过：${name}`
  }
}

export interface SplitAttachmentContent {
  text: string
  attachments: AttachmentBlock[]
  images: ImageBlock[]
  files: FileBlock[]
}

export interface UploadedAttachmentDisplay {
  name: string
  path: string
  ext: string
}

/**
 * Split mixed content into plain text plus typed attachment collections.
 */
export function splitAttachmentContent(content: string | ContentBlock[]): SplitAttachmentContent {
  if (typeof content === 'string') {
    return { text: content, attachments: [], images: [], files: [] }
  }
  let text = ''
  const attachments: AttachmentBlock[] = []
  const images: ImageBlock[] = []
  const files: FileBlock[] = []
  for (const block of content) {
    if (block.type === 'text') {
      if (isSystemNoteText(block.text)) continue
      text += (text ? '\n' : '') + block.text
      continue
    }
    if (block.type === 'image') {
      attachments.push(block)
      images.push(block)
      continue
    }
    if (block.type === 'file') {
      attachments.push(block)
      files.push(block)
    }
  }
  return { text, attachments, images, files }
}

const _IMAGE_NOTE_PATTERN = /^\s*\[(?:Large )?[Ii]mage[^\]]*\]\s*$/
const _NON_VISION_NOTE_PATTERN = /^\s*Note: The current model cannot[^\n]*$/
// 后端为「仅附件、无文字说明」的用户消息追加的中性占位文字（不带冒号则为当前格式，
// 带冒号后是旧版 legacy 列表格式，由 extractLegacyUploadedAttachmentText 单独解析）。
// 附件缩略图/文件卡本身已是「已上传」的可视指示，这段纯文字是冗余提示，渲染时隐藏。
const _UPLOAD_NOTE_PATTERN = /^\s*已上传附件[：: ]?\s*$/

export function isImageNoteText(text: string): boolean {
  if (_IMAGE_NOTE_PATTERN.test(text)) return true
  if (_NON_VISION_NOTE_PATTERN.test(text)) return true
  return false
}

export function isUploadNoteText(text: string): boolean {
  return _UPLOAD_NOTE_PATTERN.test(text)
}

/** 系统自动生成的提示性文字（非用户真实输入），渲染时从气泡正文过滤。 */
export function isSystemNoteText(text: string): boolean {
  return isImageNoteText(text) || isUploadNoteText(text)
}

export function contentToPlainText(content: string | ContentBlock[]): string {
  if (typeof content === 'string') return content
  return content
    .flatMap((block) => {
      if (block.type === 'text') return [block.text]
      if (block.type === 'file') {
        const name = (block.name || '').trim() || basename(block.path) || 'file'
        return [`[附件] ${name} (${block.path})`]
      }
      if (block.type === 'image') return ['[图片]']
      return []
    })
    .join('\n')
}

/**
 * 把已经预上传完成的待选附件转换成随消息发送的 attachment blocks。
 *
 * 未上传完、上传失败、被后端跳过的附件直接丢弃（发送前会统一 toast 说明），
 * 其余保持用户选择的顺序。
 */
export function buildOutgoingAttachments(
  pendingAttachments: PendingAttachment[],
): AttachmentBlock[] {
  const nextAttachments: AttachmentBlock[] = []
  for (const source of pendingAttachments) {
    const upload = source.upload
    if (!upload || upload.status !== 'done' || !upload.path) continue
    if (source.kind === 'file') {
      nextAttachments.push({
        type: 'file',
        path: upload.path,
        name: upload.name ?? source.file.name,
        mime_type: upload.mimeType ?? undefined,
        size_bytes: upload.sizeBytes ?? source.file.size,
      })
      continue
    }
    nextAttachments.push({
      ...source.block,
      source_path: upload.path,
    })
  }
  return nextAttachments
}

/**
 * Parse legacy "已上传附件" text blocks that may still exist in history.
 */
export function extractLegacyUploadedAttachmentText(rawText: string): {
  text: string
  attachments: UploadedAttachmentDisplay[]
} {
  const text = rawText || ''
  const marker = '已上传附件：'
  const idx = text.indexOf(marker)
  if (idx < 0) return { text, attachments: [] }
  if (idx > 0 && text[idx - 1] !== '\n') return { text, attachments: [] }

  const before = text.slice(0, idx).trimEnd()
  const after = text.slice(idx + marker.length)
  const lines = after
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
  const attachments: UploadedAttachmentDisplay[] = []

  for (const line of lines) {
    const match = /^-\s*(.+?)（(.+?)）$/.exec(line)
    if (!match) return { text, attachments: [] }
    const name = (match[1] || '').trim()
    const path = (match[2] || '').trim()
    const extRaw = (name.split('.').pop() || path.split('.').pop() || '').trim()
    const ext = extRaw ? extRaw.toUpperCase() : 'FILE'
    if (name && path) attachments.push({ name, path, ext })
  }

  if (attachments.length === 0) return { text, attachments: [] }
  return { text: before, attachments }
}

/**
 * Merge file blocks and legacy parsed text attachments into one display list.
 */
export function mergeDisplayedFileAttachments(
  files: FileBlock[],
  uploadedAttachments: UploadedAttachmentDisplay[],
): UploadedAttachmentDisplay[] {
  const normalized = files
    .filter((file) => !(typeof file.mime_type === 'string' && file.mime_type.startsWith('image/')))
    .map((file) => {
      const name = (file.name || basename(file.path) || 'file').trim()
      const extRaw = (name.split('.').pop() || file.path.split('.').pop() || '').trim()
      return { name, path: file.path, ext: extRaw ? extRaw.toUpperCase() : 'FILE' }
    })
  const merged = [...normalized]
  for (const item of uploadedAttachments) {
    if (!merged.some((entry) => entry.path === item.path && entry.name === item.name)) {
      merged.push(item)
    }
  }
  return merged
}

/** Normalize a path-like string to its basename for compact UI labels. */
export function basename(path: string): string {
  const clean = (path || '').trim()
  if (!clean) return ''
  const normalized = clean.replace(/\\+/g, '/')
  const parts = normalized.split('/').filter(Boolean)
  return parts.length > 0 ? parts[parts.length - 1] : normalized
}
