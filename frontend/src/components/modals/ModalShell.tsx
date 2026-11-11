import { type ReactNode } from 'react'
import { X } from 'lucide-react'

export interface ModalShellProps {
  open: boolean
  onClose: () => void
  title: string
  children: ReactNode
  /** 最大宽度 Tailwind class，默认 max-w-xl */
  maxWidth?: string
  /** 是否显示关闭按钮，默认 true */
  showClose?: boolean
  /** 额外的 header 右侧内容 */
  headerRight?: ReactNode
}

/**
 * 统一的模态框外壳，所有弹窗共享相同的 backdrop + 关闭交互模式。
 * 消除 Chat.tsx 中 7 处重复的 backdrop JSX。
 */
export function ModalShell({
  open,
  onClose,
  title,
  children,
  maxWidth = 'max-w-xl',
  showClose = true,
  headerRight,
}: ModalShellProps) {
  if (!open) return null

  return (
    <div
      className="overlay-scrim fixed inset-0 z-50 grid place-items-center px-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        className={`overlay-panel w-full ${maxWidth} rounded-[var(--radius-card)] overflow-hidden`}
      >
        {(title || showClose || headerRight) && (
          <div className="px-5 py-4 flex items-center justify-between">
            <div className="text-[length:var(--fs-lg)] font-semibold text-[var(--c-text)]">{title}</div>
            <div className="flex items-center gap-2">
              {headerRight}
              {showClose && (
                <button
                  type="button"
                  onClick={onClose}
                  className="btn btn-icon btn-ghost"
                  aria-label="关闭"
                >
                  <X size={16} />
                </button>
              )}
            </div>
          </div>
        )}
        {children}
      </div>
    </div>
  )
}

/**
 * 无标题栏的轻量弹出面板（仍使用 backdrop）
 */
export function PopoverShell({
  open,
  onClose,
  children,
  className = '',
}: {
  open: boolean
  onClose: () => void
  children: ReactNode
  className?: string
}) {
  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 bg-black/10 backdrop-blur-[2px] grid place-items-center px-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        className={`overlay-panel w-full max-w-[640px] rounded-[var(--radius-card)] overflow-hidden ${className}`}
      >
        {children}
      </div>
    </div>
  )
}
