import { useEffect, useState } from 'react'
import { CornerDownLeft, X } from 'lucide-react'

interface Props {
  open: boolean
  title: string
  label?: string
  placeholder?: string
  initialValue?: string
  maxLength?: number
  confirmLabel?: string
  busy?: boolean
  onConfirm: (value: string) => void
  onCancel: () => void
}

/**
 * 单行文本输入弹框。
 * Electron 下 window.prompt 不可用，需要用户输入时统一走这个弹框。
 */
export default function PromptModal({
  open,
  title,
  label,
  placeholder,
  initialValue = '',
  maxLength = 60,
  confirmLabel = '确定',
  busy = false,
  onConfirm,
  onCancel,
}: Props) {
  const [value, setValue] = useState(initialValue)

  // 每次打开都从初始值开始，避免上一次的输入残留。
  useEffect(() => {
    if (open) setValue(initialValue)
  }, [open, initialValue])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onCancel()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onCancel])

  if (!open) return null

  const trimmed = value.trim()
  const submit = () => {
    if (!trimmed || busy) return
    onConfirm(trimmed)
  }

  return (
    <div
      className="overlay-scrim fixed inset-0 z-[120] grid place-items-center px-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onCancel()
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="overlay-panel w-full max-w-[520px] rounded-[16px] px-7 pt-6 pb-6"
      >
        <div className="flex items-center justify-between">
          <h2 className="text-[19px] font-semibold leading-[26px] text-[var(--c-text)]">{title}</h2>
          <button
            type="button"
            onClick={onCancel}
            className="grid h-8 w-8 place-items-center rounded-[8px] text-[var(--c-text-tertiary)] transition-colors hover:bg-[var(--c-hover-bg)] hover:text-[var(--c-text)] cursor-pointer"
            aria-label="关闭"
          >
            <X size={18} />
          </button>
        </div>

        {label ? (
          <label
            htmlFor="prompt-modal-input"
            className="mt-7 block text-[14px] font-medium text-[var(--c-text)]"
          >
            {label}
          </label>
        ) : null}
        <input
          id="prompt-modal-input"
          autoFocus
          value={value}
          maxLength={maxLength}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              submit()
            }
          }}
          placeholder={placeholder}
          className={`${label ? 'mt-2' : 'mt-6'} h-[54px] w-full rounded-[12px] border border-[var(--c-border)] bg-[var(--c-surface-2)] px-4 text-[15px] text-[var(--c-text)] placeholder:text-[var(--c-text-tertiary)] transition-colors hover:border-[var(--c-border-strong)] focus:border-[var(--c-accent)] focus:outline-none focus:ring-2 focus:ring-[var(--c-accent)]/25`}
        />
        <div className="mt-1.5 text-right text-[13px] leading-[18px] text-[var(--c-text-tertiary)]">
          {value.length}/{maxLength}
        </div>

        <div className="mt-6 flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={onCancel}
            className="btn btn-lg btn-secondary"
          >
            取消
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={!trimmed || busy}
            className="btn btn-lg btn-primary"
          >
            {busy ? '处理中…' : confirmLabel}
            <CornerDownLeft size={14} className="opacity-70" />
          </button>
        </div>
      </div>
    </div>
  )
}
