import { useEffect, useRef, useState } from 'react'
import { Check, ChevronDown, Gauge } from 'lucide-react'
import { useEffortStore, type EffortLevel } from '../../stores'

const EFFORTS: ReadonlyArray<{
  value: EffortLevel
  label: string
  helpTitle: string
  helpRules: string[]
}> = [
  {
    value: 'quick',
    label: '快速',
    helpTitle: '快速（少步数、短时限）',
    helpRules: ['steps 3 / 工具 6 次', '适合简单问答、快速确认', '复杂任务可能提前收敛'],
  },
  {
    value: 'standard',
    label: '标准',
    helpTitle: '标准（默认档位）',
    helpRules: ['steps 6 / 工具 12 次', '适合大多数日常任务', '在深度与速度间平衡'],
  },
  {
    value: 'deep',
    label: '深度',
    helpTitle: '深度（多步数、长时限）',
    helpRules: ['steps 15 / 工具 24 次', '适合长链路检索、复杂产出', '耗时更长，结果更充分'],
  },
]

export default function EffortToggle() {
  const effort = useEffortStore((s) => s.effort)
  const setEffort = useEffortStore((s) => s.setEffort)
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

  const current = EFFORTS.find((e) => e.value === effort) ?? EFFORTS[1]

  return (
    <div ref={wrapperRef} className="relative inline-flex">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={`h-8 px-2 flex items-center gap-1.5 rounded-[var(--radius-control)] transition-colors cursor-pointer outline-none
                    hover:text-[var(--c-text)]
                    ${open ? 'text-[var(--c-text)]' : 'text-[var(--c-text-secondary)]'}`}
        title={`执行档位：${current.label}`}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <Gauge size={14} className="shrink-0" />
        <span className="text-[length:var(--fs-sm)] font-medium truncate max-w-[60px] -translate-y-px">
          {current.label}
        </span>
        <ChevronDown
          size={14}
          className={`shrink-0 transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div
          role="listbox"
          aria-label="执行档位"
          className={`overlay-panel absolute left-0 bottom-full mb-2 z-30
                     rounded-[var(--radius-card)]
                     overflow-hidden flex flex-col animate-in fade-in slide-in-from-bottom-2 duration-200
                     w-[190px]`}
        >
          <div className="p-2">
            <div className="space-y-0.5">
              {EFFORTS.map(({ value, label, helpTitle, helpRules }) => {
                const selected = value === effort
                return (
                  <div key={value} className="relative group">
                    <button
                      type="button"
                      role="option"
                      aria-selected={selected}
                      data-testid={`effort-option-${value}`}
                      onClick={() => {
                        setEffort(value)
                        setOpen(false)
                      }}
                      className={`group flex w-full items-center gap-2.5 rounded-[var(--radius-control)] px-3 py-1.5 text-left cursor-pointer transition-colors
                                  ${selected
                                    ? 'bg-[var(--c-surface-2)]/40'
                                    : 'hover:bg-[var(--c-surface-2)]/30'}`}
                    >
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

                    <div className="absolute left-full top-1/2 -translate-y-1/2 ml-3 w-[260px] max-w-[calc(100vw-2rem)] hidden group-hover:block pointer-events-none z-40">
                      <div className="overlay-panel rounded-[var(--radius-card)] overflow-hidden">
                        <div className="px-4 py-3 text-[length:var(--fs-md)] font-semibold text-[var(--c-text)]">
                          {helpTitle}
                        </div>
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
