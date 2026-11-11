import { Stethoscope, Scale, Puzzle, Microscope, type LucideIcon } from 'lucide-react'
import { getRegisteredDomains } from '../../domains/registry'
import type { AppSection } from '../../stores/ui'
import { useResolvedTheme } from '../../hooks/useResolvedTheme'
import { HairlineDivider } from '../ui/HairlineDivider'
import iconNew from '../../images/other_icon/new.png'
import iconSkill from '../../images/other_icon/skill.png'
import iconClock from '../../images/other_icon/clock.png'
import iconFolder from '../../images/other_icon/folder.png'

/** 域图标名 → Lucide 组件映射（与 domains.yaml 中 icon 字段对应） */
const DOMAIN_ICON_MAP: Record<string, LucideIcon> = {
  stethoscope: Stethoscope,
  gavel: Scale,
  microscope: Microscope,
}

interface SidebarNavProps {
  activeSection: AppSection
  activeConvId: string | null
  onNavigate: (section: AppSection) => void
  onNewSession: () => void
}

const MAIN_MENU_ITEMS = [
  { key: 'chat' as const, label: '新任务', img: iconNew },
  { key: 'experts' as const, label: '能力扩展', img: iconSkill },
  { key: 'files' as const, label: '自动化', img: iconClock },
  { key: 'outputs' as const, label: '产出物', img: iconFolder },
]

export function SidebarNav({ activeSection, activeConvId, onNavigate, onNewSession }: SidebarNavProps) {
  const domains = getRegisteredDomains()
  const theme = useResolvedTheme()

  const handleMainItemClick = (key: typeof MAIN_MENU_ITEMS[number]['key']) => {
    if (key === 'chat') {
      if (activeSection === 'chat') {
        onNewSession()
      } else {
        onNavigate('chat')
      }
      return
    }
    onNavigate(key)
  }

  return (
    <nav className="shrink-0 pb-2 space-y-1">
      {MAIN_MENU_ITEMS.map(({ key, label, img }) => {
        const active = activeSection === key && (key !== 'chat' || !activeConvId)
        return (
          <button
            key={key}
            type="button"
            onClick={() => handleMainItemClick(key)}
            className={`sidebar-nav-row grid grid-cols-[20px_1fr] items-center gap-2 px-3 py-2.5 text-[length:var(--fs-sm)] font-medium text-left transition-all duration-200 cursor-pointer mx-3 w-[calc(100%-24px)]
                ${
                  active
                    ? 'rounded-[var(--radius-control)] is-active'
                    : 'rounded-[var(--radius-control)] text-[var(--c-text)] hover:bg-[var(--c-surface-3)]'
                }`}
          >
            <img src={img} alt={label} className="w-5 h-5 justify-self-start app-icon" />
            <span>{label}</span>
          </button>
        )
      })}

      {/* ── 业务域模块入口 ── */}
      {domains.length > 0 && (
        <>
          <HairlineDivider />
          <div className="mx-3 px-3 pb-1 pt-0.5 text-[length:var(--fs-xs)] font-medium text-[var(--c-text-secondary)]">
            智能工作台
          </div>
          {domains.map((domain) => {
            const sectionKey = `domain:${domain.manifest.key}` as AppSection
            const active = activeSection === sectionKey
            // 强调色来自业务域自己的描述（domains.yaml），核心只通用读取，无耦合
            const domAccent =
              (theme === 'dark'
                ? domain.manifest.accentDark
                : domain.manifest.accentDeep || domain.manifest.accent) ||
              'var(--c-accent)'
            const IconComp = DOMAIN_ICON_MAP[domain.manifest.icon]
            const isImgIcon =
              domain.manifest.icon.startsWith('/') || domain.manifest.icon.startsWith('http')

            return (
              <button
                key={sectionKey}
                type="button"
                onClick={() => onNavigate(sectionKey)}
                className={`sidebar-nav-row grid grid-cols-[20px_1fr] items-center gap-2 px-3 py-2.5 text-[length:var(--fs-sm)] font-medium text-left transition-all duration-200 cursor-pointer mx-3 w-[calc(100%-24px)]
                  ${
                    active
                      ? 'rounded-[var(--radius-control)] is-active'
                      : 'rounded-[var(--radius-control)] text-[var(--c-text)] hover:bg-[var(--c-surface-3)]'
                  }`}
              >
                {/* 业务域图标：无背景，仅按域描边色，与主菜单同尺寸对齐 */}
                <span className="grid h-5 w-5 place-items-center justify-self-start">
                  {IconComp ? (
                    <IconComp className="h-5 w-5" style={{ color: domAccent }} />
                  ) : isImgIcon ? (
                    <img
                      src={domain.manifest.icon}
                      alt={domain.manifest.label}
                      className="h-5 w-5 app-icon"
                    />
                  ) : (
                    <Puzzle className="h-5 w-5" style={{ color: domAccent }} />
                  )}
                </span>

                {/* 标题 + 副标题（副标题来自业务域自己的描述） */}
                <span className="flex min-w-0 flex-col gap-0.5">
                  <span className="truncate text-[length:var(--fs-sm)] font-semibold leading-tight text-[var(--c-text)]">
                    {domain.manifest.label}
                  </span>
                  {domain.manifest.subtitle && (
                    <span className="truncate text-[length:var(--fs-xs)] leading-tight text-[var(--c-text-secondary)]">
                      {domain.manifest.subtitle}
                    </span>
                  )}
                </span>
              </button>
            )
          })}
          <HairlineDivider />
        </>
      )}
    </nav>
  )
}
