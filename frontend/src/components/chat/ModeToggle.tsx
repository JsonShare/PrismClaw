import { useEffect, useRef, useState } from 'react'
import { Check, ChevronDown } from 'lucide-react'
import { usePermissionStore, type AgentMode } from '../../stores'
import chatIcon from '../../images/other_icon/chat.png'
import workIcon from '../../images/other_icon/work.png'

const MODES: ReadonlyArray<{
  value: AgentMode
  label: string
  icon: string
  helpTitle: string
  helpRules: string[]
}> = [
  {
    value: 'chat',
    label: 'Chat 咨询',
    icon: chatIcon,
    helpTitle: '咨询模式（只读）',
    helpRules: ['只读问答，不会修改文件', '适合咨询、分析代码、讨论方案', '可随时切换到 WORK 模式'],
  },
  {
    value: 'work',
    label: 'Work 协作',
    icon: workIcon,
    helpTitle: '协作模式（可写）',
    helpRules: ['可创建/编辑/删除文件、执行命令', '适合产出交付物、修复 Bug、编写代码', '可在 WORK 内选择"请求批准"或"完全访问权限"'],
  },
]

export default function ModeToggle() {
  const mode = usePermissionStore((s) => s.mode)
  const setMode = usePermissionStore((s) => s.setMode)
  const [open, setOpen] = useState(false)
  const wrapperRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function onDocumentClick(event: MouseEvent) {
      const target = event.target
      if (!(target instanceof Node)) return
      if (wrapperRef.current && !wrapperRef.current.contains(target)) setOpen(false)
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDocumentClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocumentClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const current = MODES.find((m) => m.value === mode) ?? MODES[0]

  return (
    <div ref={wrapperRef} className="relative inline-flex">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={`h-8 px-2 flex items-center gap-2 rounded-[var(--radius-control)] transition-colors cursor-pointer outline-none
                    hover:text-[var(--c-text)]
                    ${open ? 'text-[var(--c-text)]' : 'text-[var(--c-text-secondary)]'}`}
        title={`模式：${current.label}`}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <img
          src={current.icon}
          alt=""
          width={14}
          height={14}
          className="shrink-0 app-icon"
          style={{ transform: 'translateY(-2px)' }}
        />
        <span className="text-[length:var(--fs-sm)] font-medium truncate max-w-[100px] -translate-y-px">{current.label}</span>
        <ChevronDown size={14} className={`shrink-0 transition-transform duration-200 ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div
          role="listbox"
          aria-label="运行模式"
          className={`overlay-panel absolute left-0 bottom-full mb-2 z-30
                     rounded-[var(--radius-card)]
                     overflow-hidden flex flex-col animate-in fade-in slide-in-from-bottom-2 duration-200
                     w-[200px]`}
        >
          <div className="p-2">
            <div className="space-y-0.5">
              {MODES.map(({ value, label, icon, helpTitle, helpRules }) => {
                const selected = value === mode
                return (
                  <div key={value} className="relative group">
                    <button
                      type="button"
                      role="option"
                      aria-selected={selected}
                      data-testid={`mode-option-${value}`}
                      onClick={() => {
                        setMode(value)
                        setOpen(false)
                      }}
                      className={`group flex w-full items-center gap-2.5 rounded-[var(--radius-control)] px-3 py-1.5 text-left cursor-pointer transition-colors
                                  ${selected
                                    ? 'bg-[var(--c-surface-2)]/40'
                                    : 'hover:bg-[var(--c-surface-2)]/30'}`}
                    >
                      <span className="shrink-0">
                        <img
                          src={icon}
                          alt=""
                          width={16}
                          height={16}
                          className="app-icon text-[var(--c-text-secondary)]/70"
                        />
                      </span>
                      <span
                        className={`min-w-0 flex-1 truncate text-[length:var(--fs-sm)]
                                    ${selected ? 'font-semibold text-[var(--c-text)]' : 'text-[var(--c-text-secondary)]'}`}
                      >
                        {label}
                      </span>
                      {selected && (
                        <Check size={16} strokeWidth={1.5} className="shrink-0 text-[var(--c-accent)]" />
                      )}
                    </button>

                    <div className="absolute left-full top-1/2 -translate-y-1/2 ml-3 w-[280px] max-w-[calc(100vw-2rem)] hidden group-hover:block pointer-events-none z-40">
                      <div className="overlay-panel rounded-[var(--radius-card)] overflow-hidden">
                        <div className="px-4 py-3 text-[length:var(--fs-md)] font-semibold text-[var(--c-text)]">{helpTitle}</div>
                        <div className="px-4 pb-4 text-[length:var(--fs-md)] text-[var(--c-text-secondary)]">
                          <ul className="space-y-1 list-disc pl-5">
                            {helpRules.map((r) => (
                              <li key={r}>{r}</li>
                            ))}
                          </ul>
                        </div>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
