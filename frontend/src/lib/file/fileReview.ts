import { apiUrl } from '../system-adapter'
import type { JSONContent } from '@tiptap/core'
import type { FileChange } from '../../types'

/**
 * 程序化打开文件审阅面板（diff 审阅）。
 *
 * 审阅面板由 Chat 页面持有本地 state，正常只能由用户点击文件卡片打开。此函数
 * 通过派发 `prismclaw:open-review` CustomEvent 让任意模块（agent 完成回调、
 * 自动化任务、其它组件）都能拉起审阅面板 —— 只要拿到一个 FileChange 对象即可。
 * Chat 页面挂载时会监听该事件并 setReviewingChange。
 *
 * 典型用法：办公文件编辑完成后自动弹出审阅：
 *   openReviewPanel(fileChange)
 */
export function openReviewPanel(change: FileChange): void {
  if (typeof window === 'undefined' || !change || typeof change.path !== 'string') return
  window.dispatchEvent(new CustomEvent('prismclaw:open-review', { detail: change }))
}

/**
 * 结构化 drift 错误：后端在文件被「带外改动」（审阅前被外部编辑）时返回 409，
 * detail 形如 `{ code: "FILE_DRIFTED" | "OFFICE_DRIFTED", message, can_force }`。
 * 文本 drift 的 `can_force=true`（前端可展示「强制还原」按钮重试 force=true）；
 * 办公文件 drift 的 `can_force=false`（强制还原会破坏后续编辑，只能重新审阅）。
 */
export type DriftCode = 'FILE_DRIFTED' | 'OFFICE_DRIFTED'

export class DriftError extends Error {
  code: DriftCode
  canForce: boolean
  constructor(code: DriftCode, message: string, canForce: boolean) {
    super(message)
    this.name = 'DriftError'
    this.code = code
    this.canForce = canForce
  }
}

/** 把失败响应（4xx/5xx）统一解析为 `DriftError`（结构化 drift）或普通 `Error`。 */
async function _parseReviewError(res: Response): Promise<Error> {
  let detail: unknown = `HTTP ${res.status}`
  try {
    const data = await res.json()
    if (data?.detail != null) detail = data.detail
  } catch {
    /* ignore */
  }
  if (
    detail &&
    typeof detail === 'object' &&
    typeof (detail as { code?: unknown }).code === 'string' &&
    ((detail as { code?: string }).code === 'FILE_DRIFTED' ||
      (detail as { code?: string }).code === 'OFFICE_DRIFTED')
  ) {
    const d = detail as { code: DriftCode; message?: string; can_force?: boolean }
    return new DriftError(
      d.code,
      typeof d.message === 'string' && d.message ? d.message : `file drifted (${d.code})`,
      Boolean(d.can_force),
    )
  }
  return new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
}

export interface RevertResult {
  status: string
  path: string
  record_id: string
  before_sha: string
}

export interface WriteContentResult {
  status: string
  path: string
  record_id: string
  file_change: FileChange
  before_ref: string
  before_sha: string
}

/**
 * 调后端「拒绝修订 → 从 ReviewSet 基线还原文件」API。
 * 由 FileReviewModal 的 Reject All 触发，直接回写工作区文件，不经过 LLM。
 * 还原目标（agent 改动前的基线）由后端 ReviewSet 决定，无需前端传 before_ref。
 */
export async function revertFileChange(
  conversationId: string,
  path: string,
  token: string,
  force = false,
): Promise<RevertResult> {
  const res = await fetch(
    apiUrl(`/api/sessions/${encodeURIComponent(conversationId)}/file-revert`),
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ path, force }),
    },
  )
  if (!res.ok) {
    throw await _parseReviewError(res)
  }
  return (await res.json()) as RevertResult
}

/**
 * Reject All for `xlsx/pptx`: 把完整的改动明细回传后端，后端对「当前文件」做局部
 * 「拒绝手术」——还原每一处被拒绝的改动，已接受的改动与后续其它编辑原样保留。
 * 不再从冻结快照重放，根治「连续编辑互毁」问题。仅处理 `xlsx/pptx`。
 */
export async function applyOfficeSelection(
  conversationId: string,
  path: string,
  details: unknown[],
  token: string,
): Promise<{ status: string; file_change: unknown; office_result: unknown }> {
  const res = await fetch(
    apiUrl(`/api/sessions/${encodeURIComponent(conversationId)}/file-office-apply`),
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ path, details }),
    },
  )
  if (!res.ok) {
    throw await _parseReviewError(res)
  }
  return (await res.json()) as { status: string; file_change: unknown; office_result: unknown }
}

export interface OfficeReviewDoc {
  ok: boolean
  /** TipTap document JSON for `xlsx/pptx` review (changeId === details index). */
  doc: JSONContent
  paragraph_count: number
  table_count: number
  review?: boolean
  deleted_count?: number
  inserted_count?: number
}

export interface DocxNativeReviewArtifactResult {
  ok: boolean
  path: string
  review_mode: 'docx-native'
  review_docx_ref: string
  review_docx_sha: string
  pending_changes: number
  file_change: FileChange
}

export interface SaveDocxNativeReviewedResult {
  status: string
  path: string
  record_id: string
  before_sha: string
  after_sha: string
  file_change?: FileChange
}

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  const chunkSize = 0x8000
  let binary = ''
  for (let i = 0; i < bytes.length; i += chunkSize) {
    const chunk = bytes.subarray(i, i + chunkSize)
    binary += String.fromCharCode(...chunk)
  }
  return btoa(binary)
}

/**
 * 合成 `xlsx/pptx` 的「修订态」——后端据 before 备份 + 逐处改动明细生成带
 * insertion/deletion marks 的 TipTap JSON（changeId === details 下标）。
 * `.docx` 已切到原生 review artifact，不再走这里。
 */
export async function fetchOfficeReviewDoc(
  conversationId: string,
  path: string,
  token: string,
  workspaceId: string = 'default',
  beforeRef?: string | null,
  details?: unknown[] | null,
): Promise<OfficeReviewDoc> {
  const res = await fetch(
    apiUrl(`/api/sessions/${encodeURIComponent(conversationId)}/office-review-html`),
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        path,
        workspace_id: workspaceId,
        before_ref: beforeRef ?? null,
        details: details ?? [],
      }),
      cache: 'no-store',
    },
  )
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const data = await res.json()
      if (data?.detail) detail = String(data.detail)
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  return (await res.json()) as OfficeReviewDoc
}

export function docxNativeReviewArtifactUrl(
  conversationId: string,
  reviewDocxRef: string,
  fileName?: string,
): string {
  const query = new URLSearchParams({ ref: reviewDocxRef })
  if (fileName) query.set('name', fileName)
  return apiUrl(`/api/sessions/${encodeURIComponent(conversationId)}/file-docx-review-artifact/raw?${query.toString()}`)
}

export async function saveDocxNativeReviewed(
  conversationId: string,
  path: string,
  reviewDocxRef: string,
  beforeRef: string,
  beforeSha: string,
  buffer: ArrayBuffer,
  token: string,
): Promise<SaveDocxNativeReviewedResult> {
  const contentBase64 = arrayBufferToBase64(buffer)
  const res = await fetch(
    apiUrl(`/api/sessions/${encodeURIComponent(conversationId)}/file-docx-save-reviewed`),
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        path,
        review_docx_ref: reviewDocxRef,
        before_ref: beforeRef,
        before_sha: beforeSha,
        content_base64: contentBase64,
      }),
    },
  )
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const data = await res.json()
      if (data?.detail) detail = String(data.detail)
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  return (await res.json()) as SaveDocxNativeReviewedResult
}

/**
 * 前端手动编辑保存：将编辑后的文本内容直接写入工作区文件。
 * 后端自动备份旧版本 + 生成 diff + 记录 FileEditRecord（支持后续回滚）。
 * 仅支持 UTF-8 文本文件，不支持二进制/办公文件。
 */
export async function writeFileContent(
  conversationId: string,
  path: string,
  content: string,
  token: string,
  workspaceId: string = 'default',
): Promise<WriteContentResult> {
  const res = await fetch(
    apiUrl(
      `/api/sessions/${encodeURIComponent(conversationId)}/file-write-content?workspace_id=${encodeURIComponent(workspaceId)}`,
    ),
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ path, content }),
    },
  )
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const data = await res.json()
      if (data?.detail) detail = String(data.detail)
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  return (await res.json()) as WriteContentResult
}

/**
 * 持久化单个文件的审阅决策（confirmed/rejected）。
 *
 * 后端以 append-only `file_review` 事件落库（镜像 `feedback`），并在 `get_messages`
 * 投影时按 (message_id, path) 把 `reviewed` 字段贴回每个 FileChange。刷新后徽标
 * 从 `change.reviewed` 回放，不再重置为「待审」。此调用是纯记账，不改动工作区文件
 * ——文件的实际接受/拒绝手术仍由 revert/apply/save 系列端点完成。
 */
export async function markFileReviewed(
  conversationId: string,
  messageId: string,
  path: string,
  decision: 'confirmed' | 'rejected',
  token: string,
): Promise<void> {
  if (!conversationId || !messageId || !path) return
  const res = await fetch(
    apiUrl(`/api/sessions/${encodeURIComponent(conversationId)}/file-review-mark`),
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ message_id: messageId, path, decision }),
    },
  )
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const data = await res.json()
      if (data?.detail) detail = String(data.detail)
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
}
