import Taro from '@tarojs/taro'
import { PropsWithChildren, useEffect } from 'react'

import './app.scss'
import { useAppStore } from './stores/useAppStore'
import { useAuthStore } from './stores/useAuthStore'
import { applyThemeToDOM, systemTheme } from './utils/theme'

function currentRoute(): string {
  const pages = Taro.getCurrentPages()
  return pages[pages.length - 1]?.route || ''
}

function App({ children }: PropsWithChildren<any>) {
  const theme = useAppStore((s) => s.theme)
  const system = useAppStore((s) => s.systemTheme)
  const setSystemTheme = useAppStore((s) => s.setSystemTheme)
  const loadWorkspaces = useAppStore((s) => s.loadWorkspaces)
  const bootstrap = useAuthStore((s) => s.bootstrap)

  // 主题偏好或系统外观变化时同步到 H5 根节点
  useEffect(() => {
    applyThemeToDOM(theme === 'system' ? system : theme)
  }, [theme, system])

  // 默认跟随系统：监听系统外观变化
  useEffect(() => {
    if (process.env.TARO_ENV === 'h5' && typeof window !== 'undefined' && window.matchMedia) {
      const mq = window.matchMedia('(prefers-color-scheme: dark)')
      const onChange = () => setSystemTheme(mq.matches ? 'dark' : 'light')
      onChange()
      if (mq.addEventListener) mq.addEventListener('change', onChange)
      else mq.addListener(onChange)
      return () => {
        if (mq.removeEventListener) mq.removeEventListener('change', onChange)
        else mq.removeListener(onChange)
      }
    }
    setSystemTheme(systemTheme())
    Taro.onThemeChange((res) => setSystemTheme(res.theme === 'dark' ? 'dark' : 'light'))
  }, [setSystemTheme])

  // 启动引导：恢复登录态 → 拉取工作区；未登录统一落到登录页
  useEffect(() => {
    bootstrap().then((ok) => {
      if (!ok) {
        if (currentRoute() !== 'pages/login/index') {
          Taro.reLaunch({ url: '/pages/login/index' })
        }
        return
      }
      if (currentRoute() === 'pages/login/index') {
        Taro.reLaunch({ url: '/pages/home/index' })
        return
      }
      loadWorkspaces().catch(() => undefined)
    })
  }, [bootstrap, loadWorkspaces])

  return children
}

export default App
