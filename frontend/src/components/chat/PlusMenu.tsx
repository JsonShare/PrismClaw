import { useEffect, useRef } from 'react'
import type { ComponentType } from 'react'
import { ChevronRight } from 'lucide-react'
import { FileIcon, LinkIcon, SkillIcon, UploadIcon, WorkIcon } from '../icons/SystemIcons'

// ---------------------------------------------------------------------------
// PlusMenu - ➕ 按钮弹出菜单：上传 / 引用文件，分隔线，专家 / 技能 / 连接器。
// 专家、技能悬浮或点击展开右侧子面板；其余项点击直接执行。
// ---------------------------------------------------------------------------

export type PlusMenuAction = 'upload' | 'quote' | 'expert' | 'skill' | 'connector'

export interface PlusMenuProps {
  open: boolean
  onAction: (action: PlusMenuAction) => void
  onClose: () => void
  /** 指针进入某一项：用于收起上一项已展开的右侧飞出面板 */
  onHoverItem?: (action: PlusMenuAction) => void
}

interface MenuItemDef {
  action: PlusMenuAction
  icon: ComponentType<{ size?: number; className?: string; strokeWidth?: number }>
  label: string
  submenu?: boolean
}

type MenuEntry = MenuItemDef | { divider: true }

const MENU_ITEMS: MenuEntry[] = [
  { action: 'upload', icon: UploadIcon, label: '上传文件' },
  { action: 'quote', icon: FileIcon, label: '引用文件' },
  { divider: true },
  { action: 'expert', icon: WorkIcon, label: '专家', submenu: true },
  { action: 'skill', icon: SkillIcon, label: '技能', submenu: true },
  { action: 'connector', icon: LinkIcon, label: '连接器' },
]

export default function PlusMenu({ open, onAction, onClose, onHoverItem }: PlusMenuProps) {
  const hoverTimer = useRef<number | null>(null)
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([])

  useEffect(
    () => () => {
      if (hoverTimer.current) window.clearTimeout(hoverTimer.current)
    },
    [],
  )

  // 打开后聚焦第一项，键盘用户不必先 Tab 进菜单
  useEffect(() => {
    if (!open) return
    const raf = requestAnimationFrame(() => itemRefs.current[0]?.focus())
    return () => cancelAnimationFrame(raf)
  }, [open])

  const openItem = (item: MenuItemDef) => {
    if (hoverTimer.current) {
      window.clearTimeout(hoverTimer.current)
      hoverTimer.current = null
    }
    onAction(item.action)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
      e.stopPropagation()
      onClose()
      return
    }
    const buttons = itemRefs.current.filter((btn): btn is HTMLButtonElement => Boolean(btn))
    if (buttons.length === 0) return
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      const current = buttons.indexOf(document.activeElement as HTMLButtonElement)
      const delta = e.key === 'ArrowDown' ? 1 : -1
      const next = current < 0 ? 0 : (current + delta + buttons.length) % buttons.length
      buttons[next]?.focus()
      return
    }
    // → 展开当前项的子面板（仅专家 / 技能）
    if (e.key === 'ArrowRight') {
      const btn = buttons.find((b) => b === document.activeElement)
      if (btn?.dataset.submenu === 'true' && btn.dataset.action) {
        e.preventDefault()
        onAction(btn.dataset.action as PlusMenuAction)
      }
    }
  }

  if (!open) return null

  return (
    <div
      role="menu"
      onKeyDown={handleKeyDown}
      className="overlay-panel absolute left-0 bottom-full z-50 mb-2 w-[240px] max-w-[calc(100vw-2rem)]
       overflow-hidden rounded-[var(--radius-card)]"
    >
      <div className="max-h-[min(70vh,480px)] overflow-y-auto p-1.5">
        {MENU_ITEMS.map((entry, index) => {
          if ('divider' in entry) {
            return <div key={`divider-${index}`} className="mx-1 my-1 h-px bg-[var(--c-border)]" />
          }
          return (
            <button
              key={entry.action}
              ref={(el) => {
                // 分隔线不占索引：按菜单项自身数量顺序保存
                const itemIndex = MENU_ITEMS.slice(0, index).filter((e) => !('divider' in e)).length
                itemRefs.current[itemIndex] = el
              }}
              type="button"
              role="menuitem"
              data-action={entry.action}
              data-submenu={entry.submenu ? 'true' : undefined}
              aria-haspopup={entry.submenu ? 'menu' : undefined}
              onClick={() => openItem(entry)}
              onMouseEnter={() => {
                // 先收起上一项展开的飞出面板，再按需展开当前项
                onHoverItem?.(entry.action)
                if (!entry.submenu) return
                if (hoverTimer.current) window.clearTimeout(hoverTimer.current)
                hoverTimer.current = window.setTimeout(() => openItem(entry), 100)
              }}
              onMouseLeave={() => {
                if (hoverTimer.current) {
                  window.clearTimeout(hoverTimer.current)
                  hoverTimer.current = null
                }
              }}
              className="group flex h-9 w-full cursor-pointer items-center gap-2.5 rounded-[var(--radius-md)]
               px-2.5 text-left text-[14px] font-normal text-[var(--c-text)] transition-colors hover:bg-[var(--c-hover-bg)]
               focus:bg-[var(--c-hover-bg)] focus:outline-none"
            >
              <entry.icon
                size={16}
                strokeWidth={1.8}
                className="shrink-0 text-[var(--c-text)]"
              />
              <span className="flex-1 truncate text-[14px]">{entry.label}</span>
              {entry.submenu && (
                <ChevronRight
                  size={14}
                  strokeWidth={2}
                  className="shrink-0 text-[var(--c-text-tertiary)] transition-colors group-hover:text-[var(--c-text-secondary)]"
                />
              )}
            </button>
          )
        })}
      </div>
    </div>
  )
}
