/**
 * 医疗域 · 工作台导航配置
 *
 * 说明：新版临床工作台的导航为「侧栏分组 + 内部状态路由」，
 * 不依赖 react-router —— 域页面是作为单个组件懒加载进 ChatPage 的，
 * 无法挂载独立路由树。URL 仅作状态同步（可刷新保持、支持前进后退）。
 */

import {
  FileText,
  Users,
  BookOpen,
  ClipboardList,
  Pill,
  type LucideIcon,
} from 'lucide-react'

/** 工作台页面 key */
export type WorkbenchPageKey = 'records' | 'patients' | 'literature' | 'guidelines' | 'drugs'

/** 导航项 */
export interface NavItem {
  key: WorkbenchPageKey
  /** 侧栏短标签 */
  label: string
  icon: LucideIcon
}

/** 导航分组 */
export interface NavGroup {
  label: string
  items: NavItem[]
}

export const NAV_GROUPS: NavGroup[] = [
  {
    label: '临床业务',
    items: [
      { key: 'records', label: '智能病历', icon: FileText },
      { key: 'patients', label: '患者管理', icon: Users },
    ],
  },
  {
    label: '知识库',
    items: [
      { key: 'literature', label: '文献知识库', icon: BookOpen },
      { key: 'guidelines', label: '指南知识库', icon: ClipboardList },
      { key: 'drugs', label: '药品知识库', icon: Pill },
    ],
  },
]

/** 平铺后的导航项，便于按 key 查找 */
export const NAV_ITEMS: NavItem[] = NAV_GROUPS.flatMap((g) => g.items)

const PAGE_KEYS = new Set<string>(NAV_ITEMS.map((i) => i.key))

/** 判断字符串是否为合法的页面 key */
export function isPageKey(value: string | null): value is WorkbenchPageKey {
  return !!value && PAGE_KEYS.has(value)
}
