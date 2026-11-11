import { Moon, Sun, Monitor } from 'lucide-react'
import type { Theme } from '../../types'

interface Props {
  theme: Theme
  onChange: (t: Theme) => void
}

const options: { value: Theme; label: string; icon: typeof Sun }[] = [
  { value: 'light', label: '亮色主题', icon: Sun },
  { value: 'dark', label: '暗色主题', icon: Moon },
  { value: 'system', label: '跟随系统', icon: Monitor },
]

export default function ThemeToggle({ theme, onChange }: Props) {
  return (
    <div
      className="inline-flex items-center gap-1"
      role="group"
      aria-label="主题切换"
    >
      {options.map(({ value, label, icon: Icon }) => {
        const active = theme === value
        return (
          <button
            key={value}
            type="button"
            onClick={() => onChange(value)}
            className={`
              relative flex items-center justify-center w-8 h-8 rounded-[var(--radius-control)]
              transition-all duration-200 cursor-pointer
              ${active
                ? 'bg-[var(--c-select-bg)] text-[var(--c-text)]'
                : 'text-[var(--c-text-secondary)] hover:text-[var(--c-text)] hover:bg-[var(--c-surface)]/50'}
            `}
            aria-label={label}
            aria-pressed={active}
            title={label}
          >
            <Icon size={14} strokeWidth={2} />
          </button>
        )
      })}
    </div>
  )
}
