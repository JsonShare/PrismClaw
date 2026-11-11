import { useEffect, useRef, useState, type ReactNode } from 'react'
import iconMenuFold from '../../images/other_icon/menu_fold.png'
import iconNew from '../../images/other_icon/new.png'
import iconBarExpand from '../../images/other_icon/bar_expand.png'
import iconBarFold from '../../images/other_icon/bar_fold.png'

export interface ChatHeaderProps {
  /** 侧边栏状态 */
  sidebarCollapsed: boolean
  onToggleSidebar: () => void

  /** 新任务按钮 */
  onNewSession: () => void

  /** 会话标题 */
  title: string

  /** landing 模式（透明背景） */
  isLanding: boolean

  /** 会话已开始（非 landing）时显示面板按钮 */
  showPanelButton: boolean
  taskFilesOpen: boolean
  onTogglePanel: () => void

  /** 额外的左侧/右侧按钮 */
  headerLeft?: ReactNode
  headerRight?: ReactNode
}

/**
 * 统一的聊天头部 — 从 Chat.tsx 提取。
 *
 * 标题显示逻辑：
 * - 默认最多显示 20 个 CJK 字符（14px 字号，每字约 14px 宽）
 * - 用 ResizeObserver 测量标题容器实际宽度，窗口缩小时自动压缩字符上限
 * - 超出上限时 JS slice + "…" 省略号，hover title 显示完整原文
 */
export function ChatHeader({
  sidebarCollapsed,
  onToggleSidebar,
  onNewSession,
  title,
  isLanding,
  showPanelButton,
  taskFilesOpen,
  onTogglePanel,
  headerLeft,
  headerRight,
}: ChatHeaderProps) {
  const baseTitle = title || (isLanding ? '' : '对话')

  // 动态计算容器能容纳的最大字符数
  const titleBoxRef = useRef<HTMLDivElement>(null)
  const [dynamicMax, setDynamicMax] = useState(20)

  useEffect(() => {
    const el = titleBoxRef.current
    if (!el) return
    const update = () => {
      const w = el.clientWidth
      // CJK 14px 字号下每字约 14px 宽；-8px 余量防 "…" 溢出
      const n = Math.max(1, Math.min(20, Math.floor((w - 8) / 14)))
      setDynamicMax(n)
    }
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const truncated = baseTitle.length > dynamicMax ? baseTitle.slice(0, dynamicMax) + '…' : baseTitle
  const displayTitle = baseTitle ? truncated : ''

  return (
    <div className="chat-area min-h-[56px] h-auto px-4 pt-2 pb-2 flex items-center shrink-0 bg-transparent">
      {/* 左侧按钮 */}
      <div className="shrink-0 flex items-center gap-1">
        <button
          type="button"
          onClick={onToggleSidebar}
          className="btn btn-icon btn-ghost"
          title={sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'}
        >
          <img src={iconMenuFold} alt={sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'} className="w-5 h-5 app-icon" />
        </button>
        {sidebarCollapsed && (
          <button
            type="button"
            onClick={onNewSession}
            className="btn btn-icon btn-ghost"
            title="新任务"
          >
            <img src={iconNew} alt="新任务" className="w-5 h-5 app-icon" />
          </button>
        )}
        {headerLeft}
      </div>

      {/* 标题 */}
      <div ref={titleBoxRef} className="flex-1 flex justify-center min-w-0 overflow-hidden px-2">
        <div className="flex flex-col items-center min-w-0 max-w-full">
          <div
            className="text-[length:var(--fs-md)] font-bold text-[var(--c-text)] text-center leading-tight truncate max-w-full"
            title={title || undefined}
          >
            {displayTitle}
          </div>
          {!isLanding && (
            <div className="mt-0.5 text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]/60 whitespace-nowrap">
              内容由 AI 生成，请仔细核查
            </div>
          )}
        </div>
      </div>

      {/* 右侧按钮 */}
      <div className="shrink-0 flex items-center gap-2">
        {showPanelButton && (
          <button
            type="button"
            onClick={onTogglePanel}
            className="btn btn-icon btn-ghost"
            title={taskFilesOpen ? '收起右侧面板' : '展开右侧面板'}
            aria-label={taskFilesOpen ? '收起右侧面板' : '展开右侧面板'}
          >
            {taskFilesOpen ? (
              <img src={iconBarFold} alt="收起右侧面板" className="w-5 h-5 app-icon" />
            ) : (
              <img src={iconBarExpand} alt="展开右侧面板" className="w-5 h-5 app-icon" />
            )}
          </button>
        )}
        {headerRight}
      </div>
    </div>
  )
}
