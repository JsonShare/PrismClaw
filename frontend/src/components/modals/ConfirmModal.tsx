import { useEffect } from 'react'
import { AlertTriangle } from 'lucide-react'

interface Props {
  open: boolean
  title: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  destructive?: boolean
  onConfirm: () => void
  onCancel: () => void
}

export default function ConfirmModal({
  open,
  title,
  message,
  confirmLabel = '确认',
  cancelLabel = '取消',
  destructive = false,
  onConfirm,
  onCancel,
}: Props) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onCancel()
      } else if (e.key === 'Enter') {
        e.preventDefault()
        onConfirm()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onCancel, onConfirm])

  if (!open) return null

  const confirmClass = destructive ? 'btn-danger' : 'btn-primary'

  return (
    <div
      className="overlay-scrim fixed inset-0 z-50 flex items-center justify-center px-4 animate-in"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onCancel()
      }}
    >
      <div className="overlay-panel w-full max-w-sm rounded-[var(--radius-card)] overflow-hidden">
        <div className="px-4 py-3 flex items-start gap-3">
          {destructive && (
            <AlertTriangle size={18} className="text-[var(--c-error)] shrink-0 mt-0.5" />
          )}
          <div className="flex-1 min-w-0">
            <div className="text-[length:var(--fs-md)] font-display font-semibold text-[var(--c-text)] mb-1">
              {title}
            </div>
            <p className="text-[length:var(--fs-md)] text-[var(--c-text-secondary)] break-words">{message}</p>
          </div>
        </div>
        <div className="flex items-center justify-end gap-2 px-3 pb-3">
          <button
            type="button"
            onClick={onCancel}
            className="btn btn-md btn-ghost"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className={`btn btn-md ${confirmClass}`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
