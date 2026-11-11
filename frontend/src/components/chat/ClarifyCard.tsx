import { CheckSquare, X } from 'lucide-react'
import type { ClarifyFlow } from '../../hooks/chat/useClarifyFlow'

/**
 * AskUserQuestion 分页澄清卡片（纯渲染）。
 * 全部状态/动作来自 useClarifyFlow——解析、选择态、提交、跳过都不在本组件。
 *
 * 布局：flex-col 三段式（固定头 + 可滚中 + 固定底），填满 ChatInputBox 的 flex-1 区域。
 */
export default function ClarifyCard({ flow }: { flow: ClarifyFlow }) {
  const {
    questions,
    currentQuestionIndex,
    selectedAnswers,
    toggleOption,
    addFreeAnswer,
    advance,
    finalSubmit,
    skipAll,
  } = flow

  if (questions.length === 0) return null

  const q = questions[currentQuestionIndex]
  const currentSelection = selectedAnswers[currentQuestionIndex] || new Set<string>()

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* ── 固定顶部状态栏：进度 + 暂停提示 + 关闭 ── */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--c-border)]/60 shrink-0">
        <div className="flex items-center gap-2">
          <span className="inline-flex h-1.5 w-1.5 rounded-full bg-[var(--c-accent)] animate-pulse" />
          <span className="text-[length:var(--fs-sm)] font-semibold text-[var(--c-accent)]">
            需要回答才能继续
          </span>
          <span className="text-[length:var(--fs-sm)] font-medium tabular-nums text-[var(--c-text-secondary)] bg-[var(--c-surface-2)]/60 px-1.5 py-0.5 rounded-[var(--radius-control)]">
            {currentQuestionIndex + 1} / {questions.length}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {/* 进度点 */}
          {questions.map((_, i) => (
            <span
              key={i}
              className={`h-1.5 w-4 rounded-full transition-all ${
                i < currentQuestionIndex
                  ? 'bg-[var(--c-accent)]'
                  : i === currentQuestionIndex
                    ? 'bg-[var(--c-accent)]/60'
                    : 'bg-[var(--c-border)]'
              }`}
            />
          ))}
          <button
            type="button"
            onClick={skipAll}
            className="btn btn-icon btn-ghost ml-2"
            title="跳过全部问题"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      {/* ── 可滚动中部：问题 + 选项列表 ── */}
      {q && (
        <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3">
          {/* 问题标题 */}
          <div className="text-[length:var(--fs-sm)] font-semibold text-[var(--c-text)] leading-relaxed">
            {q.header && (
              <span className="inline-block px-1.5 py-0.5 rounded-[var(--radius-control)] text-[length:var(--fs-sm)] font-medium bg-[var(--c-accent)]/10 text-[var(--c-accent)] mr-1.5 align-middle">
                {q.header}
              </span>
            )}
            {q.question}
            {q.multiSelect && (
              <span className="ml-2 inline-flex items-center gap-0.5 text-[length:var(--fs-sm)] font-normal text-[var(--c-text-secondary)]">
                <CheckSquare size={11} /> 可多选
              </span>
            )}
          </div>

          {/* 选项列表 */}
          <div className="mt-2.5 space-y-1.5">
            {q.options.map((opt) => {
              const selected = currentSelection.has(opt.label)
              const isRadio = !q.multiSelect

              return (
                <button
                  key={opt.label}
                  type="button"
                  onClick={() => toggleOption(currentQuestionIndex, opt.label, q.multiSelect)}
                  className={`
                    group relative w-full flex items-center gap-2.5 px-3 py-2.5 rounded-[var(--radius-md)] text-left cursor-pointer
                    text-[length:var(--fs-sm)] transition-all duration-150
                    ${selected
                      ? 'bg-[var(--c-accent)]/10 text-[var(--c-text)] border border-[var(--c-accent)]/30'
                      : 'text-[var(--c-text-secondary)] border border-transparent hover:bg-[var(--c-surface-2)]/40 hover:border-[var(--c-border)]/40'
                    }
                  `}
                  aria-pressed={selected}
                >
                  {/* 左侧强调条（选中态） */}
                  <span className={`absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-[60%] rounded-r-full transition-all ${
                    selected ? 'bg-[var(--c-accent)]' : 'opacity-0'
                  }`} />

                  {/* 单选：圆形单选按钮 | 多选：方形复选框 */}
                  {isRadio ? (
                    /* ── Radio (单选) ── */
                    <span className={`
                      h-4 w-4 rounded-full border grid place-items-center shrink-0 transition-all
                      ${selected
                        ? 'border-[var(--c-accent)] bg-[var(--c-surface)]'
                        : 'border-[var(--c-border)] bg-transparent group-hover:border-[var(--c-text-secondary)]/40'
                      }
                    `}>
                      {selected && (
                        <span className="h-2 w-2 rounded-full bg-[var(--c-accent)]" />
                      )}
                    </span>
                  ) : (
                    /* ── Checkbox (多选) ── */
                    <span className={`
                      h-4 w-4 rounded-[var(--radius-control)] border grid place-items-center shrink-0 transition-all
                      ${selected
                        ? 'border-[var(--c-accent)] bg-[var(--c-accent)]'
                        : 'border-[var(--c-border)] bg-transparent group-hover:border-[var(--c-text-secondary)]/40'
                      }
                    `}>
                      {selected && (
                        <svg width="10" height="8" viewBox="0 0 10 8" fill="none" className="text-[var(--c-btn-primary-fg)]">
                          <path d="M1 4L3.5 6.5L9 1" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      )}
                    </span>
                  )}

                  {/* 标签文字 */}
                  <span className="min-w-0 flex-1 leading-snug">
                    <span className={selected ? 'font-medium' : ''}>{opt.label}</span>
                    {opt.description && (
                      <span className={`block mt-0.5 ${selected ? 'text-[var(--c-text-secondary)]/70' : 'text-[var(--c-text-secondary)]/45'}`}>
                        {opt.description}
                      </span>
                    )}
                  </span>
                </button>
              )
            })}
          </div>
        </div>
      )}

      {/* ── 固定底部操作栏 ── */}
      <div className="px-4 py-3 border-t border-[var(--c-border)]/60 shrink-0">
        <div className="flex items-center gap-2">
          {/* 自由文本输入（备选） */}
          <input
            type="text"
            placeholder={q?.multiSelect ? '或直接输入其他答案…' : '或选择 "其他" 并输入…'}
            className="field-input clarify-input flex-1 min-w-0 h-9 px-3"
            style={{ fontSize: 'var(--fs-sm)' }}
            onKeyDown={(e) => {
              if (e.key !== 'Enter' || e.nativeEvent.isComposing) return
              const raw = e.currentTarget.value
              if (!raw.trim()) return
              e.preventDefault()
              e.stopPropagation()
              const val = raw.trim()
              e.currentTarget.value = ''
              addFreeAnswer(currentQuestionIndex, val)
              // 最后一题直接走 finalSubmit（对应按钮文案「提交全部」），
              // 非最后一题前进一题（对应按钮文案「下一题」）。advance()
              // 在末位是空操作，之前就因为这个 bug 让回车像没反应。
              if (currentQuestionIndex >= questions.length - 1) finalSubmit()
              else advance()
            }}
          />

          {/* 操作按钮 */}
          {currentQuestionIndex < questions.length - 1 ? (
            <button
              type="button"
              onClick={advance}
              disabled={currentSelection.size === 0}
              className="btn btn-md btn-primary shrink-0"
            >
              下一题 →
            </button>
          ) : (
            <button
              type="button"
              onClick={finalSubmit}
              className="btn btn-md btn-primary shrink-0"
            >
              提交全部
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
