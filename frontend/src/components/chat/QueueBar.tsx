import { useEffect, useRef, useState } from 'react'
import { ArrowUp, ChevronDown, Clock, X } from 'lucide-react'
import type { ConversationController } from '../../hooks/chat/useConversationController'

/**
 * 队列任务条（纯渲染 + 自身 UI 态）。
 * 折叠态 / 操作提示（queueTip）是这条 bar 的私有 UI 状态，不外泄；
 * 队列数据（waitingUserMessages/isQueued/queuePosition/isStreaming）由 ChatArea
 * 从 store 派生后传入，执行/删除动作统一走 controller。
 */
interface Props {
  controller: ConversationController
  waitingUserMessages: { id: string; text: string }[]
  isQueued: boolean
  queuePosition: number
  isStreaming: boolean
}

export default function QueueBar({
  controller,
  waitingUserMessages,
  isQueued,
  queuePosition,
  isStreaming,
}: Props) {
  const [collapsed, setCollapsed] = useState(false)
  const [tip, setTip] = useState<{ message: string; tone: 'success' | 'error' } | null>(null)
  const tipTimerRef = useRef<number | null>(null)

  useEffect(() => {
    return () => {
      if (tipTimerRef.current !== null) {
        window.clearTimeout(tipTimerRef.current)
        tipTimerRef.current = null
      }
    }
  }, [])

  // runQueued/deleteQueued 返回 {message, tone} 时在标题栏短暂提示（1.2s 自动消失）。
  const showTip = (res: unknown) => {
    if (!res || typeof res !== 'object') return
    if (!('message' in res)) return
    const message = String((res as { message?: unknown }).message ?? '').trim()
    const tone = ((res as { tone?: unknown }).tone as 'success' | 'error') ?? 'success'
    if (!message) return
    setTip({ message, tone })
    if (tipTimerRef.current !== null) {
      window.clearTimeout(tipTimerRef.current)
    }
    tipTimerRef.current = window.setTimeout(() => {
      setTip(null)
      tipTimerRef.current = null
    }, 1200)
  }

  if (waitingUserMessages.length === 0) return null

  return (
    <div className="overflow-hidden rounded-t-xl border border-[var(--c-border)]/60 bg-[var(--c-surface)]/72 backdrop-blur-sm">
      <button
        type="button"
        onClick={() => setCollapsed((v) => !v)}
        className="flex w-full items-center gap-2 px-4 py-2.5 text-left transition-colors hover:bg-[var(--c-surface-2)]/20 cursor-pointer"
        aria-expanded={!collapsed}
      >
        <ChevronDown
          size={14}
          className={`shrink-0 text-[var(--c-text-secondary)]/80 transition-transform ${collapsed ? '-rotate-90' : ''}`}
        />
        <span className="text-[length:var(--fs-sm)] font-medium text-[var(--c-text)]">
          队列任务
        </span>
        {tip ? (
          <span
            className={`ml-2 text-[length:var(--fs-sm)] ${
              tip.tone === 'error' ? 'text-[var(--c-error)]' : 'text-[var(--c-text-secondary)]/70'
            }`}
          >
            {tip.message}
          </span>
        ) : null}
        {isQueued ? (
          <span className="ml-auto inline-flex items-center gap-1.5 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/70">
            <Clock size={11} className="shrink-0" />
            <span>等待执行{queuePosition > 0 ? `（${queuePosition}）` : ''}</span>
          </span>
        ) : null}
      </button>

      {!collapsed && (
        <div className="max-h-40 overflow-y-auto border-t border-[var(--c-border)]/50">
          {waitingUserMessages.map((m, index) => (
            <div
              key={m.id}
              className={`flex items-start gap-3 px-4 py-2.5 ${index > 0 ? 'border-t border-[var(--c-border)]/35' : ''}`}
            >
              <div className="min-w-0 flex-1">
                <div className="whitespace-pre-wrap break-words text-[length:var(--fs-sm)] leading-6 text-[var(--c-text)]/88">
                  {m.text}
                </div>
              </div>

              <div className="flex items-center gap-1 shrink-0 pt-0.5">
                <button
                  type="button"
                  onClick={() => showTip(controller.runQueued(m.id))}
                  className="btn btn-icon btn-ghost"
                  aria-label={isStreaming ? '设为下一项执行' : '立即执行'}
                  title={isStreaming ? '设为下一项执行' : '立即执行'}
                >
                  <ArrowUp size={16} />
                </button>
                <button
                  type="button"
                  onClick={() => showTip(controller.deleteQueued(m.id))}
                  className="btn btn-icon btn-ghost"
                  aria-label="删除"
                  title="删除"
                >
                  <X size={16} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
