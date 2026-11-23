import { Fragment, useSyncExternalStore } from 'react'
import {
  getDomainEntry,
  getDomainNavItems,
  getDomainOrder,
  getRegistryVersion,
  subscribeRegistryVersion,
  type DomainNavItem,
} from '../../domains/registry'
import {
  getDomainActive,
  requestDomainPage,
  subscribeDomainActive,
} from '../../domains/nav-bus'
import type { AppSection } from '../../stores/ui'
import { HairlineDivider } from '../ui/HairlineDivider'
import iconNew from '../../images/other_icon/new.png'
import iconSkill from '../../images/other_icon/skill.png'
import iconClock from '../../images/other_icon/clock.png'
import iconFolder from '../../images/other_icon/folder.png'

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

/** 侧栏平铺后的一组：分组标签 + 该组内的域菜单项 */
interface NavGroup {
  label: string
  domainKey: string
  items: DomainNavItem[]
}

/**
 * 把所有业务域的菜单项按 group 拍平成一维分组列表。
 * 顺序由 domains.yaml 的声明顺序决定（各域声明的菜单分组顺序），
 * 每个分组在侧栏里都是一个平级块，块与块之间用发丝线分割。
 */
function buildNavGroups(): NavGroup[] {
  const groups: NavGroup[] = []
  // 顺序取构建期生成表的键序（= domains.yaml 声明顺序）。
  // 不能用 getRegisteredDomains() 的顺序：那是后端 bundle 注册顺序，不稳定。
  for (const domainKey of getDomainOrder()) {
    const entry = getDomainEntry(domainKey)
    if (!entry) continue // 后端未启用或前端未编译该域
    const items = getDomainNavItems(domainKey)
    // 单页域：整域就是一个块，标签用域名
    if (items.length < 2) {
      groups.push({ label: entry.manifest.label, domainKey, items: [] })
      continue
    }
    for (const item of items) {
      let group = groups.find((g) => g.label === item.group && g.domainKey === domainKey)
      if (!group) {
        group = { label: item.group, domainKey, items: [] }
        groups.push(group)
      }
      group.items.push(item)
    }
  }
  return groups
}

export function SidebarNav({ activeSection, activeConvId, onNavigate, onNewSession }: SidebarNavProps) {
  // 域注册表是异步填充的（App 启动时拉 /api/domains/manifests），
  // 必须订阅版本号，否则首次渲染拿到的空注册表不会随注册完成而刷新。
  useSyncExternalStore(subscribeRegistryVersion, getRegistryVersion)
  const navActive = useSyncExternalStore(subscribeDomainActive, getDomainActive)

  const navGroups = buildNavGroups()
  const hasDomainNav = navGroups.length > 0

  const handleMainItemClick = (key: typeof MAIN_MENU_ITEMS[number]['key']) => {
    if (key === 'chat') {
      // "新任务" 的语义是始终开启新会话：无论当前在哪个 section（chat / domain / files …），
      // 都必须清空 activeConvId 让页面回到 landing home。之前在非 chat section 只切 section
      // 不清 convId，导致旧会话被意外复用（用户以为点了新任务，看到的却是上次的任务）。
      onNewSession()
      return
    }
    onNavigate(key)
  }

  /** 域菜单项点击：切到该域 + 让域切到目标页 */
  const handleDomainItemClick = (domainKey: string, page: string) => {
    requestDomainPage(domainKey, page)
    onNavigate(`domain:${domainKey}` as AppSection)
  }

  return (
    <nav className="shrink-0 pb-2 flex flex-col min-h-0">
      {/* 主菜单不参与滚动 —— 否则域子项一多，新任务/能力扩展等会被滚出视野 */}
      <div className="shrink-0 space-y-1">
        {MAIN_MENU_ITEMS.map(({ key, label, img }) => {
          const active = activeSection === key && (key !== 'chat' || !activeConvId)
          return (
            <button
              key={key}
              type="button"
              onClick={() => handleMainItemClick(key)}
              // 图标列用 1.25rem 而非 20px：根字号是 14px（--fs-md），
              // 1.25rem = 17.5px，与业务域菜单的图标列同宽、始终同轴；
              // 图标本身固定 16px（不随根字号缩放），与业务域菜单的 16px 图标一致。
              className={`sidebar-nav-row grid grid-cols-[1.25rem_1fr] items-center gap-2 px-3 py-2.5 text-[length:var(--fs-sm)] font-medium text-left transition-colors duration-150 cursor-pointer mx-3 w-[calc(100%-1.5rem)] rounded-[var(--radius-control)] ${
                active
                  ? 'is-active'
                  : 'text-[var(--c-text)] hover:bg-[color-mix(in_srgb,var(--c-text)_6%,transparent)]'
              }`}
            >
              <img src={img} alt={label} className="h-[16px] w-[16px] justify-self-center app-icon" />
              <span>{label}</span>
            </button>
          )
        })}
      </div>

      {/* ── 业务域菜单：每个分组一块，块间发丝线分割 ──
          仅这一块滚动：侧栏高度有限，域多起来时不能把「所有任务」挤没。
          纵向节奏统一由容器的 gap 控制，且与主菜单的 space-y-1 同值（0.25rem = 3.75px）：
          分组标签、子项、分割线之间的间距完全一致，不再给标签单独加 margin。
          不用 gap-1.5：那是 5.625px，会比主菜单松一档，「统一」就不成立。 */}
      {hasDomainNav && (
        <>
          <HairlineDivider />
          <div className="min-h-0 overflow-y-auto overscroll-contain max-h-[42vh] flex flex-col gap-1">
            {navGroups.map((group, groupIndex) => {
              // 只有「换域」时才画线：同一域内的多个菜单分组靠统一间距衔接
              const startNewDomain =
                groupIndex > 0 && navGroups[groupIndex - 1].domainKey !== group.domainKey
              return (
                <Fragment key={`${group.domainKey}:${group.label || '__ungrouped'}`}>
                  {startNewDomain && <HairlineDivider className="mx-3" />}

                  {/* 分组标签：12px/600 + 字距 + 灰字，弱化到「分区标签」层级 */}
                  <div className="mx-3 flex h-6 items-center px-3 text-[length:var(--fs-xs)] font-semibold tracking-[0.08em] text-[var(--c-text-tertiary)]">
                    <span className="truncate">{group.label}</span>
                  </div>

                  {/* 单页域没有子项，整块只有标题，点击标题进入 —— 见下方 onClick 分支 */}
                  {group.items.length === 0 ? (
                    <button
                      type="button"
                      onClick={() => onNavigate(`domain:${group.domainKey}` as AppSection)}
                      className={`sidebar-nav-row w-[calc(100%-1.5rem)] mx-3 flex items-center rounded-[var(--radius-control)] px-3 py-2.5 text-[length:var(--fs-sm)] font-semibold text-left transition-colors duration-150 cursor-pointer ${
                        activeSection === `domain:${group.domainKey}`
                          ? 'is-active'
                          : 'text-[var(--c-text-secondary)] hover:bg-[color-mix(in_srgb,var(--c-text)_6%,transparent)] hover:text-[var(--c-text)]'
                      }`}
                    >
                      <span className="truncate">进入工作台</span>
                    </button>
                  ) : (
                    group.items.map((item) => {
                      const active =
                        activeSection === `domain:${group.domainKey}` && navActive.page === item.key
                      return (
                        <button
                          key={`${group.domainKey}:${item.key}`}
                          type="button"
                          onClick={() => handleDomainItemClick(group.domainKey, item.key)}
                          aria-current={active ? 'page' : undefined}
                          // 与主菜单完全同构：同样的 mx-3 + px-3 + 1.25rem 图标列 + gap-2 + py-2.5，
                          // 因此图标落在 22.5px、文字落在 48.75px、行高与行距都与主菜单一致。
                          // 字号同主菜单、字重 600；层级只靠灰字色与主菜单区分。
                          className={`sidebar-nav-row grid w-[calc(100%-1.5rem)] mx-3 grid-cols-[1.25rem_1fr] items-center gap-2 rounded-[var(--radius-control)] px-3 py-2.5 text-[length:var(--fs-sm)] font-semibold text-left transition-colors duration-150 cursor-pointer ${
                            active
                              ? 'is-active'
                              : 'text-[var(--c-text-secondary)] hover:bg-[color-mix(in_srgb,var(--c-text)_6%,transparent)] hover:text-[var(--c-text)]'
                          }`}
                        >
                          {item.icon ? (
                            // 业务域自配的图标图片：用 mask 渲染而非 <img>，
                            // 这样能吃掉 currentColor，普通/悬停/选中自动跟随文字变色，
                            // 也不必为明暗主题各准备一套图。16px 居中于 18.75px 图标列。
                            <span
                              aria-hidden="true"
                              className="h-[16px] w-[16px] justify-self-center bg-current"
                              style={{
                                WebkitMaskImage: `url("${item.icon}")`,
                                maskImage: `url("${item.icon}")`,
                                WebkitMaskRepeat: 'no-repeat',
                                maskRepeat: 'no-repeat',
                                WebkitMaskPosition: 'center',
                                maskPosition: 'center',
                                WebkitMaskSize: 'contain',
                                maskSize: 'contain',
                              }}
                            />
                          ) : (
                            <span aria-hidden="true" />
                          )}
                          <span className="truncate">{item.label}</span>
                        </button>
                      )
                    })
                  )}
                </Fragment>
              )
            })}
          </div>
        </>
      )}
    </nav>
  )
}
