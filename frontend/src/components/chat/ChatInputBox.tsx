import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Square, Plus, X, LoaderCircle, Sparkles, Mic, Pause,
} from 'lucide-react'
import type { PendingAttachment, PendingUpload } from '../../lib/chat/attachments'
import { imageBlockToDataUrl } from '../../lib/file/image'
import FileIcon from '../file/FileIcon'
import PlusMenu from './PlusMenu'
import type { PlusMenuAction } from './PlusMenu'
import SkillSelector from './SkillSelector'
import type { SkillCatalogItem } from './SkillSelector'
import PluginSelector, { expertLocalizedText } from './PluginSelector'
import type { ExpertCatalogItem } from './PluginSelector'
import AbilityMenu from './AbilityMenu'
import type { AbilityMenuHandle, AbilityPick } from './AbilityMenu'
import ContextPicker from './ContextPicker'
import type { ContextPickerHandle } from './ContextPicker'
import { useItemTooltip } from './useItemTooltip'
import { monogram } from './monogram'
import type { ContextRef } from '../../lib/chat/contextRefs'
import type { Message } from '../../types/chat'
import RichInput from './RichInput'
import type { RichInputHandle } from './RichInput'
import { collectChips, hasPlainText } from '../../lib/chat/inputMarkers'
import type { ChipRef } from '../../lib/chat/inputMarkers'
import { parseTrigger } from '../../lib/chat/inputTrigger'
import { apiUrl, authedUrl } from '../../lib/system-adapter'
import { formatBytes } from '../../lib/file/outputFiles'
import { useToast } from '../../lib/toast'
import FileLibraryModal from './FileLibraryModal'
import { DRAFT_SESSION_KEY, EMPTY_EXPERT_STATE, useSessionStore } from '../../stores/session'
import type { ExpertState } from '../../stores/session'
import iconSend from '../../images/other_icon/send.png'

/** 专家不可用原因码 → 用户可读文案（码与后端 ExpertErrorCode 一致）。 */
const EXPERT_UNAVAILABLE_TEXT: Record<string, string> = {
  EXPERT_UNAVAILABLE: '该专家已不可用：未部署或已被移除',
  MODULE_DISABLED: '该专家已被管理员禁用',
  EXPERT_ID_AMBIGUOUS: '该专家名称存在歧义，请重新选择',
  EXPERT_PROMPT_MISSING: '该专家的人格文件缺失',
}

function formatAttachmentMeta(file: File): string {
  const rawName = file?.name || ''
  const ext = (rawName.split('.').pop() || '').trim().toUpperCase()
  const extLabel = ext || 'FILE'
  const mb = file.size / (1024 * 1024)
  const displayMb = Math.max(0.01, mb)
  return `${extLabel}\u00b7${displayMb.toFixed(2)}MB`
}

function AttachmentCard({ file, imageSrc, upload, onPreview, onRemove, onTitleEnter, onTitleLeave }: {
  file: File
  imageSrc?: string
  upload?: PendingUpload
  onPreview: () => void
  onRemove: () => void
  onTitleEnter: (e: React.MouseEvent<HTMLElement>) => void
  onTitleLeave: () => void
}) {
  const uploading = upload?.status === 'uploading'
  const failed = upload?.status === 'error'
  const statusText = uploading
    ? '上传中…'
    : failed
      ? (upload?.error || '上传失败')
      : formatAttachmentMeta(file)

  return (
    // 外层不裁剪：删除按钮有一半探出卡片右上角，tooltip 也由 portal 渲染
    <div className={`relative group h-16 shrink-0 ${imageSrc ? 'w-16' : 'w-[200px] max-w-full'}`}>
      <div
        role="button"
        tabIndex={0}
        onClick={onPreview}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            onPreview()
          }
        }}
        className={`h-full w-full rounded-[var(--radius-md)] border overflow-hidden cursor-pointer transition-colors ${failed ? 'border-[var(--c-error)]/60' : 'border-[var(--c-border)] hover:border-[var(--c-accent)]/60'} bg-[var(--c-surface-2)]/40`}
      >
        {imageSrc ? (
          <img src={imageSrc} alt={file.name} className="w-full h-full object-cover" draggable={false} />
        ) : (
          <div className="h-full flex items-center gap-2.5 pl-2.5 pr-4 min-w-0">
            <FileIcon filename={file.name} size={24} className="shrink-0" />
            <div className="min-w-0 flex-1">
              <div
                onMouseEnter={onTitleEnter}
                onMouseLeave={onTitleLeave}
                className="text-[length:var(--fs-sm)] font-medium text-[var(--c-text)] truncate"
              >
                {file.name}
              </div>
              <div className={`text-[length:var(--fs-xs)] truncate ${failed ? 'text-[var(--c-error)]' : 'text-[var(--c-text-secondary)]/80'}`}>
                {statusText}
              </div>
            </div>
          </div>
        )}

        {/* 图片上传中：半透明遮罩 + 转圈；文件卡用副标题文字表达，避免遮住文件名 */}
        {uploading && imageSrc && (
          <div className="absolute inset-0 grid place-items-center bg-[var(--c-surface)]/55 pointer-events-none">
            <LoaderCircle size={16} className="animate-spin text-[var(--c-accent)]" />
          </div>
        )}

        {/* 文件名浮层 — hover 显示 */}
        {imageSrc && (
          <div
            onMouseEnter={onTitleEnter}
            onMouseLeave={onTitleLeave}
            className="absolute inset-x-0 bottom-0 px-1.5 py-0.5 bg-gradient-to-t from-black/60 to-transparent text-white text-[length:var(--fs-xs)] truncate opacity-0 group-hover:opacity-100 transition-opacity"
          >
            {statusText}
          </div>
        )}
      </div>

      {/* 设计稿：深灰圆形角标，压在卡片右上角外侧 */}
      <button
        type="button"
        aria-label="移除附件"
        onClick={(e) => { e.stopPropagation(); onRemove() }}
        className="absolute z-10 -top-1.5 -right-1.5 w-5 h-5 rounded-full grid place-items-center bg-[#5b6673] text-white shadow-[0_1px_3px_rgba(0,0,0,0.28)] hover:bg-[#49525e] transition-all cursor-pointer opacity-0 scale-90 pointer-events-none group-hover:opacity-100 group-hover:scale-100 group-hover:pointer-events-auto focus-visible:opacity-100 focus-visible:scale-100 focus-visible:pointer-events-auto"
      >
        <X size={11} strokeWidth={2.5} />
      </button>
    </div>
  )
}

export type ChatInputBoxVariant = 'landing' | 'normal'

export interface ChatInputBoxProps {
  input: string; isUploading: boolean; isOptimizing: boolean; isRecording: boolean
  isTranscribing: boolean; isStreaming: boolean; isClarifying: boolean; isDragging: boolean
  pendingAttachments: PendingAttachment[]
  editorRef: React.RefObject<RichInputHandle | null>
  fileInputRef: React.RefObject<HTMLInputElement | null>
  onInputChange: (value: string) => void; onSubmit: (text?: string) => void
  onFilePicked: (e: React.ChangeEvent<HTMLInputElement>) => void
  /** 剪贴板中的文件（图片/PDF/代码等）交给宿主上传，文本由输入框自行插入 */
  onPasteFiles: (files: File[]) => void
  onDragEnter: (e: React.DragEvent<HTMLFormElement>) => void
  onDragLeave: (e: React.DragEvent<HTMLFormElement>) => void
  onDragOver: (e: React.DragEvent<HTMLFormElement>) => void
  onDrop: (e: React.DragEvent<HTMLFormElement>) => void
  onOptimizePrompt: () => void; onToggleVoice: () => void; onAbort: () => void
  onRemoveAttachment: (index: number) => void
  /** 点击已选附件：已上传则打开右侧预览，未完成时由宿主提示 */
  onPreviewAttachment: (att: PendingAttachment) => void
  /** 文件库多选结果 → 待定区引用卡片（abilitiesEnabled=false 的宿主可不提供） */
  onAddRefs?: (refs: { path: string; name: string; sizeBytes?: number }[]) => void
  /** 单条消息附件上限，用于限制引用文件弹框的勾选数量 */
  maxRefFiles?: number
  variant: ChatInputBoxVariant; placeholder: string
  conversationMessages: Message[]
  workspaceId: string; token: string | null
  /**
   * 是否启用「技能 / 专家 / 引用文件 / 附件」这一整套能力入口（➕ 菜单 + `/`、`@` 触发）。
   * 默认 true；没有这些能力的宿主（如需要精简入口的域）显式关掉，避免出现点了没反应的死控件。
   */
  abilitiesEnabled?: boolean
  children?: React.ReactNode
}

/** 单条消息可携带的引用文件上限（上下文预算保护） */
const MAX_CONTEXT_REFS = 20

export default function ChatInputBox({
  input, isUploading, isOptimizing, isRecording, isTranscribing, isStreaming,
  isClarifying, isDragging, pendingAttachments,
  editorRef, fileInputRef, onInputChange, onSubmit, onFilePicked, onPasteFiles,
  onDragEnter, onDragLeave, onDragOver, onDrop, onOptimizePrompt, onToggleVoice,
  onAbort, onRemoveAttachment, onPreviewAttachment, onAddRefs, maxRefFiles, variant, placeholder,
  conversationMessages, workspaceId, token,
  abilitiesEnabled = true, children,
}: ChatInputBoxProps) {
  const isLanding = variant === 'landing'
  const [plusMenuOpen, setPlusMenuOpen] = useState(false)
  const [skillPanelOpen, setSkillPanelOpen] = useState(false)
  const [pluginPanelOpen, setPluginPanelOpen] = useState(false)
  const [contextFlyoutOpen, setContextFlyoutOpen] = useState(false)
  const [fileLibraryOpen, setFileLibraryOpen] = useState(false)
  const { toast } = useToast()
  const toolbarRef = useRef<HTMLDivElement>(null)
  const formRef = useRef<HTMLFormElement>(null)
  // 悬浮离开工具栏后延迟收起子面板的定时器（hover-intent，避免「粘滞」）
  const collapseTimer = useRef<number | null>(null)
  const abilityRef = useRef<AbilityMenuHandle>(null)
  const contextRefRef = useRef<ContextPickerHandle>(null)
  const sessionMutationQueues = useRef(new Map<string, Promise<void>>())

  const activeConvId = useSessionStore((s) => s.activeConvId)
  const sessionKey = activeConvId ?? DRAFT_SESSION_KEY
  const expertState = useSessionStore((s) => s.expertStates[sessionKey] ?? EMPTY_EXPERT_STATE)
  const selectedSkillIds = expertState.selected_skill_ids
  const [experts, setExperts] = useState<ExpertCatalogItem[]>([])
  const [skills, setSkills] = useState<SkillCatalogItem[]>([])
  const [expertsLoading, setExpertsLoading] = useState(false)
  const [skillsLoading, setSkillsLoading] = useState(false)

  useEffect(() => {
    if (!abilitiesEnabled || !token) return
    let cancelled = false
    setExpertsLoading(true)
    void (async () => {
      try {
        const res = await fetch(apiUrl('/api/experts'), { headers: { Authorization: `Bearer ${token}` } })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data = await res.json() as ExpertCatalogItem[]
        if (!cancelled) setExperts(Array.isArray(data) ? data : [])
      } catch {
        if (!cancelled) setExperts([])
      } finally {
        if (!cancelled) setExpertsLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [abilitiesEnabled, token])

  useEffect(() => {
    if (!abilitiesEnabled || !token) return
    let cancelled = false
    setSkillsLoading(true)
    void (async () => {
      try {
        const res = await fetch(apiUrl('/api/skills'), { headers: { Authorization: `Bearer ${token}` } })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const packages = await res.json() as Array<{
          expert_name: string
          expert_type?: string | null
          skills: Array<{ id: string; name: string; name_zh?: string | null; description?: string }>
        }>
        if (!cancelled) {
          setSkills(packages.flatMap((pkg) => pkg.skills.map((skill) => ({
            id: skill.id,
            name: skill.name,
            nameZh: skill.name_zh ?? null,
            description: skill.description ?? '',
            pluginName: pkg.expert_name,
            expertType: pkg.expert_type ?? null,
          }))))
        }
      } catch {
        if (!cancelled) setSkills([])
      } finally {
        if (!cancelled) setSkillsLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [abilitiesEnabled, token])

  const selectedExpert = experts.find((expert) => expert.id === expertState.expert_id)
  const expertName = expertState.expert_id
    ? (selectedExpert ? expertLocalizedText(selectedExpert.displayName) : '') || expertState.expert_id
    : ''

  // ── 触发解析：唯一输入源是编辑器「光标前文本」 ──
  const [caretText, setCaretText] = useState('')
  // Esc / 点击面板外部关闭后，在光标上下文变化前不再重新弹出
  const [dismissedAt, setDismissedAt] = useState<string | null>(null)
  const caretTextRef = useRef('')
  const trigger = abilitiesEnabled && !isClarifying && caretText !== dismissedAt
    ? parseTrigger(caretText)
    : null
  const dismissTrigger = useCallback(() => setDismissedAt(caretTextRef.current), [])

  const handleCaretChange = useCallback((text: string) => {
    caretTextRef.current = text
    setCaretText(text)
  }, [])

  const chips = collectChips(input)
  const fileChips = chips.filter((c) => c.kind === 'file')

  // 会话选择状态是技能 chip 的唯一真相源；发送清空正文后仍保留已选技能。
  useEffect(() => {
    const editor = editorRef.current
    if (!editor) return
    const rendered = collectChips(editor.getValue())
      .filter((chip) => chip.kind === 'skill')
      .map((chip) => chip.value)
    for (const skillId of rendered) {
      if (!selectedSkillIds.includes(skillId)) editor.removeChip('skill', skillId)
    }
    for (const skillId of selectedSkillIds) {
      if (!rendered.includes(skillId)) editor.insertChip({ kind: 'skill', value: skillId })
    }
  }, [editorRef, input, sessionKey, selectedSkillIds])

  // 内容区左右边缘与底部按钮对齐：左=➕图标视觉位置（按钮圆心偏左），右=发送组右缘
  const contentPadLeft = isLanding ? 'pl-[24px]' : 'pl-[20px]'
  const contentPadRight = isLanding ? 'pr-[16px]' : 'pr-[12px]'
  const attachmentPadding = `${contentPadLeft} ${contentPadRight} ${isLanding ? 'pt-6' : 'pt-3'}`
  // 按钮行在文档流内独立成行（非悬浮），编辑器永远不会滚出文字到按钮行底下
  const editorPadding = `${contentPadLeft} ${contentPadRight} ${isLanding ? 'pt-6' : 'pt-4'} pb-2`
  const textareaFocusClass = isLanding ? '' : 'focus:![outline:none] focus-visible:![outline:none] focus-visible:![outline-offset:0]'
  const toolbarPadClass = isLanding ? 'px-4' : 'px-3'
  // 只有卡片、没有正文时不视为可发送（卡片是提问的修饰，不是提问本身；
  // markersToReadable 会把卡片转成 `@名称`，不能用来判空）
  const canSend = hasPlainText(input) || pendingAttachments.length > 0
  // 后端判定该会话的专家已不可用：run 会在落库前被拒，前端不再放行发送。
  const expertUnavailableText = expertState.unavailable_reason
    ? EXPERT_UNAVAILABLE_TEXT[expertState.unavailable_reason] ?? '该专家当前不可用'
    : null
  // 发送键的可用态：与 disabled 条件同源，避免「看着能点、实际被拦」的两套判断
  const canSubmit = canSend && !isUploading && !isClarifying && expertUnavailableText === null

  const handlePlusAction = useCallback((action: PlusMenuAction) => {
    // 打开一个子面板时关闭其他子面板（不关闭主菜单，改用右侧飞出）
    dismissTrigger()
    setSkillPanelOpen(false)
    setPluginPanelOpen(false)
    setContextFlyoutOpen(false)
    switch (action) {
      case 'upload':
        setPlusMenuOpen(false)
        fileInputRef.current?.click()
        break
      case 'quote':
        setPlusMenuOpen(false)
        // 宿主未提供引用能力时不要把用户带进弹框（否则会白复制一份副本）
        if (!onAddRefs) {
          toast('当前场景不支持引用文件', 'info')
          break
        }
        setFileLibraryOpen(true)
        break
      case 'expert': setPluginPanelOpen(true); break
      case 'skill': setSkillPanelOpen(true); break
      case 'connector':
        setPlusMenuOpen(false)
        toast('连接器即将支持', 'info')
        break
    }
  }, [dismissTrigger, fileInputRef, toast, onAddRefs])

  // 单文件策略：选一个就关，和 ContextPicker 的"选完即走"一致。
  // 需要多个再点一次「添加文件」——多选文件通过拖拽或 /file 面板支持。
  const handleFilePickerChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    onFilePicked(e)
    setPlusMenuOpen(false)
  }, [onFilePicked])

  // 点击工具栏（含主菜单 + 任意右侧飞出子面板）外部时，整体收起
  useEffect(() => {
    const anyOpen = plusMenuOpen || skillPanelOpen || pluginPanelOpen || contextFlyoutOpen
    if (!anyOpen) return
    const handler = (e: MouseEvent) => {
      if (toolbarRef.current && !toolbarRef.current.contains(e.target as Node)) {
        setPlusMenuOpen(false)
        setSkillPanelOpen(false)
        setPluginPanelOpen(false)
        setContextFlyoutOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [plusMenuOpen, skillPanelOpen, pluginPanelOpen, contextFlyoutOpen])

  // 悬浮离开工具栏区域（菜单 + 右侧飞出）后延迟收起子面板；重新进入则取消。
  // 菜单/飞出均为该容器的 DOM 后代，指针在两者之间移动不会触发 leave，因此不会误关。
  const cancelCollapse = useCallback(() => {
    if (collapseTimer.current) {
      window.clearTimeout(collapseTimer.current)
      collapseTimer.current = null
    }
  }, [])

  const scheduleCollapse = useCallback(() => {
    cancelCollapse()
    collapseTimer.current = window.setTimeout(() => {
      setSkillPanelOpen(false)
      setPluginPanelOpen(false)
      setContextFlyoutOpen(false)
    }, 200)
  }, [cancelCollapse])

  useEffect(
    () => () => {
      if (collapseTimer.current) window.clearTimeout(collapseTimer.current)
    },
    [],
  )

  // 触发面板与 ➕ 子面板互斥：一旦输入框进入触发态，收起工具栏所有面板
  const triggerKind = trigger?.kind ?? null
  useEffect(() => {
    if (!triggerKind) return
    setPlusMenuOpen(false)
    setSkillPanelOpen(false)
    setPluginPanelOpen(false)
    setContextFlyoutOpen(false)
  }, [triggerKind])

  // 触发面板打开时，点击输入框/面板/发送按钮以外的区域关闭
  useEffect(() => {
    if (!trigger) return
    const handler = (e: MouseEvent) => {
      const target = e.target as Node
      if (formRef.current?.contains(target)) return
      dismissTrigger()
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [trigger, dismissTrigger])

  /** 吃掉 `/` `@` 触发串（不插入任何文本） */
  const consumeTrigger = useCallback(() => {
    if (!trigger) return
    editorRef.current?.deleteBeforeCaret(trigger.length)
  }, [trigger, editorRef])

  const recoverExpertState = useCallback(async (convId: string, previous: ExpertState) => {
    let recovered = previous
    try {
      const res = await fetch(apiUrl(`/api/sessions/${encodeURIComponent(convId)}`), {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (res.ok) {
        const data = await res.json() as { expert_state?: ExpertState }
        if (data.expert_state) recovered = data.expert_state
      }
    } catch {}
    useSessionStore.getState().setExpertState(convId, recovered)
  }, [token])

  const queueSessionMutation = useCallback((conversationId: string, mutation: () => Promise<void>) => {
    const previous = sessionMutationQueues.current.get(conversationId) ?? Promise.resolve()
    const next = previous.then(mutation).catch(() => {})
    sessionMutationQueues.current.set(conversationId, next)
    void next.then(() => {
      if (sessionMutationQueues.current.get(conversationId) === next) {
        sessionMutationQueues.current.delete(conversationId)
      }
    })
  }, [])

  const handleExpertPanelPick = useCallback((expertId: string) => {
    if (!activeConvId) {
      const current = useSessionStore.getState().expertStates[DRAFT_SESSION_KEY] ?? EMPTY_EXPERT_STATE
      useSessionStore.getState().setExpertState(DRAFT_SESSION_KEY, {
        ...current,
        expert_id: expertId,
        source: 'user',
      })
      return
    }
    const conversationId = activeConvId
    queueSessionMutation(conversationId, async () => {
      const previous = useSessionStore.getState().expertStates[conversationId] ?? EMPTY_EXPERT_STATE
      try {
        const res = await fetch(apiUrl(`/api/sessions/${encodeURIComponent(conversationId)}/expert`), {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ expert_id: expertId }),
        })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const next = await res.json() as ExpertState
        useSessionStore.getState().setExpertState(conversationId, next)
      } catch {
        await recoverExpertState(conversationId, previous)
      }
    })
  }, [activeConvId, queueSessionMutation, recoverExpertState, token])

  const handleExpertCancel = useCallback(() => {
    if (!activeConvId) {
      const current = useSessionStore.getState().expertStates[DRAFT_SESSION_KEY] ?? EMPTY_EXPERT_STATE
      useSessionStore.getState().setExpertState(DRAFT_SESSION_KEY, {
        ...current,
        expert_id: null,
        source: null,
      })
      return
    }
    const conversationId = activeConvId
    queueSessionMutation(conversationId, async () => {
      const previous = useSessionStore.getState().expertStates[conversationId] ?? EMPTY_EXPERT_STATE
      try {
        const res = await fetch(apiUrl(`/api/sessions/${encodeURIComponent(conversationId)}/expert`), {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const next = await res.json() as ExpertState
        useSessionStore.getState().setExpertState(conversationId, next)
      } catch {
        await recoverExpertState(conversationId, previous)
      }
    })
  }, [activeConvId, queueSessionMutation, recoverExpertState, token])

  const handleSkillToggle = useCallback((skillId: string) => {
    if (!activeConvId) {
      const current = useSessionStore.getState().expertStates[DRAFT_SESSION_KEY] ?? EMPTY_EXPERT_STATE
      const nextIds = current.selected_skill_ids.includes(skillId)
        ? current.selected_skill_ids.filter((id) => id !== skillId)
        : [...current.selected_skill_ids, skillId]
      useSessionStore.getState().setSelectedSkillIds(DRAFT_SESSION_KEY, nextIds)
      return
    }
    const conversationId = activeConvId
    queueSessionMutation(conversationId, async () => {
      const current = useSessionStore.getState().expertStates[conversationId] ?? EMPTY_EXPERT_STATE
      const nextIds = current.selected_skill_ids.includes(skillId)
        ? current.selected_skill_ids.filter((id) => id !== skillId)
        : [...current.selected_skill_ids, skillId]
      const res = await fetch(apiUrl(`/api/sessions/${encodeURIComponent(conversationId)}/skills`), {
        method: 'PUT',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ selected_skill_ids: nextIds }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json() as { selected_skill_ids: string[] }
      useSessionStore.getState().setSelectedSkillIds(conversationId, data.selected_skill_ids)
    })
  }, [activeConvId, queueSessionMutation, token])

  const handleChipRemove = useCallback((chip: ChipRef) => {
    if (chip.kind !== 'skill') return
    if (!activeConvId) {
      const current = useSessionStore.getState().expertStates[DRAFT_SESSION_KEY] ?? EMPTY_EXPERT_STATE
      useSessionStore.getState().setSelectedSkillIds(
        DRAFT_SESSION_KEY,
        current.selected_skill_ids.filter((id) => id !== chip.value),
      )
      return
    }
    const conversationId = activeConvId
    queueSessionMutation(conversationId, async () => {
      const skillPath = chip.value.split('/').map((part) => encodeURIComponent(part)).join('/')
      const res = await fetch(
        apiUrl(`/api/sessions/${encodeURIComponent(conversationId)}/skills/${skillPath}`),
        { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } },
      )
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json() as { selected_skill_ids: string[] }
      useSessionStore.getState().setSelectedSkillIds(conversationId, data.selected_skill_ids)
    })
  }, [activeConvId, queueSessionMutation, token])

  const handleAbilityPick = useCallback((pick: AbilityPick) => {
    consumeTrigger()
    void handleSkillToggle(pick.name)
    editorRef.current?.focus()
  }, [consumeTrigger, editorRef, handleSkillToggle])

  // 技能面板保持展开以便多选；仅在服务端确认后更新 chip。
  // 技能弹窗只列「技能」本体：专家、专家团自带的技能不在这里出现。
  // 判定用包级语义标记 expert_type（agent/team），纯技能包为 null。
  // 旧后端不返回该字段时（undefined → null）不做过滤，避免整表被清空。
  const pickerSkills = useMemo(
    () => skills.filter((skill) => !skill.expertType),
    [skills],
  )

  const handleSkillPanelPick = useCallback((skillId: string) => {
    setPlusMenuOpen(false)
    void handleSkillToggle(skillId)
  }, [handleSkillToggle])
  const handleContextToggle = useCallback((ref: ContextRef) => {
    const editor = editorRef.current
    if (!editor) return
    if (fileChips.some((c) => c.value === ref.path)) {
      editor.removeChip('file', ref.path)
      return
    }
    if (fileChips.length >= MAX_CONTEXT_REFS) return
    editor.insertChip({ kind: 'file', value: ref.path })
  }, [fileChips, editorRef])

  const handleContextPick = useCallback((ref: ContextRef) => {
    // 必须先吃掉 `@query` 再插卡片：反序会让 deleteBeforeCaret 的删除区间
    // （从触发串起点到插入后的光标处）跨过刚插入的卡片，把它整块删掉。
    consumeTrigger()
    handleContextToggle(ref)
    // 面板即将随触发串消失，焦点交回编辑器，用户可以接着输入
    editorRef.current?.focus()
  }, [consumeTrigger, handleContextToggle, editorRef])

  // 发送后让输入框失焦（移动端同时收起键盘），避免发送完仍停在输入框里。
  // 触发面板未选中就直接发送时，先把未完成的触发串删掉，避免把 `/xxx`、`@xxx` 当正文发出去。
  const handleSubmit = useCallback(() => {
    const editor = editorRef.current
    const text = trigger && editor ? editor.deleteBeforeCaret(trigger.length) : (editor?.getValue() ?? input)
    onSubmit(text)
    editor?.blur()
  }, [trigger, input, onSubmit, editorRef])

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLDivElement>): boolean => {
    if (!trigger) return false
    if (e.key === 'Escape') {
      dismissTrigger()
      return true
    }
    const menu = trigger.kind === 'slash' ? abilityRef.current : contextRefRef.current
    return menu?.handleKeyDown(e) ?? false
  }, [trigger, dismissTrigger])

  const anySubPanelOpen = plusMenuOpen || skillPanelOpen || pluginPanelOpen || contextFlyoutOpen
  // 输入栏图标按钮悬浮提示：复用项目既有 portal tooltip（.tooltip-bubble），
  // 规避原生 title 的浏览器级延迟（~0.5–1.5s）与 transform 祖先导致的定位偏移。
  const { show: showTip, hide: hideTip, render: tipEl } = useItemTooltip()

  return (
    <form ref={formRef} onSubmit={(e) => { e.preventDefault(); if (isUploading) return; handleSubmit() }}
      onDragEnter={onDragEnter} onDragLeave={onDragLeave} onDragOver={onDragOver} onDrop={onDrop}
      className={`relative flex flex-col input-surface transition-all ${isLanding ? 'duration-300' : ''} ${isDragging ? 'is-dragging' : ''} ${isClarifying ? 'min-h-[320px]' : ''}`}>

      {/* 澄清期间：children（ClarifyCard）占满全部空间，隐藏附件 + 编辑器 + toolbar */}
      {isClarifying ? (
        <div className="flex-1 min-h-0 flex flex-col">{children}</div>
      ) : (
        <>
          {pendingAttachments.length > 0 && (
            <div className={`flex flex-wrap gap-2 shrink-0 ${attachmentPadding}`}>
              {pendingAttachments.map((att, i) => (
                att.kind === 'ref' ? (
                  <div key={att.id} className="relative group h-16 w-[200px] max-w-full shrink-0">
                    <div
                      role="button"
                      tabIndex={0}
                      onClick={() => onPreviewAttachment(att)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          onPreviewAttachment(att)
                        }
                      }}
                      className="flex h-full w-full cursor-pointer items-center gap-2.5 overflow-hidden
                       rounded-[var(--radius-md)] border border-[var(--c-border)] bg-[var(--c-surface-2)]/40
                       pl-2.5 pr-4 transition-colors hover:border-[var(--c-accent)]/60"
                    >
                      <FileIcon filename={att.name} size={24} className="shrink-0" />
                      <div className="min-w-0 flex-1">
                        <div
                          onMouseEnter={(e) => showTip(e, att.name, true, 'above')}
                          onMouseLeave={hideTip}
                          className="truncate text-[length:var(--fs-sm)] font-medium text-[var(--c-text)]"
                        >
                          {att.name}
                        </div>
                        <div className="truncate text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]/80">
                          {(att.name.split('.').pop() || 'FILE').toUpperCase()}·{formatBytes(att.sizeBytes ?? 0)}
                        </div>
                      </div>
                    </div>
                    <button
                      type="button"
                      aria-label="移除附件"
                      onClick={() => { hideTip(); onRemoveAttachment(i) }}
                      className="absolute z-10 -right-1.5 -top-1.5 grid h-5 w-5 place-items-center rounded-full
                       bg-[#5b6673] text-white opacity-0 shadow-[0_1px_3px_rgba(0,0,0,0.28)] transition-all
                       hover:bg-[#49525e] cursor-pointer scale-90 pointer-events-none group-hover:scale-100
                       group-hover:opacity-100 group-hover:pointer-events-auto focus-visible:scale-100
                       focus-visible:opacity-100 focus-visible:pointer-events-auto"
                    >
                      <X size={11} strokeWidth={2.5} />
                    </button>
                  </div>
                ) : (
                  <AttachmentCard
                    key={att.id}
                    file={att.file}
                    imageSrc={att.kind === 'image' ? imageBlockToDataUrl(att.block) : undefined}
                    upload={att.upload}
                    onPreview={() => onPreviewAttachment(att)}
                    onRemove={() => { hideTip(); onRemoveAttachment(i) }}
                    onTitleEnter={(e) => showTip(e, att.file.name, true, 'above')}
                    onTitleLeave={hideTip}
                  />
                )
              ))}
            </div>
          )}

          {children}

          <RichInput
            ref={editorRef}
            value={input}
            onChange={onInputChange}
            onSubmit={handleSubmit}
            onCaretChange={handleCaretChange}
            onKeyDown={handleKeyDown}
            onPasteFiles={onPasteFiles}
            onChipRemove={(chip) => { void handleChipRemove(chip) }}
            placeholder={placeholder}
            className={`block w-full ${editorPadding} bg-transparent text-[var(--c-text)] text-[length:var(--fs-md)] resize-none outline-none ${textareaFocusClass} leading-relaxed`}
          />

          <input ref={fileInputRef} type="file" onChange={handleFilePickerChange} className="hidden" />

          <div className={`flex items-center justify-between ${toolbarPadClass} pt-1 pb-3`}>
          {abilitiesEnabled && (
            <div className="relative flex items-center" ref={toolbarRef} onMouseEnter={cancelCollapse} onMouseLeave={scheduleCollapse}>
            <button type="button" onClick={() => { dismissTrigger(); setPlusMenuOpen((p) => !p); setSkillPanelOpen(false); setPluginPanelOpen(false); setContextFlyoutOpen(false) }} disabled={isUploading || isStreaming || isClarifying}
              className="btn btn-circle btn-ghost"
              data-state={anySubPanelOpen ? 'accent' : undefined}
              aria-label="添加" onMouseEnter={(e) => showTip(e, '添加', true)} onMouseLeave={hideTip}><Plus size={18} strokeWidth={2} /></button>
            {expertState.expert_id && (
              <div
                data-expert-pill
                className={`group ml-1 flex max-w-[220px] min-w-0 items-center rounded-full py-1 pl-1 pr-2.5
                 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)] transition-colors
                 hover:bg-[var(--c-surface-2)]${expertUnavailableText ? ' opacity-50' : ''}`}
                onMouseEnter={(e) => showTip(e, expertUnavailableText ?? expertName, true, 'above', 'dark')}
                onMouseLeave={hideTip}
                data-unavailable={expertUnavailableText ? '' : undefined}
              >
                <span className="relative grid h-5 w-5 shrink-0 place-items-center">
                  <span className="grid h-full w-full place-items-center overflow-hidden rounded-full
                   bg-[var(--c-accent)] text-[length:var(--fs-xs)] font-semibold text-[var(--c-btn-primary-fg)]
                   transition-opacity group-hover:opacity-0">
                    {selectedExpert?.avatarUrl
                      ? <img src={authedUrl(selectedExpert.avatarUrl, token)} alt="" className="h-full w-full object-cover" />
                      : monogram(expertName)}
                  </span>
                  <button
                    type="button"
                    aria-label={`取消专家 ${expertName}`}
                    onClick={() => { void handleExpertCancel() }}
                    className="absolute inset-0 grid place-items-center rounded-full opacity-0
                     transition-opacity hover:bg-[var(--c-surface-3)] hover:text-[var(--c-text)]
                     focus-visible:opacity-100 group-hover:opacity-100"
                  >
                    <X size={12} strokeWidth={2} />
                  </button>
                </span>
                <button
                  type="button"
                  onClick={() => { setPlusMenuOpen(true); handlePlusAction('expert') }}
                  className="ml-1.5 min-w-0 truncate text-left text-[var(--c-text)]"
                >
                  {expertName}
                </button>
              </div>
            )}
            <PlusMenu
              open={plusMenuOpen}
              onAction={handlePlusAction}
              onClose={() => setPlusMenuOpen(false)}
              // 悬浮到别的项时立即收起已展开的飞出面板，避免「鼠标已经移到引用文件、专家面板还挂着」
              onHoverItem={(action) => {
                if (action !== 'expert') setPluginPanelOpen(false)
                if (action !== 'skill') setSkillPanelOpen(false)
                setContextFlyoutOpen(false)
              }}
            />
            <FileLibraryModal
              open={fileLibraryOpen}
              onClose={() => setFileLibraryOpen(false)}
              workspaceId={workspaceId}
              token={token}
              sessionId={activeConvId}
              maxSelect={maxRefFiles}
              onConfirm={(items) => onAddRefs?.(items)}
            />
            <SkillSelector open={skillPanelOpen} onClose={() => setSkillPanelOpen(false)} selected={selectedSkillIds} onChange={handleSkillPanelPick} skills={pickerSkills} loading={skillsLoading} />
            <PluginSelector open={pluginPanelOpen} onClose={() => setPluginPanelOpen(false)} selected={expertState.expert_id} onChange={handleExpertPanelPick} experts={experts} loading={expertsLoading} token={token} />
            <ContextPicker
              open={contextFlyoutOpen}
              onClose={() => setContextFlyoutOpen(false)}
              workspaceId={workspaceId}
              token={token}
              messages={conversationMessages}
              selectedPaths={fileChips.map((c) => c.value)}
              maxSelected={MAX_CONTEXT_REFS}
              onToggle={handleContextToggle}
              position="flyout"
            />
            {trigger?.kind === 'slash' && (
              <AbilityMenu
                ref={abilityRef}
                query={trigger.query}
                selected={selectedSkillIds.map((name) => ({ kind: 'skill' as const, name }))}
                skills={skills}
                loading={skillsLoading}
                onPick={handleAbilityPick}
              />
            )}
            {trigger?.kind === 'at' && (
              <ContextPicker
                ref={contextRefRef}
                open
                query={trigger.query}
                onClose={dismissTrigger}
                workspaceId={workspaceId}
                token={token}
                messages={conversationMessages}
                selectedPaths={fileChips.map((c) => c.value)}
                maxSelected={MAX_CONTEXT_REFS}
                onToggle={handleContextPick}
              />
            )}
          </div>
          )}

        <div className="flex items-center gap-2">
          <button type="button" onClick={() => { if (isUploading || !input.trim() || isOptimizing || chips.length > 0) return; onOptimizePrompt() }}
            disabled={isUploading || isOptimizing || !input.trim() || chips.length > 0}
            className="btn btn-circle btn-ghost"
            aria-label="优化输入" onMouseEnter={(e) => showTip(e, '优化输入', true)} onMouseLeave={hideTip}>
            {isOptimizing ? <LoaderCircle size={18} strokeWidth={2} className="animate-spin" /> : <Sparkles size={18} strokeWidth={2} />}
          </button>
          <button type="button" onClick={() => onToggleVoice()}
            className="btn btn-circle btn-ghost"
            data-state={isRecording ? 'error' : isTranscribing ? 'accent' : undefined}
            aria-label={isRecording ? '\u505C\u6B62\u5F55\u97F3' : isTranscribing ? '\u8BC6\u522B\u4E2D\u2026' : '\u8BED\u97F3\u8F93\u5165'}
            onMouseEnter={(e) => showTip(e, isRecording ? '\u505C\u6B62\u5F55\u97F3' : isTranscribing ? '\u8BC6\u522B\u4E2D\u2026' : '\u8BED\u97F3\u8F93\u5165', true)} onMouseLeave={hideTip}>
            {isTranscribing ? <LoaderCircle size={18} strokeWidth={2} className="animate-spin" /> : isRecording ? <Pause size={18} strokeWidth={2} fill="currentColor" /> : <Mic size={18} strokeWidth={2} />}
          </button>
          {isStreaming && (
            <button type="button" onClick={() => { if (isUploading) return; onAbort() }} disabled={isUploading}
              className="btn btn-circle btn-stop"
              aria-label="停止" onMouseEnter={(e) => showTip(e, '停止', true)} onMouseLeave={hideTip}><Square size={14} fill="currentColor" /></button>
          )}
          <button type="button" onClick={() => { if (isUploading) return; handleSubmit() }} disabled={!canSubmit}
            // 发送键：无内容时灰色（disabled 自带 40% 透明），有内容时主色实心 + 白色图标
            className={`btn btn-circle ${canSubmit ? 'btn-primary' : 'bg-transparent text-[var(--c-text-secondary)]'}`}
            aria-label="发送" onMouseEnter={(e) => showTip(e, '发送', true)} onMouseLeave={hideTip}>
            <span
              aria-hidden="true"
              className="w-[18px] h-[18px] bg-current"
              style={{
                WebkitMaskImage: `url("${iconSend}")`,
                maskImage: `url("${iconSend}")`,
                WebkitMaskRepeat: 'no-repeat',
                maskRepeat: 'no-repeat',
                WebkitMaskPosition: 'center',
                maskPosition: 'center',
                WebkitMaskSize: 'contain',
                maskSize: 'contain',
              }}
            />
          </button>
        </div>
      </div>
        </>
      )}

      {isDragging && (isLanding ? (
        <div className="absolute inset-0 rounded-[var(--radius-card)] bg-[var(--c-accent)]/10 backdrop-blur-[2px] grid place-items-center pointer-events-none z-20">
          <div className="flex flex-col items-center gap-2">
            <div className="w-12 h-12 rounded-[var(--radius-card)] bg-[var(--c-btn-primary-bg)] text-[var(--c-btn-primary-fg)] grid place-items-center shadow-lg animate-bounce"><Plus size={24} /></div>
            <span className="text-[length:var(--fs-md)] font-semibold text-[var(--c-accent)]">松开以添加附件</span>
          </div>
        </div>
      ) : (
        <div className="absolute inset-0 rounded-[var(--radius-card)] bg-[var(--c-accent)]/10 grid place-items-center pointer-events-none">
          <span className="text-[length:var(--fs-md)] font-medium text-[var(--c-accent)]">松开以添加附件</span>
        </div>
      ))}
      {tipEl}
    </form>
  )
}
