/**
 * 域 Bundle 注册表 — 后端驱动的域发现 + React.lazy 按需加载
 *
 * 架构设计：
 * 1. 构建时 domains-registry.mjs 根据 scripts/compile/domains.yaml
 *    生成 _lazy_map.generated.ts（只包含启用的域），实现编译时裁剪
 * 2. 后端 `GET /api/domains/manifests` 是域的运行时真实来源
 * 3. 前端启动时拉取 manifests，动态匹配已编译的域模块
 * 4. 域页面组件使用 React.lazy() 按需加载，Vite 自动代码分割
 */

import { lazy, type ComponentType } from 'react'
import { apiUrl } from '../lib/system-adapter'

// 从构建时生成的映射文件导入（只包含 enabled 的域）
import {
  DOMAIN_LAZY_MAP,
  DOMAIN_FALLBACK_ICON_MAP,
  DOMAIN_LABEL_MAP,
  DOMAIN_SUBTITLE_MAP,
  DOMAIN_ACCENT_MAP,
} from './_lazy_map.generated'

/* ─── 类型定义（与后端 DomainManifest 对应） ─── */

/** 域菜单项 */
export interface DomainMenuItem {
  key: string
  label: string
  icon: string
  path?: string
  parent?: string
}

/** 域页面项 */
export interface DomainPageEntry {
  key: string
  path: string
  js_asset?: string
  css_asset?: string
}

/** 域 manifest（后端返回的完整结构） */
export interface DomainManifest {
  key: string
  label: string
  icon: string
  path?: string
  menus: DomainMenuItem[]
  pages?: DomainPageEntry[]
  /** 副标题 — 来自业务域自己的描述（侧边栏副标题展示），核心系统不硬编码 */
  subtitle?: string
  /** 强调色（亮色主题）— 来自业务域自己的描述（侧边栏强调条 / 图标描边） */
  accent?: string
  /** 强调色（暗色主题）— 来自业务域自己的描述 */
  accentDark?: string
  /** 强调色（浅色模式图标描边用的可读色）— 仅当 accent 在浅色下过浅时提供 */
  accentDeep?: string
}

/** 域注册条目 */
export interface DomainEntry {
  manifest: DomainManifest
  /** 工作台页面组件（React.lazy 包裹，需配合 Suspense 使用） */
  WorkbenchPage: React.LazyExoticComponent<ComponentType>
  /** 独立功能页面组件映射（menus <= 1 时使用） */
  PageComponent?: React.LazyExoticComponent<ComponentType>
}

/* ─── 注册表 ─── */

const registry = new Map<string, DomainEntry>()

/** 注册一个域 */
export function registerDomain(entry: DomainEntry): void {
  registry.set(entry.manifest.key, entry)
}

/** 获取所有已注册域 */
export function getRegisteredDomains(): DomainEntry[] {
  return Array.from(registry.values())
}

/** 按域 key 获取注册条目 */
export function getDomainEntry(key: string): DomainEntry | undefined {
  return registry.get(key)
}

/** 判断 manifest 是否为工作台模式 */
export function isWorkbenchMode(manifest: DomainManifest): boolean {
  return manifest.menus.length >= 2
}

/** 清空注册表（用于重新发现时） */
export function clearRegistry(): void {
  registry.clear()
}

/* ─── 描述性元信息合并（核心零耦合）─── */

/**
 * 将业务域自带的描述性元信息（副标题 / 强调色）合并进 manifest。
 * 数据全部来自业务域自己在 domains.yaml 中的描述（经构建脚本生成为通用映射），
 * 核心系统只做 UI 展示，不硬编码任何域特例。
 * 若 manifest 自身已携带（后端下发），优先使用 manifest 原值。
 */
function withDomainMeta(manifest: DomainManifest): DomainManifest {
  const hasSubtitle = Object.prototype.hasOwnProperty.call(DOMAIN_SUBTITLE_MAP, manifest.key)
  const hasAccent = Object.prototype.hasOwnProperty.call(DOMAIN_ACCENT_MAP, manifest.key)
  if (!hasSubtitle && !hasAccent) return manifest
  return {
    ...manifest,
    subtitle: manifest.subtitle ?? (hasSubtitle ? DOMAIN_SUBTITLE_MAP[manifest.key] : undefined),
    accent: manifest.accent ?? (hasAccent ? DOMAIN_ACCENT_MAP[manifest.key].accent : undefined),
    accentDark:
      manifest.accentDark ?? (hasAccent ? DOMAIN_ACCENT_MAP[manifest.key].accentDark : undefined),
    accentDeep:
      manifest.accentDeep ?? (hasAccent ? DOMAIN_ACCENT_MAP[manifest.key].accentDeep : undefined),
  }
}

/* ─── 后端驱动的域发现 ─── */

/** 从后端 API 拉取域 manifests 并注册到本地 */
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
      if (!lazyFactory) continue  // 前端未编译该域（被裁剪），跳过

      const enrichedManifest: DomainManifest = withDomainMeta({
        ...manifest,
        icon: manifest.icon || DOMAIN_FALLBACK_ICON_MAP[manifest.key] || '',
        path: manifest.path || `/${manifest.key}`,
      })

      registerDomain({
        manifest: enrichedManifest,
        WorkbenchPage: lazy(lazyFactory),
      })
    }
    return
  }

  // 降级：后端未返回 → 全量本地注册（开发模式 / 离线场景）
  for (const [key, lazyFactory] of Object.entries(DOMAIN_LAZY_MAP)) {
    registerDomain({
      manifest: withDomainMeta({
        key,
        label: DOMAIN_LABEL_MAP[key] || key,
        icon: DOMAIN_FALLBACK_ICON_MAP[key] || '',
        path: `/${key}`,
        menus: [],
      }),
      WorkbenchPage: lazy(lazyFactory),
    })
  }
}
