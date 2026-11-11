/**
 * 医疗域 · 工作台侧栏（二级菜单）
 *
 * 视觉设计约定：
 *   · 容器：透明内容层 + 右侧 1px 发丝边，自身铺画布色（方案 A：内容区保持宿主白底）
 *   · 品牌区：18px/600 标题 + 8px 强调色圆点
 *   · 分组标签：12px/500，无装饰横线
 *   · 导航项：13px，内距 / 圆角 / 图标间距按 medical.css 的 .mw-nav* 定义
 *   · 交互：hover = --c-surface-3；激活 = 白底浮起 + 600 字重 + 左侧 2px 强调色竖条
 *
 * 全部几何值在 `medical.css` 的 `.mw-nav*` 里，本文件不写 px 字面量。
 * 点击由外层路由驱动，本组件不持有导航状态。
 */

import { NAV_GROUPS, type WorkbenchPageKey } from '../navigation/nav-config'

interface WorkbenchSidebarProps {
  /** 当前页面 key */
  active: WorkbenchPageKey
  onNavigate: (page: WorkbenchPageKey) => void
}

export function WorkbenchSidebar({ active, onNavigate }: WorkbenchSidebarProps) {
  return (
    <nav className="mw-nav mw-scroll">
      <div className="mw-nav-title">
        <i />
        临床工作台
      </div>

      {NAV_GROUPS.map((group) => (
        <div key={group.label} className="mw-nav-group">
          <div className="mw-nav-group-label">{group.label}</div>
          {group.items.map((item) => {
            const Icon = item.icon
            const isActive = active === item.key
            return (
              <button
                key={item.key}
                type="button"
                aria-current={isActive ? 'page' : undefined}
                className={'mw-nav-item' + (isActive ? ' on' : '')}
                onClick={() => onNavigate(item.key)}
              >
                <Icon />
                {item.label}
                {isActive && <span className="rail" />}
              </button>
            )
          })}
        </div>
      ))}
    </nav>
  )
}
