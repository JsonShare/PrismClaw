export interface Skill {
  id: string
  title: string
  desc: string
  tags: string[]
  installed: boolean
  origin?: 'system' | 'plaza' | 'workspace' | 'plugin' | 'project' | 'bundled' | 'personal' | 'managed' | 'mcp' | 'marketplace' | 'builtin'
  location?: string
  content?: string
  groupPath?: string[]
  trusted?: boolean
  groupLabel?: string
  category?: string | null
  marketplace_name?: string | null
  marketplace_description?: string | null
  plugin_description?: string | null
  /** 技能准入策略：auto(双通道)/user_only(仅用户指定)/model_only(仅模型)/disabled(禁用) */
  invocationPolicy?: 'auto' | 'user_only' | 'model_only' | 'disabled'
  /** 是否允许编辑（SKILL.md frontmatter editable: true 才为 true；默认 false 禁用） */
  editable?: boolean
}
