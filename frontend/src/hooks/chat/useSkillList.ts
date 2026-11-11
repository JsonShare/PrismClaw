import { useCallback, useEffect, useRef, useState } from 'react'
import { apiUrl } from '../../lib/system-adapter'
import { useAuth } from '../../context/AuthContext'

/** 技能选择器条目（后端 SkillListItem 的轻量子集） */
export interface SkillPickerItem {
  id: string
  name: string
  /** 中文显示名（后端 name_zh）；缺失时展示回退到 name */
  nameZh: string | null
  description: string
  emoji: string | null
  invocationPolicy: string
  /** 插件（marketplace）名：插件来源技能才有，用于聚合与描述展示 */
  marketplaceName: string | null
  /** 专家插件包级描述（marketplace.json description），所有子插件共享 */
  marketplaceDescription: string | null
  /** 子插件级描述（子插件自身 manifest description），每个子插件独立 */
  pluginDescription: string | null
}

/**
 * 获取用户可见技能列表（排除 model_only 和 disabled）。
 * 用于聊天输入框的技能选择器。
 */
export function useSkillList() {
  const { token } = useAuth()
  const [skills, setSkills] = useState<SkillPickerItem[]>([])
  const [loading, setLoading] = useState(false)
  const seqRef = useRef(0)

  const refresh = useCallback(async () => {
    if (!token) return
    const seq = (seqRef.current += 1)
    setLoading(true)
    try {
      const res = await fetch(apiUrl('/api/skills/list'), {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      if (seq !== seqRef.current) return
      if (!Array.isArray(data)) { setSkills([]); return }
      const filtered: SkillPickerItem[] = data
        .filter((s: any) => {
          if (!s || typeof s.name !== 'string') return false
          const policy = s.invocation_policy || 'auto'
          // model_only 和 disabled 技能不出现在选择器中
          return policy !== 'model_only' && policy !== 'disabled'
        })
        .map((s: any) => ({
          id: s.name,
          name: s.name,
          nameZh: typeof s.name_zh === 'string' && s.name_zh ? s.name_zh : null,
          description: typeof s.description === 'string' ? s.description : '',
          emoji: typeof s.emoji === 'string' ? s.emoji : null,
          invocationPolicy: s.invocation_policy || 'auto',
          marketplaceName: typeof s.marketplace_name === 'string' ? s.marketplace_name : null,
          marketplaceDescription: typeof s.marketplace_description === 'string' ? s.marketplace_description : null,
          pluginDescription: typeof s.plugin_description === 'string' ? s.plugin_description : null,
        }))
      setSkills(filtered)
    } catch {
      setSkills([])
    } finally {
      if (seq === seqRef.current) setLoading(false)
    }
  }, [token])

  useEffect(() => { refresh() }, [refresh])

  return { skills, loading, refresh }
}
