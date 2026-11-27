import { http } from './request'

/** 技能选择器条目（后端 SkillListItem 的轻量子集） */
export interface SkillItem {
  id: string
  name: string
  nameZh: string | null
  description: string
  emoji: string | null
}

interface RawSkill {
  name?: unknown
  name_zh?: unknown
  description?: unknown
  emoji?: unknown
}

/**
 * 后端已按用户可见性过滤（排除 model_only / disabled），前端不再重复判断。
 * scope=all 才能取到专家包技能；默认 scope=assets 仅返回资产技能。
 */
export async function listSkills(): Promise<SkillItem[]> {
  const data = await http.get<RawSkill[]>('/api/skills/list?scope=all')
  if (!Array.isArray(data)) return []
  return data
    .filter((item) => item && typeof item.name === 'string')
    .map((item) => ({
      id: String(item.name),
      name: String(item.name),
      nameZh: typeof item.name_zh === 'string' && item.name_zh ? item.name_zh : null,
      description: typeof item.description === 'string' ? item.description : '',
      emoji: typeof item.emoji === 'string' ? item.emoji : null,
    }))
}

/** 展示名：优先中文名，其次去掉专家包命名空间后的技能名（`expert:skill` → `skill`） */
export function skillLabel(item: SkillItem): string {
  if (item.nameZh) return item.nameZh
  const idx = item.id.indexOf(':')
  return idx > 0 ? item.id.slice(idx + 1) : item.id
}
