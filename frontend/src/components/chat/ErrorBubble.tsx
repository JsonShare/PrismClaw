import { AlertCircle, RotateCcw } from 'lucide-react'
import type { ContentBlock, Message } from '../../types'

interface Props {
  message: Message
  onRetry?: () => void
}

function contentToText(content: string | ContentBlock[]): string {
  if (typeof content === 'string') return content
  return content
    .filter((b): b is { type: 'text'; text: string } => b.type === 'text')
    .map((b) => b.text)
    .join('\n')
}

export default function ErrorBubble({ message, onRetry }: Props) {
  return (
    <div className="flex w-full mb-6 justify-start">
      <div className="max-w-[80%] flex items-start gap-2 px-4 py-3 rounded-[var(--radius-md)] border border-[var(--c-error)]/40 bg-[var(--c-surface)] text-[length:var(--fs-md)] text-[var(--c-text)]">
        <AlertCircle size={14} className="text-[var(--c-error)] shrink-0 mt-0.5" />
        <div className="min-w-0 flex-1">
          <div className="text-[length:var(--fs-sm)] uppercase tracking-wider text-[var(--c-error)] font-semibold mb-1">
            错误
          </div>
          <div className="break-words leading-relaxed">{contentToText(message.content)}</div>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="btn btn-sm btn-ghost mt-2 -ml-2"
            >
              <RotateCcw size={12} />
              重试
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
