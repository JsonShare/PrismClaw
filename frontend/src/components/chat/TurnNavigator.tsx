import { useEffect, useRef, useState } from 'react'

/**
 * 轮次快速定位（咨询页右侧轮次导航）。
 *
 * 收起态：一列短横线，每个用户提问一条；当前阅读轮次加粗加长。
 * 展开态（悬停）：浮出轮次列表，点击任意一条跳到该轮提问。
 * 显隐条件由调用方决定（轮次数 > 2 且中间栏宽于内容区默认宽度）。
 */
export interface TurnNavItem {
  /** 消息 id，作为列表 key */
  key: string
  /** 消息在虚拟列表中的下标，用于跳转 */
  index: number
  /** 提问摘要（纯文本，单行截断） */
  label: string
}

interface TurnNavigatorProps {
  turns: TurnNavItem[]
  /** 当前阅读到的轮次下标；无匹配时为 null */
  activeIndex: number | null
  onJump: (index: number) => void
}

function Dash({ active, className = '' }: { active: boolean; className?: string }) {
  return (
    <span
      aria-hidden
      className={`h-[2px] shrink-0 rounded-full transition-all ${
        active
          ? 'w-4 bg-[var(--c-text)]'
          : 'w-3 bg-[var(--c-text-tertiary)] opacity-40'
      } ${className}`}
    />
  )
}

export default function TurnNavigator({ turns, activeIndex, onJump }: TurnNavigatorProps) {
  const [expanded, setExpanded] = useState(false)
  // 关闭用短延时：刻度与卡片之间有一段空隙，直接 mouseleave 收起会让鼠标
  // 横移途中卡片就消失、永远点不到。
  const closeTimerRef = useRef<number | null>(null)
  useEffect(
    () => () => {
      if (closeTimerRef.current) window.clearTimeout(closeTimerRef.current)
    },
    [],
  )
  const openCard = () => {
    if (closeTimerRef.current) {
      window.clearTimeout(closeTimerRef.current)
      closeTimerRef.current = null
    }
    setExpanded(true)
  }
  const scheduleClose = () => {
    if (closeTimerRef.current) window.clearTimeout(closeTimerRef.current)
    closeTimerRef.current = window.setTimeout(() => {
      closeTimerRef.current = null
      setExpanded(false)
    }, 220)
  }

  return (
    <div
      // 贴内容区右缘外侧：中间栏更宽时留在内容列右侧，窄于内容宽时退回 12px。
      // top-1/2 让导航垂直居中；卡片以自身为中心向左浮出。
      className="absolute top-1/2 z-20 -translate-y-1/2"
      style={{ right: 'max(12px, calc((100% - var(--chat-content-w, 1000px)) / 2 + 8px))' }}
      onMouseEnter={openCard}
      onMouseLeave={scheduleClose}
    >
      {/* 收起态：轮次刻度 */}
      <div className="no-scrollbar flex max-h-[70vh] flex-col items-end gap-1.5 overflow-y-auto py-2">
        {turns.map((turn) => {
          const active = turn.index === activeIndex
          return (
            <button
              key={turn.key}
              type="button"
              onClick={() => onJump(turn.index)}
              title={turn.label}
              aria-label={turn.label}
              aria-current={active ? 'true' : undefined}
              className="group/turn flex h-3.5 w-6 shrink-0 cursor-pointer items-center justify-end"
            >
              <Dash
                active={active}
                className={active ? '' : 'group-hover/turn:opacity-80'}
              />
            </button>
          )
        })}
      </div>

      {/* 展开态：轮次列表 */}
      {expanded && (
        <div className="overlay-panel absolute right-[34px] top-1/2 w-[min(520px,46vw)] -translate-y-1/2 overflow-hidden rounded-[var(--radius-card)] p-1.5">
          <div className="no-scrollbar max-h-[min(60vh,460px)] overflow-y-auto">
            {turns.map((turn) => {
              const active = turn.index === activeIndex
              return (
                <button
                  key={turn.key}
                  type="button"
                  onClick={() => {
                    setExpanded(false)
                    onJump(turn.index)
                  }}
                  title={turn.label}
                  aria-current={active ? 'true' : undefined}
                  className="flex w-full cursor-pointer items-center gap-3 rounded-[var(--radius-md)] px-3 py-2.5 text-left transition-colors hover:bg-[var(--c-hover-bg)]"
                >
                  <span
                    className={`min-w-0 flex-1 truncate text-[length:var(--fs-md)] leading-snug ${
                      active
                        ? 'font-medium text-[var(--c-text)]'
                        : 'text-[var(--c-text-tertiary)]'
                    }`}
                  >
                    {turn.label}
                  </span>
                  <Dash active={active} />
                </button>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
