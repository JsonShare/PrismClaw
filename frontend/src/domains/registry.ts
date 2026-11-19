/**
 * 域 Bundle 注册表 — 后端驱动的域发现 + React.lazy 按需加载
 *
 * 数据分工（三者互不重复）：
 *   - 侧栏导航项（顺序 / 分组 / 标签）：构建期由 domains.yaml 生成 DOMAIN_NAV_MAP
 *   - 启用域集合与域身份（key / label）：运行时 GET /api/domains/manifests
 *   - 页面组件：React.lazy 按需加载，Vite 自动代码分割
 */

import { lazy, type ComponentType } from 'react'

import { apiUrl } from '../lib/system-adapter'
import { DOMAIN_LAZY_MAP, DOMAIN_LABEL_MAP, DOMAIN_NAV_MAP } from './_lazy_map.generated'

/* ─── 类型定义 ─── */

/** 域 identity（前端只消费 key / label 两项，其余字段由后端协议保留） */
export interface DomainManifest {
  key: string
  label: string
}

/** 域注册条目 */
export interface DomainEntry {
  manifest: DomainManifest
  /**
   * 工作台页面组件（React.lazy 包裹，需配合 Suspense 使用）。
   * 由系统侧栏驱动页面切换，组件通过 page / pageNonce 接收跳转意图。
   */
  WorkbenchPage: React.LazyExoticComponent<ComponentType<WorkbenchPageProps>>
}

/** 工作台页面的受控 props：由 DomainSection 从 nav-bus 的下行意图透传 */
export interface WorkbenchPageProps {
  /** 侧栏选中的页面 key；undefined 表示由域自行决定初始页（URL / 默认页） */
  page?: string
  /** 跳转意图序号：重复点击同一子项时自增，域据此可「重置到该页顶层」 */
  pageNonce?: number
}

/** 域的侧栏导航项（顺序 / 标签 / 分组 / 图标均来自 domains.yaml，经构建脚本生成） */
export interface DomainNavItem {
  key: string
  label: string
  group: string
  /** lucide 图标名（kebab-case）；空串表示只显示文字 */
  icon: string
}

/* ─── 注册表 ─── */

const registry = new Map<string, DomainEntry>()

/**
 * 注册表版本号 —— 供 useSyncExternalStore 订阅。
 * 域是异步发现的（App 启动后拉 /api/domains/manifests），
 * 不订阅的话侧栏首次渲染拿到空表就再也不会更新。
 */
let registryVersion = 0
const registryListeners = new Set<() => void>()

function bumpRegistryVersion(): void {
  registryVersion += 1
  for (const fn of registryListeners) fn()
}

export function subscribeRegistryVersion(listener: () => void): () => void {
  registryListeners.add(listener)
  return () => {
    registryListeners.delete(listener)
  }
}

export function getRegistryVersion(): number {
  return registryVersion
}

/** 注册一个域 */
export function registerDomain(entry: DomainEntry): void {
  registry.set(entry.manifest.key, entry)
  bumpRegistryVersion()
}

/** 获取所有已注册域 */
export function getRegisteredDomains(): DomainEntry[] {
  return Array.from(registry.values())
}

/** 按域 key 获取注册条目 */
export function getDomainEntry(key: string): DomainEntry | undefined {
  return registry.get(key)
}

/** 取某域的侧栏导航项（构建期生成；域未在 domains.yaml 声明时为空） */
export function getDomainNavItems(domainKey: string): DomainNavItem[] {
  return DOMAIN_NAV_MAP[domainKey] ?? []
}

/**
 * 域在侧栏中的展示顺序 —— 取构建期生成表的键序（= domains.yaml 声明顺序）。
 * 不能依赖 getRegisteredDomains() 的顺序：它来自后端 /api/domains/manifests
 * 返回的 bundle 顺序，bundle 注册顺序变化会悄悄改变侧栏排列。
 */
export function getDomainOrder(): string[] {
  return Object.keys(DOMAIN_NAV_MAP)
}

/* ─── 后端驱动的域发现 ─── */

/** 从后端 API 拉取域 identity 并注册到本地 */
export async function discoverAndRegisterDomains(): Promise<void> {
  let manifests: DomainManifest[] = []

  try {
    const res = await fetch(apiUrl('/api/domains/manifests'))
    if (res.ok) {
      manifests = await res.json()
    }
  } catch {
    // 后端不可达时降级为全量本地注册
  }

  // 后端返回了 manifests → 仅注册后端启用且前端已编译的域
  if (manifests.length > 0) {
    for (const manifest of manifests) {
      const lazyFactory = DOMAIN_LAZY_MAP[manifest.key]
      if (!lazyFactory) continue // 前端未编译该域（被裁剪），跳过
      registerDomain({
        manifest: { key: manifest.key, label: manifest.label },
        WorkbenchPage: lazy(lazyFactory),
      })
    }
    return
  }

  // 降级：后端未返回 → 全量本地注册（开发模式 / 离线场景）
  for (const [key, lazyFactory] of Object.entries(DOMAIN_LAZY_MAP)) {
    registerDomain({
      manifest: { key, label: DOMAIN_LABEL_MAP[key] || key },
      WorkbenchPage: lazy(lazyFactory),
    })
  }
}
