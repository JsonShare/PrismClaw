import { useEffect, useRef } from 'react'
import {
  Upload,
  AtSign,
  Blocks,
  Hammer,
  ChevronRight,
} from 'lucide-react'
import { HairlineDivider } from '../ui/HairlineDivider'

// ---------------------------------------------------------------------------
// PlusMenu - ➕ 按钮弹出菜单
//
// 四个选项：添加文件 / 引用文件 / 专家 / 技能
// 其中"引用文件 / 专家 / 技能"会进一步弹出子选择面板（用右侧箭头提示）。
// "引用文件"与输入框 `@` 打开的是同一个面板（工作区文件 + 对话文件），
// 是移动端等无键盘触发场景的入口。
// 展开交互：悬浮（含 100ms 防抖）或点击均可展开子面板，二者等价。
// 视觉对齐"搜索/选择"子面板的设计语言：浅灰 hover、统一圆角、扁平边框。
// ---------------------------------------------------------------------------

export type PlusMenuAction = 'add_file' | 'context' | 'plugin' | 'skill'

export interface PlusMenuProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onAction: (action: PlusMenuAction) => void
}

interface MenuItemDef {
  action: PlusMenuAction
  icon: typeof Upload
  label: string
  /** 是否在右侧显示 ChevronRight 提示"可展开" */
  hasSubPanel: boolean
  /** 在该项之前渲染一条分组分隔线 */
  dividerBefore?: boolean
}

const MENU_ITEMS: MenuItemDef[] = [
  {
    action: 'add_file',
    icon: Upload,
    label: '添加文件',
    hasSubPanel: false,
  },
  {
    action: 'context',
    icon: AtSign,
    label: '引用文件',
    hasSubPanel: true,
  },
  {
    action: 'plugin',
    icon: Blocks,
    label: '专家',
    hasSubPanel: true,
    dividerBefore: true,
  },
  {
    action: 'skill',
    icon: Hammer,
    label: '技能',
    hasSubPanel: true,
  },
]

export default function PlusMenu({ open, onOpenChange, onAction }: PlusMenuProps) {
  const hoverTimer = useRef<number | null>(null)

  // 外部点击收起交由 ChatInputBox 的工具栏容器统一处理（主菜单 + 右侧飞出子面板共用一个边界）

  // 卸载时清理可能挂起的悬浮定时器
  useEffect(
    () => () => {
      if (hoverTimer.current) window.clearTimeout(hoverTimer.current)
    },
    [],
  )

  // 展开子面板（悬浮 / 点击通用）：触发对应动作并保持主菜单常驻（右侧飞出），由容器统一收起
  const openSubPanel = (action: PlusMenuAction) => {
    if (hoverTimer.current) {
      window.clearTimeout(hoverTimer.current)
      hoverTimer.current = null
    }
    onAction(action)
  }

  if (!open) return null

  return (
    <div
      className="overlay-panel absolute left-0 bottom-full mb-2 w-60 rounded-[var(--radius-card)] overflow-hidden z-50"
    >
      <div className="p-1">
        {MENU_ITEMS.map((item) => (
          <div key={item.action}>
              {item.dividerBefore && (
                <HairlineDivider className="mx-2 my-1" />
              )}
            <button
              type="button"
              onClick={() => {
                if (item.hasSubPanel) {
                  openSubPanel(item.action)
                } else {
                  openSubPanel(item.action)
                  onOpenChange(false)
                }
              }}
              onMouseEnter={
                item.hasSubPanel
                  ? () => {
                      if (hoverTimer.current) window.clearTimeout(hoverTimer.current)
                      hoverTimer.current = window.setTimeout(() => openSubPanel(item.action), 100)
                    }
                  : undefined
              }
              onMouseLeave={
                item.hasSubPanel
                  ? () => {
                      if (hoverTimer.current) {
                        window.clearTimeout(hoverTimer.current)
                        hoverTimer.current = null
                      }
                    }
                  : undefined
              }
              className="group w-full flex items-center gap-2.5 pl-2.5 pr-2 py-2 rounded-[var(--radius-md)] text-left transition-colors cursor-pointer text-[var(--c-text)] hover:bg-[var(--c-surface-3)] focus:bg-[var(--c-surface-3)] focus:outline-none"
            >
              <item.icon
                size={16}
                strokeWidth={2}
                className="shrink-0 text-[var(--c-text-secondary)] group-hover:text-[var(--c-text)] transition-colors"
              />
              <span className="flex-1 text-[length:var(--fs-sm)]">{item.label}</span>
              {item.hasSubPanel && (
                <ChevronRight
                  size={14}
                  strokeWidth={2}
                  className="shrink-0 text-[var(--c-text-secondary)]/60 group-hover:text-[var(--c-text-secondary)] transition-colors"
                />
              )}
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
