import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useChatStore, useSessionStore } from '../../stores'
import { apiUrl } from '../../lib/system-adapter'
import { normalizeWorkspacePath } from '../../lib/chat/paths'
import {
  revertFileChange,
  applyOfficeSelection,
  writeFileContent,
  fetchOfficeReviewDoc,
  markFileReviewed,
} from '../../lib/file/fileReview'
import type { FileChange } from '../../types'
import type { JSONContent } from '@tiptap/core'

/**
 * 咨询页文件预览 + 审阅控制器（唯一入口）。
 *
 * 此前这条链散落在 ChatPage 主体（~390 行）：8 个 useState、2 个 ref、4 个
 * useEffect、11 个 useCallback/useMemo，与页面编排逻辑交织。现整体收口：
 *   - 预览：openPreviewModal / openFileFromOutputList / closePreviewModal / downloadSingleFile
 *   - 审阅：openReviewInPanel / reviewHandlers（确认/拒绝/保存/回滚/docx 原生提交）
 *   - 状态：selectedFile / previewBlob / reviewingChange / reviewHtml / taskFilesOpen
 * ChatPage 只取返回值透传给 ChatSection / ChatRightPanel。
 */

const EMPTY_MESSAGES: import('../../types').Message[] = []

export interface ReviewHandlers {
  onConfirm: () => Promise<void>
  onReject: (force?: boolean) => Promise<void>
  onSaveContent: (content: string) => Promise<void>
  onRollback: () => Promise<void>
  onDocxNativeCommitted: () => Promise<void>
  onDocxNativeRejected: () => Promise<void>
  onReviewDone: () => void
}

export interface FilePreviewController {
  taskFilesOpen: boolean
  setTaskFilesOpen: React.Dispatch<React.SetStateAction<boolean>>
  selectedFile: string | null
  selectedFileError: string | null
  previewBlob: Blob | null
  reviewingChange: FileChange | null
  reviewHtml: JSONContent | null
  reviewHtmlError: string | null
  openPreviewModal: (rawPath: string) => void
  openReviewInPanel: (change: FileChange) => void
  openFileFromOutputList: (path: string) => void
  downloadSingleFile: (path: string) => Promise<void>
  closePreviewModal: () => void
  reviewHandlers: ReviewHandlers
}

interface FilePreviewDeps {
  token: string | null
  backendWorkspaceId: string
}

export function useFilePreviewController({
  token,
  backendWorkspaceId,
}: FilePreviewDeps): FilePreviewController {
  const activeConvId = useSessionStore((s) => s.activeConvId)
  const currentMessages = useChatStore((s) =>
    activeConvId ? s.convStates[activeConvId]?.messages ?? EMPTY_MESSAGES : EMPTY_MESSAGES,
  )

  const [taskFilesOpen, setTaskFilesOpen] = useState(true)
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [, setSelectedFileLoading] = useState(false)
  const [selectedFileError, setSelectedFileError] = useState<string | null>(null)
  const [previewBlobUrl, setPreviewBlobUrl] = useState<string | null>(null)
  const [previewBlob, setPreviewBlob] = useState<Blob | null>(null)
  const [reviewingChange, setReviewingChange] = useState<FileChange | null>(null)
  const [reviewHtml, setReviewHtml] = useState<JSONContent | null>(null)
  const [reviewHtmlError, setReviewHtmlError] = useState<string | null>(null)
  const reviewHtmlPathRef = useRef<string | null>(null)
  const previewBlobUrlRef = useRef<string | null>(null)
  // 快速切换文件时 cancel guard：旧 fetch 回来发现 path 已不是当前目标就丢弃。
  // 否则 fetch A → fetch B → fetch A 先完成 → setPreviewBlob(A) 覆盖 B →
  // 用户看到闪烁/停在错误文件上。
  const loadingPathRef = useRef<string | null>(null)

  useEffect(() => {
    previewBlobUrlRef.current = previewBlobUrl
  }, [previewBlobUrl])

  // 对 `xlsx/pptx` 办公文件变更，准备 TipTap 审阅文档。
  // `.docx` 已切到原生 review artifact，不再通过 office-review-html 合成投影。
  useEffect(() => {
    if (
      !reviewingChange ||
      !['xlsx', 'pptx'].includes(reviewingChange.kind) ||
      !selectedFile ||
      !activeConvId ||
      !token
    ) {
      if (reviewHtmlPathRef.current) {
        reviewHtmlPathRef.current = null
        setReviewHtml(null)
        setReviewHtmlError(null)
      }
      return
    }
    if (reviewHtmlPathRef.current === selectedFile && reviewHtml !== null) return
    let cancelled = false
    reviewHtmlPathRef.current = selectedFile
    setReviewHtmlError(null)
    const beforeRef = reviewingChange.before_ref ?? null
    const details = reviewingChange.officeDetail ?? []
    fetchOfficeReviewDoc(activeConvId, selectedFile, token, backendWorkspaceId, beforeRef, details)
      .then((r) => {
        if (!cancelled) setReviewHtml(r.doc)
      })
      .catch((e) => {
        if (!cancelled) setReviewHtmlError(e instanceof Error ? e.message : String(e))
      })
    return () => {
      cancelled = true
    }
  }, [reviewingChange, selectedFile, activeConvId, token, backendWorkspaceId, reviewHtml])

  useEffect(() => {
    return () => {
      if (previewBlobUrlRef.current && previewBlobUrlRef.current.startsWith('blob:')) {
        URL.revokeObjectURL(previewBlobUrlRef.current)
      }
    }
  }, [])

  const rawFileUrl = useCallback(
    (path: string) =>
      apiUrl(
        `/api/files/raw?workspace_id=${encodeURIComponent(backendWorkspaceId)}&path=${encodeURIComponent(
          path,
        )}${token ? `&_token=${encodeURIComponent(token)}` : ''}`,
      ),
    [backendWorkspaceId, token],
  )

  const revokePreviewBlobUrl = useCallback(() => {
    const url = previewBlobUrlRef.current
    if (url && url.startsWith('blob:')) URL.revokeObjectURL(url)
    previewBlobUrlRef.current = null
    setPreviewBlobUrl(null)
  }, [])

  const closePreviewModal = useCallback(() => {
    revokePreviewBlobUrl()
    setPreviewBlob(null)
    setSelectedFile(null)
    setSelectedFileError(null)
    setReviewingChange(null)
  }, [revokePreviewBlobUrl])

  const loadFileBlobUrl = useCallback(
    async (path: string) => {
      if (!token) return
      setSelectedFileLoading(true)
      setSelectedFileError(null)
      // Cancel guard：标记「当前正在加载 path」，旧 fetch 回来时若 loadingPathRef
      // 已变成另一个值 → 丢弃结果（防快速切换时 fetch A 先完成覆盖 fetch B）。
      loadingPathRef.current = path
      try {
        const res = await fetch(rawFileUrl(path), {
          headers: { Authorization: `Bearer ${token}` },
          cache: 'no-store',
        })
        if (!res.ok) {
          const text = await res.text().catch(() => '')
          throw new Error(text || `HTTP ${res.status}`)
        }
        const blob = await res.blob()
        if (loadingPathRef.current !== path) {
          // 期间用户已切到另一个文件 → 丢弃这次结果，最后一次 fetch 赢。
          return
        }
        let finalBlob = blob
        const typeHasCharset = /charset=/i.test(blob.type)
        const isTextLike =
          /^(text\/|application\/(json|xml|javascript|x-yaml|toml))/i.test(blob.type) ||
          /\.(md|markdown|txt|text|rst|tex|py|js|mjs|cjs|ts|tsx|jsx|css|scss|less|json|jsonc|yaml|yml|toml|ini|cfg|conf|sh|bash|zsh|sql|csv|log|env|gitignore|dockerfile|makefile|go|java|c|cpp|h|hpp|rs|rb|php|lua|scala|kt|swift|html|htm|xml|svg)$/i.test(path)
        if (isTextLike && !typeHasCharset) {
          try {
            const buf = await blob.arrayBuffer()
            finalBlob = new Blob([buf], { type: (blob.type || 'text/plain') + '; charset=utf-8' })
          } catch {
            finalBlob = blob
          }
        }
        revokePreviewBlobUrl()
        const url = URL.createObjectURL(finalBlob)
        previewBlobUrlRef.current = url
        setPreviewBlobUrl(url)
        setPreviewBlob(finalBlob)
      } catch (err) {
        if (loadingPathRef.current !== path) return // 切走了，错误也丢弃
        const msg = err instanceof Error ? err.message : '加载失败'
        setSelectedFileError(msg)
      } finally {
        if (loadingPathRef.current === path) {
          loadingPathRef.current = null
          setSelectedFileLoading(false)
        }
      }
    },
    [rawFileUrl, revokePreviewBlobUrl, token],
  )

  const downloadSingleFile = useCallback(
    async (path: string) => {
      if (!token) return
      try {
        const res = await fetch(rawFileUrl(path), {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!res.ok) {
          console.error('[downloadSingleFile] failed:', res.status)
          return
        }
        const blob = await res.blob()
        const blobUrl = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = blobUrl
        const fileName = path.split('/').pop() || 'download'
        a.download = fileName
        a.style.display = 'none'
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        setTimeout(() => URL.revokeObjectURL(blobUrl), 1000)
      } catch (err) {
        console.error('[downloadSingleFile] error:', err)
      }
    },
    [rawFileUrl, token],
  )

  const latestFileChangeByPath = useMemo(() => {
    const byPath = new Map<string, FileChange>()
    for (let i = currentMessages.length - 1; i >= 0; i -= 1) {
      const message = currentMessages[i]
      const changes = message.metadata?.fileChanges ?? []
      for (const change of changes) {
        const rawPath = typeof change.path === 'string' ? change.path.trim() : ''
        if (!rawPath) continue
        const normalized = normalizeWorkspacePath(rawPath) ?? rawPath.replace(/\\+/g, '/')
        if (!byPath.has(normalized)) {
          byPath.set(normalized, change)
        }
      }
    }
    return byPath
  }, [currentMessages])

  const extractHttpUrl = useCallback((raw: string): string | null => {
    const text = (raw || '').trim()
    if (!text) return null
    const match = text.match(/https?:\/\/[^\s)<>"']+/i)
    if (!match) return null
    let url = match[0]
    url = url.replace(/[)\].,;]+$/g, '')
    return url || null
  }, [])

  const openPreviewModal = useCallback(
    (rawPath: string) => {
      setTaskFilesOpen(true)
      setReviewingChange(null)
      const extractedUrl = extractHttpUrl(rawPath)
      if (extractedUrl) {
        setSelectedFile(extractedUrl)
        setSelectedFileError(null)
        // 先清旧 blob + 标记加载中 → 右侧面板切换文件时不会悬浮旧内容
        revokePreviewBlobUrl()
        setPreviewBlob(null)
        setSelectedFileLoading(true)
        return
      }

      const normalized = normalizeWorkspacePath(rawPath)
      if (!normalized) {
        const raw = (rawPath || '').trim()
        const normalizedRaw = raw.replace(/\\+/g, '/')
        const looksLikeAbs =
          normalizedRaw.startsWith('/') || normalizedRaw.startsWith('//') || /^[a-z]:\//i.test(normalizedRaw)
        setSelectedFile(rawPath)
        setSelectedFileError(
          looksLikeAbs
            ? '云端部署无法访问本地路径，请将文件生成到工作区（使用相对路径，如 outputs/report.docx）'
            : '无法预览该文件路径',
        )
        revokePreviewBlobUrl()
        setPreviewBlob(null)
        return
      }

      setSelectedFile(normalized)
      setSelectedFileError(null)
      revokePreviewBlobUrl()
      setPreviewBlob(null)
      void loadFileBlobUrl(normalized)
    },
    [extractHttpUrl, loadFileBlobUrl, revokePreviewBlobUrl],
  )

  const openReviewInPanel = useCallback(
    (change: FileChange) => {
      const normalized = normalizeWorkspacePath(change.path)
      if (normalized) {
        setSelectedFile(normalized)
        setSelectedFileError(null)
        revokePreviewBlobUrl()
        setPreviewBlob(null)
        void loadFileBlobUrl(normalized)
      } else {
        setSelectedFile(change.path)
        setSelectedFileError('无法预览该文件路径')
        revokePreviewBlobUrl()
        setPreviewBlob(null)
      }
      setReviewingChange(change)
      setTaskFilesOpen(true)
    },
    [revokePreviewBlobUrl, loadFileBlobUrl],
  )

  // 持久化单个文件的审阅决策，并即时回写内存 store（无需刷新即更新徽标）。
  // 审阅决策通过 append-only `file_review` 事件落库（见 markFileReviewed），刷新后由
  // 后端投影回 `change.reviewed`。这里在决策成功后把 `reviewed` 贴回对应 message 的
  // fileChanges，使徽标立即从「审阅 ▸」变为「已确认 / 已还原」。best-effort：缺少
  // message_id / 会话上下文时静默跳过，不阻断文件的实际接受/拒绝手术。
  const persistReviewDecision = useCallback(
    async (change: FileChange, decision: 'confirmed' | 'rejected') => {
      const messageId = typeof change.message_id === 'string' ? change.message_id : ''
      if (!activeConvId || !token || !messageId || !change.path) return
      try {
        await markFileReviewed(activeConvId, messageId, change.path, decision, token)
      } catch {
        // 记账失败不阻断主流程；下次刷新会回到「待审」，用户可重试。
        return
      }
      const store = useChatStore.getState()
      const msgs = store.convStates[activeConvId]?.messages ?? []
      const target = msgs.find((m) => m.id === messageId)
      const changes = target?.metadata?.fileChanges
      if (!changes) return
      const next = changes.map((c) =>
        c.path === change.path ? { ...c, reviewed: decision } : c,
      )
      store.mergeMessageMetadata(activeConvId, messageId, { fileChanges: next })
    },
    [activeConvId, token],
  )

  // 程序化打开审阅面板：任何模块（agent 回调、自动化、其它组件）都可通过派发
  // `prismclaw:open-review` CustomEvent（detail 为 FileChange）来拉起审阅面板，
  // 无需持有 Chat 的内部 state。见 lib/fileReview.ts::openReviewPanel。
  useEffect(() => {
    const handler = (e: Event) => {
      const change = (e as CustomEvent<FileChange>).detail
      if (change && typeof change.path === 'string') {
        openReviewInPanel(change)
      }
    }
    window.addEventListener('prismclaw:open-review', handler as EventListener)
    return () => window.removeEventListener('prismclaw:open-review', handler as EventListener)
  }, [openReviewInPanel])

  const openFileFromOutputList = useCallback(
    (path: string) => {
      const normalized = normalizeWorkspacePath(path)
      const key = normalized ?? (path || '').trim().replace(/\\+/g, '/')
      const matchedChange = latestFileChangeByPath.get(key)
      if (matchedChange) {
        openReviewInPanel(matchedChange)
        return
      }
      if (!normalized) {
        openPreviewModal(path)
        return
      }

      openPreviewModal(normalized)
    },
    [openReviewInPanel, latestFileChangeByPath, openPreviewModal],
  )

  const reviewHandlers = useMemo<ReviewHandlers>(() => ({
    onConfirm: async () => {
      await persistReviewDecision(reviewingChange!, 'confirmed')
    },
    onReject: async (force?: boolean) => {
      if (!activeConvId || !token || !reviewingChange) throw new Error('缺少会话上下文')
      const isOfficeChange = ['xlsx', 'pptx'].includes(reviewingChange.kind)
      const officeDetail = reviewingChange.officeDetail ?? []
      if (isOfficeChange && officeDetail.length > 0) {
        await applyOfficeSelection(
          activeConvId,
          reviewingChange.path,
          officeDetail as unknown[],
          token,
        )
        if (selectedFile) void loadFileBlobUrl(selectedFile)
        await persistReviewDecision(reviewingChange, 'rejected')
        return
      }
      await revertFileChange(activeConvId, reviewingChange.path, token, force)
      if (selectedFile) void loadFileBlobUrl(selectedFile)
      await persistReviewDecision(reviewingChange, 'rejected')
    },
    onSaveContent: async (content: string) => {
      // 无 change 的文件（产物列表/文件树打开）也允许编辑保存：路径回退到
      // selectedFile。原有 reviewingChange 优先，保证审阅上下文路径不变。
      const path = reviewingChange?.path ?? selectedFile
      if (!activeConvId || !token || !path) throw new Error('缺少会话上下文')
      await writeFileContent(activeConvId, path, content, token, backendWorkspaceId)
      if (selectedFile) void loadFileBlobUrl(selectedFile)
    },
    onRollback: async () => {
      if (!activeConvId || !token || !reviewingChange) throw new Error('缺少会话上下文')
      await revertFileChange(activeConvId, reviewingChange.path, token)
      if (selectedFile) void loadFileBlobUrl(selectedFile)
      await persistReviewDecision(reviewingChange, 'rejected')
    },
    onDocxNativeCommitted: async () => {
      if (selectedFile) void loadFileBlobUrl(selectedFile)
      if (reviewingChange) await persistReviewDecision(reviewingChange, 'confirmed')
    },
    onDocxNativeRejected: async () => {
      if (selectedFile) void loadFileBlobUrl(selectedFile)
      if (reviewingChange) await persistReviewDecision(reviewingChange, 'rejected')
    },
    onReviewDone: () => setReviewingChange(null),
  }), [activeConvId, backendWorkspaceId, loadFileBlobUrl, persistReviewDecision, reviewingChange, selectedFile, token])

  return {
    taskFilesOpen,
    setTaskFilesOpen,
    selectedFile,
    selectedFileError,
    previewBlob,
    reviewingChange,
    reviewHtml,
    reviewHtmlError,
    openPreviewModal,
    openReviewInPanel,
    openFileFromOutputList,
    downloadSingleFile,
    closePreviewModal,
    reviewHandlers,
  }
}
