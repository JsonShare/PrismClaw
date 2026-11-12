import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Square, ArrowUp, Plus, X, LoaderCircle, Sparkles, Mic, Pause,
} from 'lucide-react'
import type { PendingAttachment, PendingUpload } from '../../lib/chat/attachments'
import { imageBlockToDataUrl } from '../../lib/file/image'
import FileIcon from '../file/FileIcon'
import PlusMenu from './PlusMenu'
import EffortToggle from './EffortToggle'
import type { PlusMenuAction } from './PlusMenu'
import SkillSelector from './SkillSelector'
import PluginSelector from './PluginSelector'
import AbilityMenu from './AbilityMenu'
import type { AbilityMenuHandle, AbilityPick } from './AbilityMenu'
import ContextPicker from './ContextPicker'
import type { ContextPickerHandle } from './ContextPicker'
import { useItemTooltip } from './useItemTooltip'
import { useSkillList } from '../../hooks/chat/useSkillList'
import type { ContextRef } from '../../lib/chat/contextRefs'
import type { Message } from '../../types/chat'
import RichInput from './RichInput'
import type { RichInputHandle } from './RichInput'
import { collectChips, designationOf, hasPlainText } from '../../lib/chat/inputMarkers'
import { parseTrigger } from '../../lib/chat/inputTrigger'

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
  variant: ChatInputBoxVariant; placeholder: string
  conversationMessages: Message[]
  workspaceId: string; token: string | null
  /**
   * 是否启用「技能 / 专家 / 引用文件 / 附件」这一整套能力入口（➕ 菜单 + `/`、`@` 触发）。
   * 默认 true；没有这些能力的宿主（如医疗咨询域）显式关掉，避免出现点了没反应的死控件。
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
  onAbort, onRemoveAttachment, onPreviewAttachment, variant, placeholder,
  conversationMessages, workspaceId, token,
  abilitiesEnabled = true, children,
}: ChatInputBoxProps) {
  const isLanding = variant === 'landing'
  const [plusMenuOpen, setPlusMenuOpen] = useState(false)
  const [skillPanelOpen, setSkillPanelOpen] = useState(false)
  const [pluginPanelOpen, setPluginPanelOpen] = useState(false)
  const [contextFlyoutOpen, setContextFlyoutOpen] = useState(false)
  const toolbarRef = useRef<HTMLDivElement>(null)
  const formRef = useRef<HTMLFormElement>(null)
  // 悬浮离开工具栏后延迟收起子面板的定时器（hover-intent，避免「粘滞」）
  const collapseTimer = useRef<number | null>(null)
  const abilityRef = useRef<AbilityMenuHandle>(null)
  const contextRefRef = useRef<ContextPickerHandle>(null)

  // 技能与专家同源：整页只取一次，`/` 面板、技能面板、专家面板共用
  const { skills, loading: skillsLoading } = useSkillList()

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

  // ── 正文里的卡片是技能/专家/引用文件的唯一真相源 ──
  const chips = collectChips(input)
  const designation = designationOf(input)
  const fileChips = chips.filter((c) => c.kind === 'file')

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

  const handlePlusAction = useCallback((action: PlusMenuAction) => {
    // 打开一个子面板时关闭其他子面板（不关闭主菜单，改用右侧飞出）
    dismissTrigger()
    setSkillPanelOpen(false)
    setPluginPanelOpen(false)
    setContextFlyoutOpen(false)
    switch (action) {
      case 'add_file': fileInputRef.current?.click(); break
      case 'skill': setSkillPanelOpen(true); break
      case 'plugin': setPluginPanelOpen(true); break
      case 'context': setContextFlyoutOpen(true); break
    }
  }, [fileInputRef, dismissTrigger])

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

  // 技能与专家可以同时选多个（后端逐个激活），选中是叠加而不是替换。
  const handleAbilityPick = useCallback((pick: AbilityPick) => {
    const current = pick.kind === 'skill' ? designation.skills : designation.plugins
    consumeTrigger()
    if (current.includes(pick.name)) {
      editorRef.current?.removeChip(pick.kind, pick.name)
      return
    }
    editorRef.current?.insertChip({ kind: pick.kind, value: pick.name })
    editorRef.current?.focus()
  }, [designation.skills, designation.plugins, consumeTrigger, editorRef])

  // 面板是单选：点一项就插入并关闭，靠再次打开面板累积多张卡片；
  // 点已选中的项视为取消该卡片。
  const handleSkillPanelPick = useCallback((name: string) => {
    if (designation.skills.includes(name)) {
      editorRef.current?.removeChip('skill', name)
      return
    }
    editorRef.current?.insertChip({ kind: 'skill', value: name })
    setPlusMenuOpen(false)
  }, [designation.skills, editorRef])

  const handlePluginPanelPick = useCallback((name: string) => {
    if (designation.plugins.includes(name)) {
      editorRef.current?.removeChip('plugin', name)
      return
    }
    editorRef.current?.insertChip({ kind: 'plugin', value: name })
    setPlusMenuOpen(false)
  }, [designation.plugins, editorRef])

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
            placeholder={placeholder}
            className={`block w-full ${editorPadding} bg-transparent text-[var(--c-text)] text-[length:var(--fs-md)] resize-none outline-none ${textareaFocusClass} leading-relaxed`}
          />

          <input ref={fileInputRef} type="file" onChange={handleFilePickerChange} className="hidden" />

          <div className={`flex items-center justify-between ${toolbarPadClass} pt-1 pb-3`}>
          {abilitiesEnabled && (
            <div className="relative" ref={toolbarRef} onMouseEnter={cancelCollapse} onMouseLeave={scheduleCollapse}>
            <button type="button" onClick={() => { dismissTrigger(); setPlusMenuOpen((p) => !p); setSkillPanelOpen(false); setPluginPanelOpen(false); setContextFlyoutOpen(false) }} disabled={isUploading || isStreaming || isClarifying}
              className="btn btn-circle btn-ghost"
              data-state={anySubPanelOpen ? 'accent' : undefined}
              aria-label="添加" onMouseEnter={(e) => showTip(e, '添加', true)} onMouseLeave={hideTip}><Plus size={18} strokeWidth={2} /></button>
            <PlusMenu open={plusMenuOpen} onOpenChange={setPlusMenuOpen} onAction={handlePlusAction} />
            <EffortToggle />
            <SkillSelector open={skillPanelOpen} onClose={() => setSkillPanelOpen(false)} selected={designation.skills} onChange={handleSkillPanelPick} skills={skills} loading={skillsLoading} />
            <PluginSelector open={pluginPanelOpen} onClose={() => setPluginPanelOpen(false)} selected={designation.plugins} onChange={handlePluginPanelPick} skills={skills} loading={skillsLoading} />
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
                selected={[
                  ...designation.skills.map((name) => ({ kind: 'skill' as const, name })),
                  ...designation.plugins.map((name) => ({ kind: 'plugin' as const, name })),
                ]}
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
          <button type="button" onClick={() => { if (isUploading) return; handleSubmit() }} disabled={isUploading || isClarifying || !canSend}
            className="btn btn-circle btn-inverse"
            aria-label="发送" onMouseEnter={(e) => showTip(e, '发送', true)} onMouseLeave={hideTip}><ArrowUp size={16} strokeWidth={2.5} /></button>
        </div>
      </div>
        </>
      )}

      {isDragging && (isLanding ? (
        <div className="absolute inset-0 rounded-[var(--radius-card)] bg-[var(--c-accent)]/10 backdrop-blur-[2px] grid place-items-center pointer-events-none z-20">
          <div className="flex flex-col items-center gap-2">
            <div className="w-12 h-12 rounded-[var(--radius-card)] bg-[var(--c-btn-primary-bg)] text-[var(--c-btn-primary-fg)] grid place-items-center shadow-lg animate-bounce"><Plus size={24} /></div>
            <span className="text-[length:var(--fs-md)] font-bold text-[var(--c-accent)]">松开以添加附件</span>
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
