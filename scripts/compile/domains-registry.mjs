#!/usr/bin/env node
/**
 * domains-registry.mjs
 *
 * 从 scripts/compile/domains.yaml（唯一真实来源）生成三份产物：
 *
 * 1. 前端映射：frontend/src/domains/_lazy_map.generated.ts
 *    - DOMAIN_LAZY_MAP: key → React.lazy 工厂
 *    - DOMAIN_LABEL_MAP: key → 降级标签
 *    - DOMAIN_NAV_MAP: key → 侧栏导航项（标签 / 分组 / 顺序）
 *
 * 2. 后端 manifest 片段：各域 _manifest_generated.py
 *    - 每个域一个文件，bundle.py 的 get_manifest() 直接返回
 *
 * 3. Shell 变量：scripts/compile/out/.domains.env
 *    - PRISMCLAW_BACKEND_PATHS
 *    - 供 Makefile / Dockerfile / build-frontend.sh 使用
 *
 * 用法：
 *   node scripts/compile/domains-registry.mjs
 *   PRISMCLAW_DOMAINS=medical node scripts/compile/domains-registry.mjs
 */

import { readFileSync, writeFileSync, mkdirSync, existsSync, rmSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const PROJECT_ROOT = resolve(__dirname, '../..')

// ── YAML 解析 ──

function parseDomainsYaml(yamlPath) {
  const content = readFileSync(yamlPath, 'utf8')
  const domains = {}
  let currentKey = null
  let currentSection = null  // 'menus' | 'pages' | null
  let listItemIdx = 0

  for (const rawLine of content.split('\n')) {
    // 仅当 # 前为非空白（即注释起始）时才剥离；避免误删引号内的 #（如十六进制颜色 "#185FA5"）
    const line = rawLine.replace(/(?<!\S)#.*$/, '').trimEnd()
    if (!line.trim()) continue

    // domains: 顶层
    if (/^domains\s*:/.test(line)) {
      currentSection = null
      continue
    }

    // 域 key（2空格缩进）
    const domainMatch = line.match(/^  (\w+)\s*:/)
    if (domainMatch) {
      currentKey = domainMatch[1]
      domains[currentKey] = { menus: [], pages: [], tools: [], skills: [] }
      currentSection = null
      listItemIdx = 0
      continue
    }

    if (!currentKey || !domains[currentKey]) continue

    // menus: / pages: 标记
    const sectionMatch = line.match(/^    (menus|pages|tools|skills)\s*:/)
    if (sectionMatch) {
      currentSection = sectionMatch[1]
      listItemIdx = 0
      continue
    }

    // 列表项（6空格 + - ）
    const listMatch = line.match(/^      -\s+/)
    if (listMatch && currentSection) {
      const rest = line.slice(listMatch[0].length).trim()
      // 四种列表段（menus/pages/tools/skills）均 push 新 dict
      if (currentSection === 'menus' || currentSection === 'pages' ||
          currentSection === 'tools' || currentSection === 'skills') {
        domains[currentKey][currentSection].push({})
        listItemIdx = domains[currentKey][currentSection].length - 1
      }
      // 行内 key:value
      const kvMatch = rest.match(/^(\w+)\s*:\s*(.+)/)
      if (kvMatch) {
        const [, prop, value] = kvMatch
        domains[currentKey][currentSection][listItemIdx][prop] = parseValue(value)
      }
      continue
    }

    // 列表项续行（8空格 key:value）
    const contMatch = line.match(/^        (\w+)\s*:\s*(.+)/)
    if (contMatch && currentSection && listItemIdx >= 0) {
      const [, prop, value] = contMatch
      domains[currentKey][currentSection][listItemIdx][prop] = parseValue(value)
      continue
    }

    // 域属性（4空格缩进）
    const propMatch = line.match(/^    (\w+)\s*:\s*(.+)/)
    if (propMatch) {
      const [, prop, value] = propMatch
      if (prop !== 'menus' && prop !== 'pages') {
        domains[currentKey][prop] = parseValue(value)
      }
    }
  }

  return domains
}

function parseValue(raw) {
  const v = raw.trim()
  if (v === 'true') return true
  if (v === 'false') return false
  if (/^["']/.test(v)) return v.slice(1, -1)
  if (/^\d+$/.test(v)) return Number(v)
  return v
}

// ── 环境变量覆盖 ──

function applyEnvOverride(domains) {
  const envList = process.env.PRISMCLAW_DOMAINS
  if (!envList) return domains

  const enabledKeys = new Set(envList.split(',').map(k => k.trim()).filter(Boolean))
  console.log(`  PRISMCLAW_DOMAINS override: [${[...enabledKeys].join(', ')}]`)

  for (const [key, cfg] of Object.entries(domains)) {
    cfg.enabled = enabledKeys.has(key)
  }
  for (const key of enabledKeys) {
    if (!domains[key]) {
      console.warn(`  ⚠ 域 "${key}" 不在 domains.yaml 中，跳过`)
    }
  }
  return domains
}

// ── 生成前端 _lazy_map.generated.ts ──

function generateLazyMapTs(enabledDomains) {
  const OBJ_DEFAULT = '{ default:'
  const OBJ_DEFAULT_CT = '{ default: ComponentType }'

  // 域菜单图标：资源归属业务域（subpackages/<域>/...），构建期以 ?url 引入并哈希。
  // 只读 icon_asset（前端资源），icon 是写入后端 manifest 的逻辑名，两者互不替代。
  // 不放 core/public —— 那是核心目录，按域名组织会形成核心对业务域的反向依赖。
  const navIconImports = []
  const navIconVar = new Map()
  enabledDomains.forEach((d, di) => {
    d.menus.forEach((m, mi) => {
      if (!m.icon_asset) return
      const name = `_navIcon${di}_${mi}`
      navIconImports.push(`import ${name} from '@domains/${d.key}/${m.icon_asset}?url'`)
      navIconVar.set(`${d.key}:${m.key}`, name)
    })
  })

  const lines = [
    '/**',
    ' * 自动生成 — 由 scripts/compile/domains-registry.mjs 从 domains.yaml 生成',
    ` * 启用域: [${enabledDomains.map(d => d.key).join(', ')}]`,
    ' * ⚠ 请勿手动修改！',
    ' */',
    '',
    "import type { ComponentType } from 'react'",
    ...(navIconImports.length ? ['', ...navIconImports] : []),
    '',
    '// React.lazy 工厂类型（抽成别名避免 IDE 语言注入误报解构）',
    'type DomainLazyFactory = () => Promise<' + OBJ_DEFAULT_CT + '>',
    '',
    '// 域懒加载映射 — key → React.lazy 工厂',
    'export const DOMAIN_LAZY_MAP: Record<string, DomainLazyFactory> = {',
  ]
  for (const d of enabledDomains) {
    lines.push("  " + d.key + ": () => import('" + d.lazy_import + "').then(m => (" + OBJ_DEFAULT + " m." + d.lazy_export + " })),")
  }
  lines.push('}')
  lines.push('')

  // 降级标签
  lines.push('// 域降级标签 — 前端离线场景的标签备用')
  lines.push('export const DOMAIN_LABEL_MAP: Record<string, string> = {')
  for (const d of enabledDomains) {
    lines.push(`  ${d.key}: '${d.label}',`)
  }
  lines.push('}')
  lines.push('')

  // 域左侧栏导航项 —— 分组标签 + 顺序 + 图标均来自 domains.yaml，核心侧栏据此平铺渲染
  lines.push('// 域左侧栏导航项 — 顺序 / 分组 / 图标来自 domains.yaml（核心侧栏平铺渲染）')
  lines.push('export const DOMAIN_NAV_MAP: Record<string, { key: string; label: string; group: string; icon: string }[]> = {')
  for (const d of enabledDomains) {
    lines.push(`  ${d.key}: [`)
    for (const m of d.menus) {
      const iconExpr = navIconVar.get(`${d.key}:${m.key}`) || "''"
      lines.push(`    { group: '${m.group || ''}', icon: ${iconExpr}, key: '${m.key}', label: '${m.label}' },`)
    }
    lines.push('  ],')
  }
  lines.push('}')
  lines.push('')

  return lines.join('\n')
}

// ── 生成后端 _manifest.generated.py ──

function generateManifestPy(domain) {
  const menusLines = domain.menus.map(m =>
    `        MenuEntry(key="${m.key}", label="${m.label}", icon="${m.icon || ''}", parent="${m.parent || ''}", group="${m.group || ''}"),`
  )
  const pagesLines = domain.pages.map(p =>
    `        PageEntry(key="${p.key}", path="${p.path}", js_asset="${p.js_asset || ''}", css_asset="${p.css_asset || ''}"),`
  )

  return [
    '"""自动生成 — 由 scripts/compile/domains-registry.mjs 从 domains.yaml 生成。',
    `域: ${domain.key} | ⚠ 请勿手动修改！"""`,
    '',
    'from prismclaw.schema.services.domain import DomainManifest, MenuEntry, PageEntry',
    '',
    'MANIFEST = DomainManifest(',
    `    key="${domain.key}",`,
    `    label="${domain.label}",`,
    `    icon="${domain.icon || ''}",`,
    `    path="${domain.path || ''}",`,
    `    menus=[`,
    ...menusLines,
    '    ],',
    `    pages=[`,
    ...pagesLines,
    '    ],',
    ')',
    '',
  ].join('\n')
}

// ── 主流程 ──

function main() {
  const yamlPath = resolve(__dirname, 'domains.yaml')

  console.log('🔍 Reading domain config:', yamlPath)
  if (!existsSync(yamlPath)) {
    console.error('✗ scripts/compile/domains.yaml not found')
    process.exit(1)
  }

  let domains = parseDomainsYaml(yamlPath)
  domains = applyEnvOverride(domains)

  const enabledDomains = Object.entries(domains)
    .filter(([, cfg]) => cfg.enabled)
    .map(([key, cfg]) => ({ key, ...cfg }))

  console.log(`✓ Enabled domains: [${enabledDomains.map(d => d.key).join(', ')}]`)

  // 校验前端分包目录 + 域 pyproject.toml
  for (const d of enabledDomains) {
    const fePath = resolve(PROJECT_ROOT, 'frontend', d.frontend_path || `subpackages/${d.key}`)
    if (!existsSync(fePath)) {
      console.warn(`  ⚠ 域 "${d.key}" 前端分包不存在: ${fePath}`)
    }
    const backendDir = resolve(PROJECT_ROOT, d.backend_path || `backend/src/prismclaw_${d.key}`)
    const pyprojectPath = resolve(backendDir, 'pyproject.toml')
    if (!existsSync(pyprojectPath)) {
      console.warn(`  ⚠ 域 "${d.key}" 缺少 pyproject.toml: ${pyprojectPath}`)
      console.warn(`    域必须有自己的 pyproject.toml 声明 [project.entry-points."prismclaw.domains"]`)
    }
  }

  // ── 产物 1: 前端懒加载映射 ──
  const tsOutputPath = resolve(PROJECT_ROOT, 'frontend/src/domains/_lazy_map.generated.ts')
  mkdirSync(dirname(tsOutputPath), { recursive: true })
  writeFileSync(tsOutputPath, generateLazyMapTs(enabledDomains), 'utf8')
  console.log('✓ [前端] Generated:', tsOutputPath)

  // ── 产物 2: 后端 manifest 片段（每个域一个文件） ──
  for (const d of enabledDomains) {
    const backendDir = resolve(PROJECT_ROOT, d.backend_path)
    if (!existsSync(backendDir)) {
      console.warn(`  ⚠ 域 "${d.key}" 后端目录不存在: ${backendDir}，跳过 manifest 生成`)
      continue
    }
    const pyOutputPath = resolve(backendDir, '_manifest_generated.py')
    writeFileSync(pyOutputPath, generateManifestPy(d), 'utf8')
    console.log(`✓ [后端] Generated: ${d.key}/_manifest_generated.py`)
  }

  // ── 产物 2b: 清理禁用域的残留 manifest（只写不删会让禁用域借尸还魂） ──
  for (const [key, cfg] of Object.entries(domains)) {
    if (cfg.enabled) continue
    const backendDir = resolve(PROJECT_ROOT, cfg.backend_path || `backend/src/prismclaw_${key}`)
    const staleManifest = resolve(backendDir, '_manifest_generated.py')
    if (existsSync(staleManifest)) {
      rmSync(staleManifest)
      console.log(`✓ [后端] Removed stale manifest: ${key}/_manifest_generated.py`)
    }
  }

  // ── 产物 3: Shell 变量 ──
  const backendPaths = enabledDomains
    .map(d => d.backend_path || `backend/src/prismclaw_${d.key}`)
  const envPath = resolve(__dirname, 'out/.domains.env')
  mkdirSync(dirname(envPath), { recursive: true })
  const envLines = [
    '# 自动生成 — 由 scripts/compile/domains-registry.mjs 生成',
    `PRISMCLAW_BACKEND_PATHS="${backendPaths.join(',')}"`,
  ]
  writeFileSync(envPath, envLines.join('\n') + '\n', 'utf8')
  console.log('✓ [部署] Generated:', envPath)

  console.log(`\n  ${enabledDomains.length} domain(s) → 3 categories of artifacts generated`)
}

main()