/**
 * 咨询聊天区域 —— 与宿主 ChatArea normal mode 布局完全一致
 *
 * 结构对齐宿主 ChatArea：
 *   外层 .chat-area + .chat-scroller + 底部 px-4 py-4 md:px-8 容器
 *   消息区和输入区共享 max-w-[1000px] mx-auto 居中
 *
 * 渲染层：MessageBubble + ChatInputBox + useChatScrollController（零 store/context）
 * 数据层：useConsultationChat
 * 映射层：ConsultMessage → Message
 *
 * 域特有：患者上下文条（顶部），输入框下方免责声明
 * 域禁用：附件/语音/技能/插件/引用文件/优化提示词 → noop/空值
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowDown, Loader2 } from 'lucide-react'
import MessageBubble, { extractQuestionPlainText } from '@/components/chat/MessageBubble'
import ChatInputBox from '@/components/chat/ChatInputBox'
import type { RichInputHandle } from '@/components/chat/RichInput'
import { useChatScrollController } from '@/hooks/chat/useChatScroll'
import type { Message } from '@/types/chat'
import { useConsultationChat } from './useConsultationChat'
import type { ConsultMessage } from './useConsultationChat'
import type { PatientProfile } from '../../types/view'
import { createConsultation } from '../../api/client'

// ── 常量 ──

const STREAMING_ID = '__streaming__'

/** 肿瘤患者常见咨询快捷问题 */
const QUICK_QUESTIONS = [
  '当前治疗方案是否需要调整？',
  '最新检查指标有什么变化？',
  '这个分期的预后如何？',
  '有哪些可选的替代治疗方案？',
  '药物不良反应怎么处理？',
  '需要做哪些随访检查？',
  '患者是否适合质子治疗？',
]

// ── ConsultMessage → Message 映射 ──

function consultToMessage(msg: ConsultMessage): Message {
  return {
    id: msg.id,
    role: msg.role,
    content: msg.content,
    timestamp: msg.timestamp,
  }
}

// ── Props ──

interface MedicalChatAreaProps {
  patient: PatientProfile
  initialSessionId?: string
}

// ── 组件 ──

export function MedicalChatArea({ patient, initialSessionId }: MedicalChatAreaProps) {
  const chat = useConsultationChat()
  const scrollerRef = useRef<HTMLDivElement>(null)
  const editorRef = useRef<RichInputHandle>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const consultationCreatedRef = useRef(false)

  // ── 输入状态（ChatInputBox 需要） ──

  const [input, setInput] = useState('')
  const [isDragging, setIsDragging] = useState(false)

  // ── 数据映射 ──

  const messages: Message[] = useMemo(
    () => chat.messages.map(consultToMessage),
    [chat.messages],
  )

  const items: Message[] = useMemo(() => {
    if (!chat.isStreaming || !chat.streamingText) return messages
    const streaming: Message = {
      id: STREAMING_ID,
      role: 'assistant',
      content: chat.streamingText,
      timestamp: Date.now(),
    }
    return [...messages, streaming]
  }, [messages, chat.isStreaming, chat.streamingText])

  const latestAssistantMessageId = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i]?.role === 'assistant') return messages[i].id
    }
    return null
  }, [messages])

  // ── 滚动控制 ──

  const activityKey = `${items.length}-${chat.streamingText.length}`
  const scroll = useChatScrollController({
    scrollerRef,
    isStreaming: chat.isStreaming,
    activityKey,
    resetKey: chat.activeConvId ?? undefined,
  })

  useEffect(() => {
    if (scroll.mode === 'bottom' && scrollerRef.current) {
      scrollerRef.current.scrollTop = scrollerRef.current.scrollHeight
    }
  }, [scroll.mode, items.length])

  // ── 首次会话设置 ──

  useEffect(() => {
    if (initialSessionId) {
      chat.setActiveConv(initialSessionId)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSessionId])

  // ── 发送消息 ──
  // 时序关键：必须先建会话 → 域登记 → 再发消息
  // 否则 SESSION_START hook 在咨询记录写入前触发，AI 无患者上下文

  const handleSubmit = useCallback(async () => {
    const text = input.trim()
    if (!text) return
    if (chat.isStreaming) return

    setInput('')

    // 1) 首次发消息：先建核心会话（不发 WS 帧）
    let sessionId = chat.activeConvId
    if (!sessionId && !consultationCreatedRef.current) {
      sessionId = await chat.ensureSession(`${patient.name} 咨询`)
    }

    // 2) 首次建会话：域登记（SESSION_START hook 需要这条记录）
    if (sessionId && !consultationCreatedRef.current) {
      consultationCreatedRef.current = true
      try {
        await createConsultation({
          sessionId,
          patientId: patient.id,
          title: `${patient.name} 咨询`,
        })
      } catch (err) {
        // 域登记失败 → 提示但不阻断聊天
        console.error('[MedicalChatArea] 咨询登记失败:', err)
      }
    }

    // 3) 发消息（已有 sessionId，sendMessage 内部不会重复建会话）
    await chat.sendMessage(text, sessionId ?? undefined)
  }, [input, chat, patient.id, patient.name])

  // ── 复制回调 ──

  const handleCopy = useCallback(async (msg: Message) => {
    const text = typeof msg.content === 'string' ? msg.content : ''
    await navigator.clipboard?.writeText(text)
  }, [])

  // ── ChatInputBox noop ──

  const noop = useCallback(() => {}, [])
  const noopChange = useCallback((_e: React.ChangeEvent<HTMLInputElement>) => {}, [])
  const noopFiles = useCallback((_files: File[]) => {}, [])

  // ── 渲染：对齐宿主 ChatArea normal mode 布局 ──

  return (
    <div className="chat-area flex flex-col flex-1 min-w-0 min-h-0 relative">
      {/* 消息滚动区 —— 对齐宿主 chat-scroller */}
      <div
        ref={scrollerRef}
        onScroll={scroll.handleScroll}
        className="chat-scroller flex-1 overflow-y-auto px-4 py-6 md:px-8"
      >
        <div className="max-w-[1000px] mx-auto w-full min-h-full flex flex-col">
          {items.length === 0 && !chat.streamingText && (
            <div className="flex-1 flex flex-col items-center justify-center px-2">
              <p className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)] mb-4">常见问题</p>
              <div className="flex flex-wrap gap-2 justify-center max-w-[480px]">
                {QUICK_QUESTIONS.map((q) => (
                  <button
                    key={q}
                    type="button"
                    onClick={() => void chat.sendMessage(q)}
                    className="px-3 py-1.5 rounded-lg bg-[var(--c-surface-2)] text-[length:var(--fs-sm)] text-[var(--c-text-secondary)] hover:bg-[var(--c-accent-soft)] hover:text-[var(--c-accent)] transition-colors cursor-pointer"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}

          {items.map((item, idx) => {
            const isStreamingItem = item.id === STREAMING_ID
            const showSupplementalSections =
              !isStreamingItem &&
              item.role === 'assistant' &&
              item.id === latestAssistantMessageId &&
              item.id === messages[messages.length - 1]?.id
            const questionText = (() => {
              if (item.role !== 'assistant') return null
              const prev = items[idx - 1]
              if (!prev || prev.role !== 'user') return null
              return extractQuestionPlainText(prev.content)
            })()

            return (
              <MessageBubble
                key={item.id}
                message={item}
                isStreaming={isStreamingItem}
                showSupplementalSections={showSupplementalSections}
                questionText={questionText}
                onCopy={handleCopy}
                onRetry={
                  item.role === 'error'
                    ? () => chat.sendMessage('')
                    : undefined
                }
              />
            )
          })}

          {/* pinned/reading 留白 */}
          {scroll.mode !== 'bottom' && (
            <div aria-hidden style={{ height: scroll.spacerHeight }} />
          )}
        </div>
      </div>

      {/* 底部输入区 —— 对齐宿主 px-4 py-4 md:px-8 + max-w 居中 */}
      <div className="shrink-0 px-4 py-4 md:px-8">
        <div className="max-w-[1000px] mx-auto w-full">
          {/* 回到底部按钮 —— 对齐宿主样式 */}
          {scroll.contentOverflow && !scroll.atBottom && (
            <div className="mb-3 flex justify-center">
              <button
                type="button"
                onClick={scroll.scrollToBottom}
                className="flex items-center gap-2 rounded-full border border-[var(--c-border)]/60
                           bg-[var(--c-surface)]/80 backdrop-blur-sm
                           px-4 py-2 text-[length:var(--fs-sm)] font-medium
                           text-[var(--c-text-secondary)] shadow-[var(--shadow-sm)]
                           hover:bg-[var(--c-surface-2)] hover:text-[var(--c-text)] hover:border-[var(--c-border)]
                           active:scale-95
                           transition-all cursor-pointer"
                title={chat.isStreaming ? 'AI 正在处理' : '回到底部'}
                aria-label={chat.isStreaming ? 'AI 正在处理' : '回到底部'}
              >
                {chat.isStreaming ? (
                  <Loader2 size={14} strokeWidth={2.5} className="block shrink-0 animate-spin" />
                ) : (
                  <ArrowDown size={14} strokeWidth={2.5} className="block shrink-0" />
                )}
                <span>{chat.isStreaming ? 'AI 正在处理' : '回到底部'}</span>
              </button>
            </div>
          )}

          {/* 错误提示条 */}
          {chat.lastError && (
            <div className="mb-3 p-3 rounded-xl border border-[var(--c-danger)] bg-[var(--c-danger-soft)]">
              <p className="text-[length:var(--fs-sm)] text-[var(--c-danger)]">{chat.lastError}</p>
            </div>
          )}

          <ChatInputBox
            variant="normal"
            input={input}
            isUploading={false}
            isOptimizing={false}
            isRecording={false}
            isTranscribing={false}
            isStreaming={chat.isStreaming}
            isClarifying={false}
            isDragging={isDragging}
            pendingAttachments={[]}
            editorRef={editorRef}
            fileInputRef={fileInputRef}
            onInputChange={setInput}
            onSubmit={() => void handleSubmit()}
            onFilePicked={noopChange}
            onPasteFiles={noopFiles}
            onDragEnter={(e) => { e.preventDefault(); setIsDragging(true) }}
            onDragLeave={() => setIsDragging(false)}
            onDragOver={(e) => { if (Array.from(e.dataTransfer?.types ?? []).includes('Files')) e.preventDefault() }}
            onDrop={(e) => { e.preventDefault(); setIsDragging(false) }}
            onOptimizePrompt={noop}
            onToggleVoice={noop}
            onAbort={chat.abort}
            onRemoveAttachment={noop}
            onPreviewAttachment={noop}
            placeholder={`${patient.name} 的问题…`}
            conversationMessages={messages}
            workspaceId=""
            token={null}
            abilitiesEnabled={false}
          />

          {/* 免责声明 —— 对齐宿主样式 */}
          <div className="mt-1.5 text-[length:var(--fs-xs)] text-[var(--c-text-secondary)] text-center opacity-60">
            内容由 AI 生成，请仔细核查
          </div>
        </div>
      </div>
    </div>
  )
}
