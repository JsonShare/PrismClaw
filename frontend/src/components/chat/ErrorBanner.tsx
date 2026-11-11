import { AlertTriangle, X } from 'lucide-react'

interface Props {
  visible: boolean
  onReconnect: () => void
  onDismiss?: () => void
  details?: string | null
}

export default function ErrorBanner({ visible, onReconnect, onDismiss, details }: Props) {
  if (!visible) return null
  return (
    <div className="absolute top-0 left-0 right-0 z-30 mx-auto max-w-3xl mt-3 px-4">
      <div className="flex items-center gap-2 px-3 py-2 rounded-[var(--radius-md)] border border-[var(--c-error)]/40
                      bg-[var(--c-surface)] text-[length:var(--fs-md)] text-[var(--c-text)]">
        <AlertTriangle size={14} className="text-[var(--c-error)] shrink-0" />
        <div className="flex-1 min-w-0">
          <div>连接已断开，自动重连多次失败。</div>
          {details && (
            <div className="mt-1 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)] whitespace-pre-wrap break-words">
              {details}
            </div>
          )}
        </div>
        <button
          type="button"
          onClick={onReconnect}
          className="btn btn-sm btn-secondary"
        >
          重新连接
        </button>
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="btn btn-icon btn-ghost"
            title="关闭"
          >
            <X size={12} />
          </button>
        )}
      </div>
    </div>
  )
}
