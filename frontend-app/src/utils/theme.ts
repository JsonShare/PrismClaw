import Taro from '@tarojs/taro'

export type Theme = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

/** 与 PC 端 frontend/src/stores/ui.ts 使用同一个存储键 */
export const THEME_KEY = 'prismclaw-theme'

export const THEME_OPTIONS: { label: string; value: Theme; hint: string }[] = [
  { label: '浅色', value: 'light', hint: '始终使用亮色界面' },
  { label: '深色', value: 'dark', hint: '始终使用暗色界面' },
  { label: '跟随系统', value: 'system', hint: '随系统外观自动切换' }
]

export function themeLabel(theme: Theme): string {
  const option = THEME_OPTIONS.find((o) => o.value === theme)
  return option ? option.label : '跟随系统'
}

export function readStoredTheme(): Theme {
  try {
    const saved = Taro.getStorageSync(THEME_KEY)
    if (saved === 'light' || saved === 'dark' || saved === 'system') return saved
  } catch {
    // 存储不可用时回落到跟随系统
  }
  return 'system'
}

export function systemTheme(): ResolvedTheme {
  if (process.env.TARO_ENV === 'h5' && typeof window !== 'undefined' && window.matchMedia) {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  }
  try {
    return Taro.getSystemInfoSync().theme === 'dark' ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

export function resolveTheme(theme: Theme, system: ResolvedTheme): ResolvedTheme {
  return theme === 'system' ? system : theme
}

/** H5 下同步到 documentElement，保证页面外的背景与滚动区域一致 */
export function applyThemeToDOM(resolved: ResolvedTheme) {
  if (process.env.TARO_ENV === 'h5' && typeof document !== 'undefined') {
    document.documentElement.dataset.theme = resolved
  }
}
