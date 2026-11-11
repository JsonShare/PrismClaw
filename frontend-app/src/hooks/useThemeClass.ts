import { useAppStore } from '../stores/useAppStore'

/**
 * 把解析后的主题（light / dark）挂到页面根节点。
 * 页面根节点下的组件共享 --xxx 令牌，弹框与快捷导航同样生效。
 */
export function useThemeClass(base: string): string {
  const theme = useAppStore((s) => s.theme)
  const systemTheme = useAppStore((s) => s.systemTheme)
  const dark = (theme === 'system' ? systemTheme : theme) === 'dark'
  return dark ? `${base} theme-dark` : base
}
