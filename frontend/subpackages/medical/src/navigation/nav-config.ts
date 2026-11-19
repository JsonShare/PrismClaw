/**
 * 医疗域 · 工作台页面 key 白名单
 *
 * 二级菜单的标签与顺序已迁到 scripts/compile/domains.yaml（由核心侧栏平铺渲染），
 * 这里只保留页面 key 的独立校验，作为域内 URL / 外部跳转的合法性防线。
 */

/** 工作台页面 key */
export type WorkbenchPageKey = 'records' | 'patients' | 'literature' | 'guidelines' | 'drugs'

const PAGE_KEYS = new Set<string>([
  'records',
  'patients',
  'literature',
  'guidelines',
  'drugs',
])

/** 判断字符串是否为合法的页面 key */
export function isPageKey(value: string | null): value is WorkbenchPageKey {
  return !!value && PAGE_KEYS.has(value)
}
