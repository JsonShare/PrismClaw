import { http } from './request'

/** 技能选择器条目（后端 SkillListItem 的轻量子集） */
export interface SkillItem {
  id: string
  name: string
  nameZh: string | null
  description: string
  emoji: string | null
  marketplaceDescription: string | null
  pluginDescription: string | null
}

export interface ExpertGroup {
  name: string
  description: string
  skills: SkillItem[]
}

interface RawSkill {
  name?: unknown
  name_zh?: unknown
  description?: unknown
  emoji?: unknown
  marketplace_name?: unknown
  marketplace_description?: unknown
  plugin_description?: unknown
  invocation_policy?: unknown
}

const policyOf = (raw: RawSkill) =>
  typeof raw.invocation_policy === 'string' ? raw.invocation_policy : 'auto'

/** 与 PC 端一致：model_only / disabled 技能不出现在选择器中 */
export async function listSkills(): Promise<SkillItem[]> {
  const data = await http.get<RawSkill[]>('/api/skills/list')
  if (!Array.isArray(data)) return []
  return data
    .filter((item) => item && typeof item.name === 'string')
    .filter((item) => {
      const policy = policyOf(item)
      return policy !== 'model_only' && policy !== 'disabled'
    })
    .map((item) => ({
      id: String(item.name),
      name: String(item.name),
      nameZh: typeof item.name_zh === 'string' && item.name_zh ? item.name_zh : null,
      description: typeof item.description === 'string' ? item.description : '',
      emoji: typeof item.emoji === 'string' ? item.emoji : null,
      marketplaceDescription:
        typeof item.marketplace_description === 'string' ? item.marketplace_description : null,
      pluginDescription:
        typeof item.plugin_description === 'string' ? item.plugin_description : null,
    }))
}

export function skillLabel(item: SkillItem): string {
  return item.nameZh || item.name
}

/** 独立技能（id 不含插件前缀），用于「技能」选择器 */
export function standaloneSkills(skills: SkillItem[]): SkillItem[] {
  return skills.filter((item) => !item.id.includes(':'))
}

function pluginNameOf(id: string): string {
  const idx = id.indexOf(':')
  return idx > 0 ? id.slice(0, idx) : id
}

/** 插件技能按 id 前缀聚合成「专家」，用于「专家」选择器 */
export function expertGroups(skills: SkillItem[]): ExpertGroup[] {
  const map = new Map<string, SkillItem[]>()
  for (const item of skills) {
    if (!item.id.includes(':')) continue
    const name = pluginNameOf(item.id)
    const bucket = map.get(name)
    if (bucket) bucket.push(item)
    else map.set(name, [item])
  }
  return Array.from(map.entries())
    .map(([name, items]) => ({
      name,
      description:
        items.find((item) => item.pluginDescription)?.pluginDescription ||
        items.find((item) => item.marketplaceDescription)?.marketplaceDescription ||
        items.find((item) => item.description)?.description ||
        `${items.length} 个技能`,
      skills: items,
    }))
    .sort((a, b) => a.name.localeCompare(b.name))
}
