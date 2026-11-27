export interface Skill {
  id: string
  title: string
  desc: string
  tags: string[]
  installed: boolean
  origin?: 'system' | 'plaza' | 'workspace' | 'expert' | 'project' | 'bundled' | 'personal' | 'managed' | 'mcp' | 'marketplace' | 'builtin'
  location?: string
  content?: string
  groupPath?: string[]
  groupLabel?: string
  category?: string | null
  expert_name?: string | null
  expert_description?: string | null
  /** 是否允许编辑（SKILL.md frontmatter editable: true 才为 true；默认 false 禁用） */
  editable?: boolean
}
