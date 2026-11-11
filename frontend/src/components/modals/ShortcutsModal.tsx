import { useEffect } from 'react'
import { X } from 'lucide-react'
import { modKeyLabel } from '../../hooks/useGlobalKeyboard'

interface Props {
  open: boolean
  onClose: () => void
}

interface Entry {
  keys: string[]
  description: string
}

export default function ShortcutsModal({ open, onClose }: Props) {
  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, open])

  if (!open) return null

  const mod = modKeyLabel()
  const entries: Entry[] = [
    { keys: [mod, 'K'], description: '打开命令面板' },
    { keys: [mod, 'N'], description: '新建会话' },
    { keys: [mod, '\\'], description: '切换侧边栏' },
    { keys: [mod, '/'], description: '打开此帮助' },
    { keys: [mod, 'Enter'], description: '强制发送（会中断生成）' },
    { keys: ['Esc'], description: '关闭已打开的弹窗/面板' },
    { keys: ['Enter'], description: '发送当前消息（输入框内）' },
    { keys: ['Shift', 'Enter'], description: '输入框内换行' },
  ]

  return (
    <div
      className="overlay-scrim fixed inset-0 z-50 flex items-center justify-center px-4 animate-in"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="overlay-panel w-full max-w-md rounded-[var(--radius-card)] overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3">
          <span className="text-[length:var(--fs-md)] font-display font-semibold text-[var(--c-text)]">
            快捷键
          </span>
          <button
            type="button"
            onClick={onClose}
            className="btn btn-icon btn-ghost"
          >
            <X size={14} />
          </button>
        </div>
        <ul className="px-4 py-3 space-y-2 text-[length:var(--fs-md)]">
          {entries.map((entry, i) => (
            <li key={i} className="flex items-center justify-between gap-3">
              <span className="text-[var(--c-text-secondary)]">{entry.description}</span>
              <span className="flex items-center gap-1">
                {entry.keys.map((k) => (
                  <kbd
                    key={k}
                    className="px-1.5 py-0.5 text-[length:var(--fs-sm)] rounded-[var(--radius-control)] border border-[var(--c-border)]
                               bg-[var(--c-surface)] text-[var(--c-text)] font-mono"
                  >
                    {k}
                  </kbd>
                ))}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
