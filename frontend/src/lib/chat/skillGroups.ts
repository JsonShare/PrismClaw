import type { SkillPickerItem } from '../../hooks/chat/useSkillList'

// ---------------------------------------------------------------------------
// 技能列表的分组与描述派生
//
// 技能与插件（专家）是同一份 /api/skills/list 数据：插件技能 id 形如
// `plugin:skill`，按冒号前缀聚合成"专家"；无冒号的独立技能单独成组。
// 技能选择器、专家选择器与 `/` 能力面板共用本模块，避免三处各写一套过滤。
// ---------------------------------------------------------------------------

export interface PluginGroup {
  name: string
  skills: SkillPickerItem[]
}

function pluginNameOf(id: string): string {
  const idx = id.indexOf(':')
  return idx > 0 ? id.slice(0, idx) : id
}

/** 插件副标题：子插件描述 → 包描述 → 首个技能描述 → 技能数。 */
export function pluginDescriptionOf(skills: SkillPickerItem[]): string {
  const pluginDesc = skills.find((s) => s.pluginDescription)?.pluginDescription
  if (pluginDesc) return pluginDesc
  const mpDesc = skills.find((s) => s.marketplaceDescription)?.marketplaceDescription
  if (mpDesc) return mpDesc
  const firstDesc = skills.find((s) => s.description)?.description
  if (firstDesc) return firstDesc
  return `${skills.length} 个技能`
}

function matchesSkill(skill: SkillPickerItem, query: string): boolean {
  if (!query) return true
  const q = query.toLowerCase()
  return (
    skill.name.toLowerCase().includes(q) ||
    (skill.nameZh ?? '').toLowerCase().includes(q) ||
    skill.description.toLowerCase().includes(q)
  )
}

/** 独立技能（id 不含插件前缀）。 */
export function standaloneSkills(skills: SkillPickerItem[], query: string): SkillPickerItem[] {
  return skills.filter((s) => !s.id.includes(':') && matchesSkill(s, query))
}

/** 插件技能按 id 前缀聚合；查询命中插件名或任一成员技能即保留。 */
export function pluginGroups(skills: SkillPickerItem[], query: string): PluginGroup[] {
  const map = new Map<string, SkillPickerItem[]>()
  for (const s of skills) {
    if (!s.id.includes(':')) continue
    const name = pluginNameOf(s.id)
    const bucket = map.get(name)
    if (bucket) bucket.push(s)
    else map.set(name, [s])
  }
  const q = query.trim().toLowerCase()
  return Array.from(map.entries())
    .map(([name, items]) => ({ name, skills: items }))
    .filter((g) => !q || g.name.toLowerCase().includes(q) || g.skills.some((s) => matchesSkill(s, q)))
    .sort((a, b) => a.name.localeCompare(b.name))
}
