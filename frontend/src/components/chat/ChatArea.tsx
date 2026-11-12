import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { ArrowDown, Loader2 } from 'lucide-react'
import type { AttachmentBlock, FileChange, Message, Workspace } from '../../types'
import MessageBubble, { extractQuestionPlainText } from './MessageBubble'
import EmptyStateSuggestions from './EmptyStateSuggestions'
import ChatInputBox, { type ChatInputBoxProps } from './ChatInputBox'
import type { RichInputHandle } from './RichInput'
import { collectChips, hasPlainText, markersToReadable } from '../../lib/chat/inputMarkers'
import ClarifyCard from './ClarifyCard'
import QueueBar from './QueueBar'
import { MessageSkeleton } from '../ui/Skeleton'
import ServerWorkspaceManager from '../file/ServerWorkspaceManager'
import ModeToggle from './ModeToggle'
import { UploadQueueModal, UploadTicker, type UploadTask } from '../file/UploadQueue'
import { StreamingAsrSession } from '../../lib/chat/streamingAsr'
import {
  buildOutgoingAttachments,
  contentToPlainText,
  type PendingAttachment,
  type PendingUpload,
  skipCodeToMessage,
  type SkippedFile,
  type UploadedWorkspaceFile,
} from '../../lib/chat/attachments'
import {
  fileToImageBlock,
  isImageError,
  isSupportedImageType,
  MAX_IMAGES_PER_MESSAGE,
} from '../../lib/file/image'
import { apiUrl } from '../../lib/system-adapter'
import { useToast } from '../../lib/toast'
import { useChatStore, usePermissionStore, useSessionStore } from '../../stores'
import type { OutboxItem } from '../../stores/chat'
import { useChatScrollController } from '../../hooks/chat/useChatScroll'
import { useClarifyFlow } from '../../hooks/chat/useClarifyFlow'
import { usePhaseClock } from '../../hooks/chat/usePhaseClock'
import type { ConversationController } from '../../hooks/chat/useConversationController'
import { useUiStore } from '../../stores/ui'
import landingLogo from '../../images/logo/logo_512x512.png'

interface Props {
  token: string
  workspaceId: string
  conversationId: string
  // 会话级状态（messages/流式/队列/历史加载态）一律由本组件直接订阅 store，
  // 会话级动作（发送/中止/重试/澄清/确认/队列）统一走 controller——
  // 不再经 ChatPage → ChatSection 逐层透传（原 16 个会话级 props 已移除）。
  controller: ConversationController
  prefillInput?: { text: string; nonce: number } | null
  onOpenFilePreview?: (path: string) => void
  workspaceUploadDir?: string
  layout?: 'default' | 'landing'
  workspaces?: Workspace[]
  onSelectWorkspace?: (id: string) => void
  onCreateServerWorkspace?: (name: string) => Promise<void>
  onUploadServerFiles?: (files: File[]) => Promise<void>
  onRemoveServerWorkspace?: (id: string) => Promise<void>
  isWorkspacesLoading?: boolean
  onOpenReviewInPanel?: (change: FileChange) => void
}

const STREAMING_ID = '__streaming__'
// zustand selector 的 fallback 必须是稳定引用，否则每次 store 变更都触发重渲染。
const EMPTY_MESSAGES: Message[] = []
const EMPTY_OUTBOX: OutboxItem[] = []

const MAX_FILES_PER_MESSAGE = 10
const WELCOME_CHOICE_CARD =
  '我们接下来做点什么？'

export default function ChatArea({
  token,
  workspaceId,
  conversationId,
  controller,
  prefillInput,
  onOpenFilePreview,
  workspaceUploadDir,
  layout,
  workspaces,
  onSelectWorkspace,
  onCreateServerWorkspace,
  onUploadServerFiles,
  onRemoveServerWorkspace,
  isWorkspacesLoading = false,
  onOpenReviewInPanel,
}: Props) {
  const mode = usePermissionStore((s) => s.mode)
  const isWorkMode = mode === 'work'
  const { toast } = useToast()
  const [input, setInput] = useState('')
  const attachmentId = useCallback((file: File): string => {
    return `${file?.name || ''}:${file?.size || 0}:${file?.lastModified || 0}`
  }, [])

  const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([])
  const [isUploading, setIsUploading] = useState(false)
  const [isOptimizing, setIsOptimizing] = useState(false)
  const [isRecording, setIsRecording] = useState(false)
  const [isTranscribing, setIsTranscribing] = useState(false)
  const [isDragging, setIsDragging] = useState(false)
  const [uploadTasks, setUploadTasks] = useState<UploadTask[]>([])
  const [uploadQueueOpen, setUploadQueueOpen] = useState(false)
  const uploadQueueClearTimerRef = useRef<number | null>(null)
  const uploadSessionIdRef = useRef<string | null>(null)

  useEffect(() => {
    return () => {
      if (uploadQueueClearTimerRef.current) {
        window.clearTimeout(uploadQueueClearTimerRef.current)
        uploadQueueClearTimerRef.current = null
      }
    }
  }, [])
  const submitInFlightRef = useRef(false)
  const submitCooldownUntilRef = useRef(0)
  const editorRef = useRef<RichInputHandle>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const asrSessionRef = useRef<StreamingAsrSession | null>(null)
  const dragCounterRef = useRef(0)

  // ── 会话级状态：直接订阅 store（单真相源），不再经 props 透传 ──
  const messages = useChatStore((s) => s.convStates[conversationId]?.messages ?? EMPTY_MESSAGES)
  const streamingText = useChatStore((s) => s.convStates[conversationId]?.streamingText ?? '')
  const isStreaming = useChatStore((s) => s.convStates[conversationId]?.isStreaming ?? false)
  const isQueued = useChatStore((s) => s.convStates[conversationId]?.isQueued ?? false)
  const queuePosition = useChatStore((s) => s.convStates[conversationId]?.queuePosition ?? 0)
  const isLoadingHistory = useChatStore(
    (s) => s.convStates[conversationId]?.historyStatus === 'loading',
  )
  const outbox = useChatStore((s) => s.convStates[conversationId]?.outbox ?? EMPTY_OUTBOX)
  const queuedUserMessages = useMemo(
    () =>
      outbox.map((m) => ({
        id: m.id,
        text:
          markersToReadable(m.text).trim() ||
          (m.attachments.length > 0 ? `附件任务（${m.attachments.length} 个）` : '未命名任务'),
      })),
    [outbox],
  )

  // 从 activeTaskRuntime 读取统一运行态，避免前端维护第二份影子状态。
  const liveTaskRuntime = useChatStore(
    (s) => s.convStates[conversationId]?.activeTaskRuntime ?? null,
  )
  // 风险确认操作条：由统一运行态 + socket 就绪态直接派生（此前在 ChatPage 派生后透传）。
  const confirmationVisible =
    liveTaskRuntime?.task?.state === 'awaiting_confirmation' ||
    liveTaskRuntime?.task?.state === 'awaiting_risk_confirmation'
  const confirmationDisabled = !conversationId || controller.wsState !== 'ready' || isStreaming
  const liveToolCalls = liveTaskRuntime?.tool_calls ?? []
  const liveSubagentItems = useMemo(
    () => liveTaskRuntime?.subagent_trace ?? [],
    [liveTaskRuntime],
  )
  // 执行过程中实时同步的 timeline 节点，用于合成 streaming 消息（ExecutionTimeline 消费）。
  const liveTimeline = useChatStore(
    (s) => s.convStates[conversationId]?.timeline ?? [],
  )
  // 执行心跳阶段（set_phase patch 驱动；仅传给 StepTimeline 用于步骤状态显示）
  const livePhase = useChatStore(
    (s) => s.convStates[conversationId]?.phase ?? null,
  )
  // runGate: HITL 单一真相源。取代 phase.state 的 awaiting_clarification 语义。
  const liveRunGate = useChatStore(
    (s) => s.convStates[conversationId]?.runGate ?? null,
  )
  // phaseAt = 该相位 patch 到达的本地时刻（store 记），仅供心跳基线校正；
  // 计时纪元是本轮开始时刻 runStartedAt（心跳帧到达前界面就该计数）。
  const livePhaseAt = useChatStore(
    (s) => s.convStates[conversationId]?.phaseAt ?? null,
  )
  const liveRunStartedAt = useChatStore(
    (s) => s.convStates[conversationId]?.runStartedAt ?? null,
  )
  const liveIsStreaming = useChatStore(
    (s) => s.convStates[conversationId]?.isStreaming ?? false,
  )
  const liveElapsedS = usePhaseClock(livePhase, livePhaseAt, liveRunStartedAt, liveIsStreaming)
  // AskUserQuestion 澄清流：解析（clarifyRequest/messages 双路径）、选择态、
  // 提交/跳过全部收口在 useClarifyFlow；本组件只消费 isClarifying（输入屏蔽）、
  // resetSelections（发送后重置）与 flow 对象（传给 ClarifyCard）。
  const clarifyFlow = useClarifyFlow(conversationId, controller)
  const markMsgClarified = useChatStore((s) => s.markMsgClarified)

  const setMessageFeedback = useChatStore((s) => s.setMessageFeedback)

  // 对话宽度：新任务页面中间栏内容区的默认宽度（px），与右侧预览栏拖拽宽度相互独立
  const chatWidth = useUiStore((s) => s.chatWidth)

  const contentToText = useCallback((content: Message['content']): string => markersToReadable(contentToPlainText(content)), [])

  const normalizeForClipboard = useCallback((text: string): string => {
    const s = (text || '').toString().replace(/\r\n/g, '\n').replace(/\r/g, '\n')
    const lines = s.split('\n').map((l) => l.replace(/[ \t]+$/g, ''))
    const out: string[] = []
    let blankRun = 0
    for (const line of lines) {
      if (!line.trim()) {
        blankRun += 1
        if (blankRun <= 1) out.push('')
        continue
      }
      blankRun = 0
      out.push(line)
    }
    while (out.length > 0 && !out[0].trim()) out.shift()
    while (out.length > 0 && !out[out.length - 1].trim()) out.pop()
    return out.join('\n')
  }, [])

  const copyToClipboard = useCallback(async (text: string): Promise<void> => {
    const raw = normalizeForClipboard(text)
    if (!raw.trim()) return
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      try {
        await navigator.clipboard.writeText(raw)
        return
      } catch {}
    }
    const el = document.createElement('textarea')
    el.value = raw
    el.setAttribute('readonly', 'true')
    el.style.position = 'fixed'
    el.style.top = '0'
    el.style.left = '0'
    el.style.width = '1px'
    el.style.height = '1px'
    el.style.opacity = '0'
    document.body.appendChild(el)
    el.focus()
    el.select()
    document.execCommand('copy')
    document.body.removeChild(el)
  }, [normalizeForClipboard])

  const handleCopy = useCallback(
    async (m: Message): Promise<void> => {
      await copyToClipboard(contentToText(m.content))
    },
    [contentToText, copyToClipboard],
  )

  const handleFeedback = useCallback(
    async (messageId: string, vote: 'up' | 'down' | null): Promise<void> => {
      if (!conversationId) return
      const prev =
        useChatStore
          .getState()
          .convStates[conversationId]?.messages?.find((m) => m.id === messageId)?.metadata?.feedback ?? null
      setMessageFeedback(conversationId, messageId, vote)
      try {
        const res = await fetch(apiUrl(`/api/sessions/${encodeURIComponent(conversationId)}/feedback`), {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            message_id: messageId,
            vote: vote ?? 'none',
          }),
        })
        if (!res.ok) throw new Error('feedback failed')
      } catch {
        setMessageFeedback(conversationId, messageId, prev)
      }
    },
    [conversationId, setMessageFeedback, token],
  )

  const handleShare = useCallback(
    async (m: Message): Promise<void> => {
      const text = contentToText(m.content)
      if (!text.trim()) return
      const nav = navigator as any
      if (typeof nav.share === 'function') {
        await nav.share({ text })
        return
      }
      await copyToClipboard(text)
    },
    [contentToText, copyToClipboard],
  )

  const activeConvId = useSessionStore((s) => s.activeConvId)

  const uploadFiles = useCallback(
    async (
      files: File[],
      signal?: AbortSignal,
    ): Promise<{ uploaded: UploadedWorkspaceFile[]; skipped: SkippedFile[] }> => {
      const form = new FormData()
      for (const f of files) {
        // Preserve the original directory structure (folder uploads) by passing
        // the relative path as the multipart filename.
        const rel = (f as any).relativePath || (f as any).webkitRelativePath || f.name
        form.append('files', f, rel)
      }
      const params = new URLSearchParams()
      params.set('workspace_id', workspaceId || 'default')
      const dir = workspaceUploadDir ?? ''
      if (dir) params.set('dir', dir)
      const uploadSessionId = activeConvId || uploadSessionIdRef.current
      if (uploadSessionId) params.set('session_id', uploadSessionId)
      const res = await fetch(
        apiUrl(`/api/files/upload?${params.toString()}`),
        {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: form,
        signal,
        },
      )
      if (!res.ok) {
        const text = await res.text().catch(() => '')
        throw new Error(text || `上传失败（HTTP ${res.status}）`)
      }
      const data = (await res.json()) as {
        files: UploadedWorkspaceFile[]
        skipped: SkippedFile[]
        session_id?: string
      }
      // 后端自动创建 session_id 时（无 activeConvId 的新对话场景）：仅记住它，
      // 让本轮后续附件复用同一上传目录；不触碰 activeConvId。
      if (!activeConvId && !uploadSessionIdRef.current && data.session_id) {
        uploadSessionIdRef.current = data.session_id
      }
      return { uploaded: data.files ?? [], skipped: data.skipped ?? [] }
    },
    [activeConvId, token, workspaceId, workspaceUploadDir],
  )

  // ── 附件预上传 ──
  // 选中即上传：卡片立刻显示真实上传状态，发送时只等待未完成的请求；
  // 删除附件会 abort 对应请求，回调里也会忽略已被移除的附件。
  const prefetchAbortRef = useRef<Map<string, AbortController>>(new Map())
  const prefetchPromiseRef = useRef<Map<string, Promise<PendingUpload>>>(new Map())

  const startPrefetch = useCallback((att: PendingAttachment): Promise<PendingUpload> => {
    const running = prefetchPromiseRef.current.get(att.id)
    if (running) return running

    const controller = new AbortController()
    prefetchAbortRef.current.set(att.id, controller)
    setPendingAttachments((prev) =>
      prev.map((a) => (a.id === att.id ? { ...a, upload: { status: 'uploading' } } : a)),
    )

    const promise = (async (): Promise<PendingUpload> => {
      try {
        const { uploaded, skipped } = await uploadFiles([att.file], controller.signal)
        if (skipped.length > 0) {
          const msg = skipCodeToMessage(skipped[0].code, skipped[0].name)
          toast(msg, 'error')
          return { status: 'error', error: msg }
        }
        const file = uploaded[0]
        if (!file) return { status: 'error', error: '上传失败' }
        return {
          status: 'done',
          path: file.path,
          name: file.original_name,
          mimeType: file.mime_type,
          sizeBytes: file.size_bytes,
        }
      } catch (err) {
        if (controller.signal.aborted) return { status: 'error', error: '已取消' }
        const msg = err instanceof Error ? err.message : '上传失败，请重试。'
        toast(msg, 'error')
        return { status: 'error', error: msg }
      }
    })().then((result) => {
      prefetchAbortRef.current.delete(att.id)
      setPendingAttachments((prev) =>
        prev.some((a) => a.id === att.id)
          ? prev.map((a) => (a.id === att.id ? { ...a, upload: result } : a))
          : prev,
      )
      return result
    })
    prefetchPromiseRef.current.set(att.id, promise)
    return promise
  }, [toast, uploadFiles])

  const addUploadTasks = useCallback((files: File[]) => {
    const now = Date.now()
    const newTasks: UploadTask[] = files.map((f, i) => {
      const rel = (f as any).relativePath || (f as any).webkitRelativePath || f.name
      return {
        id: `${now}-${i}-${Math.random().toString(36).slice(2, 8)}`,
        name: f.name,
        relativePath: rel,
        size: f.size,
        status: 'pending',
      }
    })
    setUploadTasks((prev) => [...prev, ...newTasks])
    return newTasks.map((t) => t.id)
  }, [])

  const updateUploadTaskStatus = useCallback(
    (ids: string[], status: UploadTask['status'], error?: string) => {
      setUploadTasks((prev) =>
        prev.map((t) => (ids.includes(t.id) ? { ...t, status, error: error ?? t.error } : t)),
      )
    },
    [],
  )

  const handleUploadServerFiles = useCallback(
    async (files: File[]) => {
      if (files.length === 0) return
      // 新一批开始：取消上一批的清理计时器
      if (uploadQueueClearTimerRef.current) {
        window.clearTimeout(uploadQueueClearTimerRef.current)
        uploadQueueClearTimerRef.current = null
      }
      // 一次性加入全部 pending，让进度条从 0 开始显示完整批次
      const ids = addUploadTasks(files)
      // 串行逐个上传：进度条按完成文件数推进，任意时刻只有一个「上传中」
      for (let i = 0; i < files.length; i++) {
        const id = ids[i]
        updateUploadTaskStatus([id], 'uploading')
        try {
          await onUploadServerFiles?.([files[i]])
          updateUploadTaskStatus([id], 'done')
        } catch (err) {
          const msg = err instanceof Error ? err.message : '上传失败'
          updateUploadTaskStatus([id], 'error', msg)
        }
      }
      // 全部结束（含失败）：停留 3 秒后清空整个队列
      uploadQueueClearTimerRef.current = window.setTimeout(() => {
        setUploadTasks([])
        uploadQueueClearTimerRef.current = null
      }, 3000)
    },
    [addUploadTasks, onUploadServerFiles, updateUploadTaskStatus],
  )

  const addFiles = useCallback(
    async (files: File[]) => {
      if (files.length === 0) return false

      const next = [...pendingAttachments]
      const existing = new Set(next.map((a) => a.id))
      const addedAttachments: PendingAttachment[] = []
      let imageCount = next.filter((a) => a.kind === 'image').length
      let added = 0

      for (const f of files) {
        if (!f) continue
        const id = attachmentId(f)
        if (existing.has(id)) continue
        if (next.length >= MAX_FILES_PER_MESSAGE) {
          toast(`每条消息最多 ${MAX_FILES_PER_MESSAGE} 个文件，超出部分已忽略。`, 'error')
          break
        }

        // 只有内联视觉通道支持的图片才走 base64；其它图片（svg/bmp/tiff/heic…）
        // 或超限图片降级为普通文件附件，随工作区上传，交给 file-viewer 预览。
        if (f.type.startsWith('image/') && isSupportedImageType(f.type)) {
          if (imageCount >= MAX_IMAGES_PER_MESSAGE) {
            toast(`每条消息最多 ${MAX_IMAGES_PER_MESSAGE} 张图片，超出部分已忽略。`, 'error')
            continue
          }
          const r = await fileToImageBlock(f)
          if (!isImageError(r)) {
            const att: PendingAttachment = { kind: 'image', id, file: f, block: r }
            next.push(att)
            addedAttachments.push(att)
            existing.add(id)
            imageCount += 1
            added += 1
            continue
          }
          if (r.kind === 'read-failure') {
            toast(r.message, 'error')
            continue
          }
        }

        const att: PendingAttachment = { kind: 'file', id, file: f }
        next.push(att)
        addedAttachments.push(att)
        existing.add(id)
        added += 1
      }

      if (added > 0) {
        setPendingAttachments(next)
        // 选中即预上传，不等发送；失败/跳过在回调里 toast
        for (const att of addedAttachments) void startPrefetch(att)
      }
      return added > 0
    },
    [attachmentId, pendingAttachments, startPrefetch, toast],
  )

  const removeAttachment = useCallback((index: number) => {
    setPendingAttachments((prev) => {
      const att = prev[index]
      if (att) {
        prefetchAbortRef.current.get(att.id)?.abort()
        prefetchAbortRef.current.delete(att.id)
        prefetchPromiseRef.current.delete(att.id)
      }
      return prev.filter((_, i) => i !== index)
    })
  }, [])

  const handleDrop = useCallback(
    (event: React.DragEvent<HTMLFormElement>) => {
      event.preventDefault()
      setIsDragging(false)
      dragCounterRef.current = 0
      const files = Array.from(event.dataTransfer?.files ?? [])
      if (files.length === 0) return
      void addFiles(files)
    },
    [addFiles],
  )

  const handleDragEnter = useCallback((event: React.DragEvent<HTMLFormElement>) => {
    if (!Array.from(event.dataTransfer?.types ?? []).includes('Files')) return
    dragCounterRef.current += 1
    setIsDragging(true)
  }, [])

  const handleDragLeave = useCallback((_event: React.DragEvent<HTMLFormElement>) => {
    dragCounterRef.current = Math.max(0, dragCounterRef.current - 1)
    if (dragCounterRef.current === 0) setIsDragging(false)
  }, [])

  const handleFilePicked = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(event.target.files ?? [])
      event.target.value = ''
      if (files.length === 0) return
      void addFiles(files)
    },
    [addFiles],
  )

  const focusInputAtEnd = useCallback(() => {
    requestAnimationFrame(() => editorRef.current?.focusEnd())
  }, [])

  // 首次挂载 + 切换会话时自动聚焦输入框——用户直接敲字即可，无需先点击
  useEffect(() => {
    focusInputAtEnd()
  }, [focusInputAtEnd, conversationId])

  const handleOptimizePrompt = useCallback(async () => {
    // 正文含内联卡片时不优化：优化结果会整体替换正文，卡片无处安放
    if (collectChips(input).length > 0) return
    const trimmed = input.trim()
    if (!trimmed || isOptimizing) return
    setIsOptimizing(true)
    try {
      const optimized = await controller.optimizePrompt(trimmed)
      setInput(optimized)
      requestAnimationFrame(() => focusInputAtEnd())
    } catch (err) {
      const msg = err instanceof Error ? err.message : '提示词优化失败，请稍后重试。'
      toast(msg, 'error')
    } finally {
      setIsOptimizing(false)
    }
  }, [input, controller, isOptimizing, focusInputAtEnd, toast])

  // ---- 语音输入（流式）：PCM 采集 → WS 逐片段送 ASR → 边说话边出字 ----
  const toggleVoiceInput = useCallback(async () => {
    // 流式识别进行中 → 停止采集并等待最终结果
    if (asrSessionRef.current) {
      setIsRecording(false)
      setIsTranscribing(true)
      asrSessionRef.current.stop()
      return
    }
    if (clarifyFlow.isClarifying || isUploading || isStreaming) return
    if (!token) {
      toast('登录已过期，请重新登录', 'error')
      return
    }

    const base = input.trim()
    const session = new StreamingAsrSession()
    asrSessionRef.current = session
    try {
      await session.start(token, {
        onReady: () => setIsRecording(true),
        onPartial: (text) => {
          // 增量结果会反复改写，直接覆盖上屏，不做追加
          setInput(base ? `${base} ${text}` : text)
        },
        onFinal: (text) => {
          setInput(base ? `${base} ${text}` : text)
          setIsRecording(false)
          setIsTranscribing(false)
          asrSessionRef.current = null
          requestAnimationFrame(() => focusInputAtEnd())
        },
        onError: (message) => {
          toast(message, 'error')
          setIsRecording(false)
          setIsTranscribing(false)
          asrSessionRef.current = null
        },
        onClosed: () => {
          // 没拿到 final 就结束（停止过早 / 上游中断）：复位 UI，保留已有文字
          if (asrSessionRef.current !== session) return
          asrSessionRef.current = null
          setIsRecording(false)
          setIsTranscribing(false)
        },
      })
    } catch (err) {
      // 先取状态再 abort：abort() 会把 userClosed 置为 true
      const wasUserClosed = session.userClosed
      session.abort()
      asrSessionRef.current = null
      setIsRecording(false)
      setIsTranscribing(false)
      // 用户主动中断（连接尚未就绪时点停止）不算错误
      if (wasUserClosed) return
      const message = err instanceof Error ? err.message : '实时语音识别不可用'
      toast(message, 'error')
    }
  }, [
    token,
    input,
    clarifyFlow.isClarifying,
    isUploading,
    isStreaming,
    focusInputAtEnd,
    toast,
  ])

  // 组件卸载时中断流式识别
  useEffect(() => {
    return () => {
      asrSessionRef.current?.abort()
      asrSessionRef.current = null
    }
  }, [])

  const handlePickSuggestion = useCallback(
    (prompt: string) => {
      setInput(prompt)
      focusInputAtEnd()
    },
    [focusInputAtEnd],
  )

  useEffect(() => {
    if (!prefillInput) return
    setInput(prefillInput.text)
    focusInputAtEnd()
  }, [prefillInput, focusInputAtEnd])

  const scrollerRef = useRef<HTMLDivElement>(null)

  const items = useMemo<Message[]>(() => {
    const base: Message[] =
      layout === 'default' && messages.length === 0 && !isLoadingHistory
        ? [
            {
              id: '__welcome__',
              role: 'assistant',
              content: WELCOME_CHOICE_CARD,
              timestamp: Date.now(),
            },
          ]
        : messages

    if (!isStreaming && !streamingText) return base
    const streaming: Message = {
      id: STREAMING_ID,
      role: 'assistant',
      content: streamingText,
      timestamp: Date.now(),
      toolCalls: liveToolCalls.length > 0 ? liveToolCalls : undefined,
      subagentTrace: liveSubagentItems.length > 0 ? liveSubagentItems : undefined,
      timeline: liveTimeline.length > 0 ? liveTimeline : undefined,
    }
    return [...base, streaming]
  }, [isLoadingHistory, isStreaming, layout, liveSubagentItems, liveToolCalls, liveTimeline, messages, streamingText])

  const waitingUserMessages = useMemo(() => {
    return (queuedUserMessages ?? []).filter((m) => (m.text || '').trim().length > 0)
  }, [queuedUserMessages])

  const latestAssistantMessageId = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const msg = messages[i]
      if (msg?.role === 'assistant') return msg.id
    }
    return null
  }, [messages])

  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollerRef.current,
    estimateSize: () => 96,
    overscan: 6,
    getItemKey: (index) => items[index]?.id ?? index,
  })

  // 底部对齐的虚拟器精修：程序性滚动后虚拟器先用 estimateSize 绘制、再经
  // measureElement 重测，需要对齐两帧后重新 scrollToIndex 校正。滚动状态机
  // （useChatScrollController）在对齐底部后回调此处。
  const refineScrollToEnd = useCallback(() => {
    if (items.length > 0) {
      virtualizer.scrollToIndex(items.length - 1, { align: 'end' })
    }
  }, [items.length, virtualizer])

  // 滚动状态机（三态）：bottom 跟随底部 / pinned 问题钉顶 / reading 用户滚离阅读。
  // 发问即钉顶、生成期间不自动滚底（用户先看前面的内容）；按钮只做「回到底部」。
  const scrollActivityKey = `${items.length}|${streamingText.length}|${liveToolCalls.length}|${liveSubagentItems.length}|${liveTimeline.length}`
  const scroll = useChatScrollController({
    scrollerRef,
    isStreaming,
    activityKey: scrollActivityKey,
    refineScrollToEnd,
    resetKey: conversationId,
  })
  const { pinNewQuestion } = scroll

  // 置顶的唯一触发点：消费 store 里的 pinRequest（发问指令）。发送动作在
  // appendMessage 时显式记录「这条 user 消息是我刚发的」，与发问入口无关
  // （textarea 提交、继续问引导、文件上传、确认继续、队列执行都走同一动作）。
  // 消费时机必须等历史加载落定：landing 首次发问时 appendMessage 必然落在
  // history fetch（isLoadingHistory）窗口内——此前用「比较前后末条消息 id」
  // 的启发式探测，该窗口内守卫会把置顶永久吞掉（真实链路 bug，注入测试测不出）。
  const pinRequest = useChatStore((s) =>
    conversationId ? s.convStates[conversationId]?.pinRequest ?? null : null,
  )
  useEffect(() => {
    if (!pinRequest || !conversationId) return
    if (isLoadingHistory) return
    const idx = messages.findIndex((m) => m.id === pinRequest.messageId)
    if (idx === -1) return // 消息尚未出现在列表（极端时序），等下一轮渲染再消费
    pinNewQuestion(idx)
    useChatStore.getState().clearPinRequest(conversationId)
  }, [pinRequest, isLoadingHistory, messages, pinNewQuestion, conversationId])

  const submit = useCallback(async (textOverride?: string) => {
    // 正文以标记串承载卡片；判空只看卡片以外的文字（只有卡片不算有效提问），
    // 发送仍用原始正文（含标记）
    const trimmed = (textOverride ?? input).trim()
    const hasText = hasPlainText(trimmed)
    const imageAttachments = pendingAttachments.filter((a): a is Extract<PendingAttachment, { kind: 'image' }> => a.kind === 'image')
    const hasImages = imageAttachments.length > 0
    const hasFiles = pendingAttachments.length > 0
    if (!hasText && !hasImages && !hasFiles) return
    if (isUploading) return
    if (clarifyFlow.isClarifying) return

    const now = Date.now()
    if (submitInFlightRef.current) return
    if (now < submitCooldownUntilRef.current) return
    submitInFlightRef.current = true
    submitCooldownUntilRef.current = now + 350

    let finalText = trimmed
    let outgoingAttachments: AttachmentBlock[] | undefined
    try {
      if (hasFiles) {
        setIsUploading(true)
        try {
          // 附件在选中时就开始预上传；这里只等待尚未完成的请求，
          // 不再重复上传，也保证失败项不会混进消息。
          const settled = await Promise.all(
            pendingAttachments.map(async (att) => {
              if (att.upload?.status === 'done') return att
              const promise = prefetchPromiseRef.current.get(att.id) ?? startPrefetch(att)
              return { ...att, upload: await promise } as PendingAttachment
            }),
          )
          const failed = settled.filter((a) => a.upload?.status !== 'done')
          if (failed.length > 0) {
            toast(`有 ${failed.length} 个附件上传失败，已从本次发送中移除。`, 'error')
          }
          const blocks = buildOutgoingAttachments(settled)
          if (blocks.length === 0) return
          outgoingAttachments = blocks
        } finally {
          setIsUploading(false)
        }
      }

      // 技能/专家/引用文件都在正文标记串里，随 content 一并发送，不再单发字段
      void controller.sendMessage(finalText, outgoingAttachments)
      setInput('')
      setPendingAttachments([])
      // 附件已随本轮发送（或已清空）：上传侧临时 session 不再复用
      uploadSessionIdRef.current = null
      prefetchAbortRef.current.clear()
      prefetchPromiseRef.current.clear()
      // 滚动权归滚动状态机：发问置顶由 store 里的 pinRequest 指令触发
      // （appendMessage 记录 → 上方消费 effect → scroll.pinNewQuestion），
      // 与发问入口无关，此处不做滚动处理。

      const latestAssistantMsg = messages.filter((m) => m.role === 'assistant').pop()
      if (latestAssistantMsg) {
        markMsgClarified(conversationId, latestAssistantMsg.id)
      }
      clarifyFlow.resetSelections()
    } finally {
      window.setTimeout(() => {
        submitInFlightRef.current = false
      }, 350)
    }
  }, [
    input,
    isUploading,
    clarifyFlow,
    controller,
    pendingAttachments,
    startPrefetch,
    messages,
    items,
    markMsgClarified,
    conversationId,
    toast,
  ])

  const totalSize = virtualizer.getTotalSize()
  const virtualItems = virtualizer.getVirtualItems()

  const showEmptyState = items.length === 0 && !isLoadingHistory
  const showLanding = layout === 'landing' && items.length === 0 && !isLoadingHistory

  // 胶囊文案（2026-09-02 用户反馈）：生成期统一显示「AI 正在处理」loading 态，
  // 不再区分 analyzing / executing / waiting_llm / compacting 等子相位——用户感知
  // 到「LLM 还在跑」就行，工具细节归右侧任务面板（ExecutionTimeline / useTaskPanelData
  // 保留 phase 文案分发的细粒度状态，那里是给操作者看的「执行细节」层）。
  const streamingLabel = 'AI 正在处理'

  // landing 与 normal 两处输入框共享同一组 props（此前是两份逐属性拷贝，
  // 仅 variant/placeholder/children 有差异，留在 JSX 处）。
  const chatInputBoxProps: Omit<
    ChatInputBoxProps,
    'variant' | 'placeholder' | 'children'
  > = {
    input,
    isUploading,
    isOptimizing,
    isRecording,
    isTranscribing,
    isStreaming,
    isClarifying: clarifyFlow.isClarifying,
    isDragging,
    pendingAttachments,
    editorRef,
    fileInputRef,
    onInputChange: setInput,
    onSubmit: (text?: string) => void submit(text),
    onFilePicked: handleFilePicked,
    onPasteFiles: (files: File[]) => void addFiles(files),
    onDragEnter: handleDragEnter,
    onDragLeave: handleDragLeave,
    onDragOver: (e) => {
      if (Array.from(e.dataTransfer?.types ?? []).includes('Files')) {
        e.preventDefault()
      }
    },
    onDrop: handleDrop,
    onOptimizePrompt: () => void handleOptimizePrompt(),
    onToggleVoice: () => void toggleVoiceInput(),
    onAbort: controller.abort,
    onRemoveAttachment: removeAttachment,
    onPreviewAttachment: (att) => {
      const upload = att.upload
      if (upload?.status === 'done' && upload.path) {
        if (onOpenFilePreview) onOpenFilePreview(upload.path)
        else toast('当前页面不支持文件预览', 'info')
        return
      }
      if (upload?.status === 'error') {
        toast(upload.error || '附件上传失败，无法预览', 'error')
        return
      }
      toast('附件还在上传中，请稍候', 'info')
    },
    conversationMessages: messages,
    workspaceId,
    token,
  }

  if (showLanding) {
    return (
      <div className="chat-area flex flex-col flex-1 min-w-0 min-h-0 relative">
        <div className="chat-scroller flex-1 min-h-0 overflow-y-auto px-4 py-6 md:py-10 relative">
          <div className="min-h-full flex flex-col items-center justify-center">
            {/* Landing 品牌展示区：大光晕提升到 860px 容器级，跨 logo+title+subtitle 全覆盖，
                 不再局限在 logo 脚下；主+尾双椭圆叠加，title 右侧不硬截；
                 opacity 45% 只给氛围不抢文字；全部用主题变量无 color-mix */}
            <div className="max-w-[860px] mx-auto w-full relative">
              <div className="flex flex-col items-center text-center">
                <div className="relative">
                  <div className="relative flex items-center gap-5">
                    {/* 仅展示 logo 图片，去掉渠道色块容器；尺寸放大到 96px */}
                    <img
                      src={landingLogo}
                      alt="PrismClaw"
                      className="w-36 h-36 object-contain relative z-10"
                      draggable={false}
                    />
                    <div className="text-left">
                      <div className="text-6xl md:text-7xl font-display font-bold tracking-tight leading-none text-[var(--c-text)]">
                        Prism<span className="text-[var(--c-accent)]">Claw</span>
                      </div>
                      <div className="mt-4 text-[length:var(--fs-lg)] md:text-[length:var(--fs-xl)] font-medium text-[var(--c-text-secondary)]">
                        随时随地，帮您高效干活
                      </div>
                    </div>
                  </div>
                </div>
              </div>

            {/* 输入框模块 */}
            <div className="mt-10">
              <ChatInputBox
                variant="landing"
                {...chatInputBoxProps}
                placeholder="发消息、发图片、发文件、创建自动化任务、/快捷调用、@添加上下文"
              />

            <div className="mt-2 flex items-center">
              <ModeToggle />
              <ServerWorkspaceManager
                workspaceId={workspaceId}
                workspaces={workspaces}
                isLoading={isWorkspacesLoading}
                onSelectWorkspace={onSelectWorkspace}
                onCreateWorkspace={onCreateServerWorkspace}
                onUploadFiles={handleUploadServerFiles}
                onRemoveWorkspace={onRemoveServerWorkspace}
                readOnly={!isWorkMode}
              />
              <div className="flex-1 min-w-0 ml-1 flex items-center justify-end">
                <UploadTicker tasks={uploadTasks} onClick={() => setUploadQueueOpen(true)} />
              </div>
            </div>
          </div>

            {/* 提示任务模块 */}
            <EmptyStateSuggestions onPick={handlePickSuggestion} />
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="chat-area flex flex-col flex-1 min-w-0 min-h-0 relative">
      <div
        ref={scrollerRef}
        onScroll={scroll.handleScroll}
        className="chat-scroller flex-1 overflow-y-auto px-4 py-6 md:px-8 md:py-10"
      >
        <div className="mx-auto w-full" style={{ maxWidth: chatWidth }}>
          {isLoadingHistory && items.length === 0 && (
            <div className="pt-4">
              <MessageSkeleton />
            </div>
          )}

          {showEmptyState && <EmptyStateSuggestions onPick={handlePickSuggestion} />}

          {!showEmptyState && !isLoadingHistory && (
            <div style={{ height: totalSize, position: 'relative', width: '100%' }}>
              {virtualItems.map((vi) => {
                const item = items[vi.index]
                if (!item) return null
                const isStreamingItem = item.id === STREAMING_ID
                const followups = !isStreamingItem ? item.metadata?.followupQuestions : undefined
                const showSupplementalSections =
                  !isStreamingItem &&
                  item.role === 'assistant' &&
                  item.id === latestAssistantMessageId &&
                  item.id === messages[messages.length - 1]?.id
                // 为 assistant 消息提取前一条 user 消息的提问文本（供导出使用）
                const questionText = (() => {
                  if (item.role !== 'assistant') return null
                  const prev = items[vi.index - 1]
                  if (!prev || prev.role !== 'user') return null
                  return extractQuestionPlainText(prev.content)
                })()
                return (
                  <div
                    key={vi.key}
                    data-index={vi.index}
                    ref={virtualizer.measureElement}
                    style={{
                      position: 'absolute',
                      top: 0,
                      left: 0,
                      width: '100%',
                      transform: `translateY(${vi.start}px)`,
                    }}
                  >
                    <MessageBubble
                      message={item}
                      isStreaming={isStreamingItem}
                      phase={isStreamingItem ? livePhase : undefined}
                      runGate={isStreamingItem ? liveRunGate : undefined}
                      elapsedS={isStreamingItem ? liveElapsedS : undefined}
                      followupQuestions={followups}
                      showSupplementalSections={showSupplementalSections}
                      questionText={questionText}
                      onQuickReply={(t) => void controller.sendMessage(t)}
                      onOpenFilePreview={onOpenFilePreview}
                      onOpenFile={onOpenFilePreview}
                      onCopy={handleCopy}
                      onFeedback={handleFeedback}
                      onShare={handleShare}
                      onRetry={
                        item.role === 'error'
                          ? () => controller.retryMessage(item.id)
                          : undefined
                      }
                      workspaceId={workspaceId}
                      token={token}
                      conversationId={conversationId}
                      onOpenReviewInPanel={onOpenReviewInPanel}
                    />
                  </div>
                )
              })}
            </div>
          )}

          {/* pinned/reading 期间的自适应留白占位：缺口 = 视口高 − 问题顶到内容
              末尾的距离（见 useChatScroll.measureSpacer）。发送瞬间补足一屏
              （问题钉顶、旧内容推出视口上方），回答流式展开 1:1 消耗缺口，
              超过一屏后归零——滚动范围终结在真实内容末尾，不拖死白。 */}
          {scroll.mode !== 'bottom' && (
            <div aria-hidden style={{ height: scroll.spacerHeight }} />
          )}

        </div>
      </div>

      <div className="shrink-0 px-4 pt-4 pb-2 md:px-8">
        <div className="mx-auto w-full" style={{ maxWidth: chatWidth }}>
          {/* 底部悬浮按钮，只有两态、永不向上定位：
              1. pinned（发问置顶生成中）→ spinner「正在生成…」loading 态，点击滚动到底部；
              2. 未贴底（reading 滚离 / 结束后停在上方）→ 「回到底部」，点击一键回底。 */}
          {scroll.contentOverflow && !scroll.atBottom && (
            <div className="mb-3 flex justify-center">
              <button
                type="button"
                onClick={scroll.scrollToBottom}
                className={`flex items-center gap-1.5 rounded-full border backdrop-blur-sm
                           px-4 py-1.5 text-[length:var(--fs-sm)] font-medium
                           shadow-[var(--shadow-md)]
                           active:scale-95
                           transition-all cursor-pointer
                           ${isStreaming
                             ? 'border-[var(--c-accent)]/40 bg-[var(--c-accent-soft)] text-[var(--c-accent)] hover:bg-[var(--c-accent-soft)] hover:text-[var(--c-accent)]'
                             : 'border-[var(--c-border)]/60 bg-[var(--c-surface)]/80 text-[var(--c-text-secondary)] hover:bg-[var(--c-surface-2)] hover:text-[var(--c-text)] hover:border-[var(--c-border)]'
                           }`}
                title={isStreaming ? streamingLabel : '回到底部'}
                aria-label={isStreaming ? streamingLabel : '回到底部'}
              >
                {isStreaming ? (
                  <Loader2 size={14} strokeWidth={2.5} className="block shrink-0 animate-spin" />
                ) : (
                  <ArrowDown size={14} strokeWidth={2.5} className="block shrink-0" />
                )}
                <span>{isStreaming ? streamingLabel : '回到底部'}</span>
              </button>
            </div>
          )}

          <QueueBar
            controller={controller}
            waitingUserMessages={waitingUserMessages}
            isQueued={isQueued}
            queuePosition={queuePosition}
            isStreaming={isStreaming}
          />

          <ChatInputBox
            variant="normal"
            {...chatInputBoxProps}
            placeholder="发消息、发图片、发文件、创建自动化任务、/快捷调用、@添加上下文"
          >
              <ClarifyCard flow={clarifyFlow} />

              {confirmationVisible && (
                <div className="mb-3 p-4 rounded-[var(--radius-card)] border border-[var(--c-border)] bg-[var(--c-surface)]/80 backdrop-blur-sm">
                  <div className="text-[length:var(--fs-sm)] font-medium text-[var(--c-text)] mb-2">需要确认方向</div>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => controller.submitConfirmation('approve')}
                      disabled={confirmationDisabled}
                      className="btn btn-md btn-primary"
                    >
                      确认继续
                    </button>
                    <button
                      type="button"
                      onClick={() => controller.submitConfirmation('deny')}
                      disabled={confirmationDisabled}
                      className="btn btn-md btn-secondary"
                    >
                      取消任务
                    </button>
                  </div>
                </div>
              )}
          </ChatInputBox>

            <div className="mt-2 flex items-center">
              <ModeToggle />
              <ServerWorkspaceManager
                workspaceId={workspaceId}
                workspaces={workspaces}
                isLoading={isWorkspacesLoading}
                onSelectWorkspace={onSelectWorkspace}
                onCreateWorkspace={onCreateServerWorkspace}
                onUploadFiles={handleUploadServerFiles}
                onRemoveWorkspace={onRemoveServerWorkspace}
                readOnly={!isWorkMode}
              />
              <div className="flex-1 min-w-0 ml-1 flex items-center justify-end">
                {uploadTasks.length > 0 && (
                  <UploadTicker tasks={uploadTasks} onClick={() => setUploadQueueOpen(true)} />
                )}
              </div>
            </div>
        </div>
      </div>

      <UploadQueueModal
        tasks={uploadTasks}
        open={uploadQueueOpen}
        onClose={() => setUploadQueueOpen(false)}
      />
    </div>
  )
}
