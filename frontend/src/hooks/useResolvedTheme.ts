import { useSyncExternalStore } from 'react'

/** 读取当前实际主题（解析 'system' → 'light' | 'dark'），并随 DOM 变化响应 */
export function useResolvedTheme(): 'light' | 'dark' {
  return useSyncExternalStore(subscribe, getSnapshot)
}

function getSnapshot(): 'light' | 'dark' {
  if (typeof document === 'undefined') return 'light'
  return (document.documentElement.dataset.theme as 'light' | 'dark') || 'light'
}

function subscribe(callback: () => void): () => void {
  const observer = new MutationObserver(callback)
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['data-theme'],
  })
  return () => observer.disconnect()
}