import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Search, ChevronLeft, ChevronRight, Check,
  Store, Headphones, Award, FileText,
  PenLine, Clapperboard, Rocket, Presentation, CalendarRange, Wallet, Film,
  Plane, GraduationCap, BarChart3, ShoppingBag, ShieldCheck, Target, BookOpen, Bot,
  Cpu, Database, FlaskConical, Lock, Puzzle,
  type LucideIcon,
} from 'lucide-react'
import SkillDetailModal from '../../../components/modals/SkillDetailModal'
import { RefreshIcon } from '../../../components/icons/SystemIcons'
import { apiUrl, authedUrl } from '../../../lib/system-adapter'
import { useAuth } from '../../../context/AuthContext'
import { useUiStore } from '../../../stores/ui'
import type { Skill } from '../../../types/skill'

interface ServerSkillItem {
  name: string
  description?: string
  emoji?: string | null
  source?: string | null
  origin?: string | null
  location?: string | null
  group_path?: string[] | null
  group_label?: string | null
  category?: string | null
  editable?: boolean | null
  expert_name?: string | null
  expert_description?: string | null
  /** 所属专家包类型：agent=专家、team=专家团 */
  expert_type?: string | null
}

/** 面板内使用的技能条目：额外携带所属专家包类型，用于「专家 / 专家团」分栏。 */
type PanelSkill = Skill & { expertType: 'agent' | 'team' | null }

/**
 * 推导技能所属专家名。
 * 后端对 expert 来源技能的 group_path 恒为 [expert_name]（origin.py classify_skill_group_path），
 * 技能 name 也是 `expert:skill` 命名空间格式（extension_manager _namespaced_capability_name），
 * 故优先取 groupPath[0]，兜底取 id 冒号前缀。
 */
function pluginNameOf(skill: Pick<Skill, 'id' | 'groupPath' | 'expert_name'>): string {
  if (skill.expert_name?.trim()) return skill.expert_name.trim()
  const gp = skill.groupPath?.[0]
  if (gp && gp.trim()) return gp.trim()
  const idx = skill.id.indexOf(':')
  return idx > 0 ? skill.id.slice(0, idx) : skill.id
}

/** 去掉专家命名空间前缀，返回技能基础名（expert:skill → skill）。 */
function skillBaseName(skill: Skill): string {
  const idx = skill.id.indexOf(':')
  return idx > 0 ? skill.id.slice(idx + 1) : skill.id
}

/** 将插件 ID 格式化为可读标题："my-plugin" → "My Plugin" */
function formatPluginName(name: string): string {
  return name
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(' ')
}

/**
 * 专家（插件）卡片缩略图标：按插件 ID + 描述派生。
 * 只使用与领域无关的能力语义；无匹配时回落 Puzzle。
 */
const EXPERT_ICON_RULES: Array<[RegExp, LucideIcon]> = [
  [/(agent|assistant|助手|智能体|人工智能)/, Cpu],
  [/(data|database|数据|信息)/, Database],
  [/(research|science|科研|研究)/, FlaskConical],
  [/(security|privacy|compliance|安全|隐私|合规)/, Lock],
  [/(analysis|analy|分析)/, BarChart3],
  [/(document|report|文档|报告)/, FileText],
]

function expertVisualFor(name: string, desc: string): { Icon: LucideIcon } {
  const t = (name + ' ' + desc).toLowerCase()
  for (const [re, Icon] of EXPERT_ICON_RULES) {
    if (re.test(t)) return { Icon }
  }
  return { Icon: Puzzle }
}

/**
 * 图标配色：同一语义图标保持稳定主色（深色用于图形，亮色用于底纹）。
 */
const EXPERT_TINTS = new Map<LucideIcon, [string, string]>([
  [FlaskConical, ['#1F5139', '#3E8A63']],
  [Cpu, ['#3B3A8C', '#6A68D8']],
  [Lock, ['#3F4A5A', '#66788F']],
  [Database, ['#1F4F66', '#3D8AA8']],
  [GraduationCap, ['#28406B', '#4C719F']],
])

const EXPERT_TINT_FALLBACK: [string, string] = ['#3A3F52', '#5F6A85']

function expertTint(_name: string, Icon: LucideIcon): [string, string] {
  return EXPERT_TINTS.get(Icon) ?? EXPERT_TINT_FALLBACK
}

/** 专家图标块：淡色底纹 + 同色系描边 + 深色线性图标，避免高饱和色块与白 icon 的生硬感。 */
function ExpertIconTile({ name, Icon, size = 36 }: { name: string; Icon: LucideIcon; size?: number }) {
  const [deep, bright] = expertTint(name, Icon)
  return (
    <span
      className="grid place-items-center shrink-0"
      style={{
        width: size,
        height: size,
        borderRadius: size >= 48 ? 12 : 10,
        background: `linear-gradient(145deg, color-mix(in srgb, ${bright} 16%, var(--c-surface)) 0%, color-mix(in srgb, ${bright} 5%, var(--c-surface)) 100%)`,
        boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${bright} 28%, transparent)`,
      }}
      aria-hidden
    >
      <Icon
        size={Math.round(size * 0.45)}
        strokeWidth={1.9}
        style={{ color: `color-mix(in srgb, ${deep} 80%, var(--c-text))` }}
      />
    </span>
  )
}

/**
 * 专家包的领域分类：由包名与所含技能的分类/标题/描述派生，仅用于「专家 / 专家团」的二级筛选。
 * 规则顺序即优先级（医疗合规归医疗，法务合规归法律）。
 */
const EXPERT_DOMAIN_RULES: Array<[RegExp, string]> = [
  [/(医疗|临床|医学|诊疗|病历|用药|护理|medical|clinic|health)/i, '医疗'],
  [/(法务|法律|合同|诉讼|合规|隐私|知识产权|监管|legal|law|compliance|privacy)/i, '法律'],
  [/(科研|研究|文献|论文|实验|学术|research)/i, '科研'],
  [/(办公|文档|表格|会议|演示|邮件|office|document|meeting)/i, '办公'],
]
const EXPERT_DOMAIN_ORDER = ['医疗', '法律', '科研', '办公', '其他']

function expertDomainOf(name: string, skills: Skill[]): string {
  const text = [name, ...skills.map((s) => `${s.title} ${s.category || ''} ${s.desc}`)]
    .join(' ')
    .toLowerCase()
  for (const [re, domain] of EXPERT_DOMAIN_RULES) {
    if (re.test(text)) return domain
  }
  return '其他'
}

/**
 * 专家头像 URL：由 `/api/experts` 下发（`avatarUrl`），这里只按专家名索引。
 * 不再由前端猜包内文件名（旧的 `.plugin/icon.*` 在全部专家包中都不存在）。
 */
type ExpertAvatarIndex = Map<string, string>

/** 专家图标：优先用服务端下发的头像，加载失败或缺失时回落默认图标块。 */
function ExpertAvatar({
  name,
  Icon,
  size = 36,
  avatarUrl,
  token,
}: {
  name: string
  Icon: LucideIcon
  size?: number
  avatarUrl?: string | null
  token: string | null
}) {
  const [failed, setFailed] = useState(false)
  if (avatarUrl && !failed) {
    return (
      <img
        src={authedUrl(avatarUrl, token)}
        alt=""
        aria-hidden
        onError={() => setFailed(true)}
        className="shrink-0 object-cover bg-[var(--c-surface)]"
        style={{
          width: size,
          height: size,
          borderRadius: size >= 48 ? 12 : 10,
        }}
      />
    )
  }
  return <ExpertIconTile name={name} Icon={Icon} size={size} />
}

/** 专家卡片正文与能力标签（分类优先，缺失时退回技能名）。 */
function expertCardMeta(skills: Skill[]): { description: string; tags: string[]; publisher: string } {
  const categories = Array.from(
    new Set(skills.map((s) => (s.category || '').trim()).filter(Boolean)),
  )
  const tags = (
    categories.length > 0
      ? categories
      : Array.from(new Set(skills.map((s) => skillBaseName(s))))
  ).slice(0, 4)

  const pluginName = skills.find((s) => s.expert_name)?.expert_name || (skills[0] ? pluginNameOf(skills[0]) : '')
  const pluginDesc = skills.find((s) => s.expert_description)?.expert_description || ''
  const firstDesc = skills.find((s) => s.desc && s.desc.trim())?.desc.trim() || ''
  // SKILL.md 描述里常带「参考：」前缀（适配版标注），展示时去掉。
  const description = pluginDesc || firstDesc.replace(/^(参考|reference)：?\s*/i, '').trim() || '暂无描述'

  return { description, tags, publisher: pluginName || 'PrismWork 官方' }
}

/**
 * 卡片缩略图标：按标题/描述/分类关键字派生，尽量贴合场景。
 * 关键词使用「精确匹配」避免宽泛词污染；无任何匹配时回落到默认图标 FileText。
 * 后端若后续下发 icon 字段可替换为优先用服务端字段。
 */
function skillVisualFor(skill: Skill): { Icon: LucideIcon } {
  const t = (skill.title + ' ' + (skill.desc || '') + ' ' + (skill.category || '')).toLowerCase()

  // ── Orange（创作 / 营销 / 内容生成类） ──
  if (/(商店|店铺|电商|商城|shop|store|ecommerce)/i.test(t)) return { Icon: Store }
  if (/(文案|撰写|改写|内容生成|copy.?writing|content.?writing|写作)/i.test(t)) return { Icon: PenLine }
  if (/(广告故事|故事生成|剧本|短视频|storyboard|narrative|ad.?story|film|视频脚本)/i.test(t)) return { Icon: Clapperboard }
  if (/(gtm|go.?to.?market|营销方案|marketing|launch|上市发布|新品上市)/i.test(t)) return { Icon: Rocket }
  if (/(配音|耳机|音频|语音|headphone|audio|voice|播客|podcast)/i.test(t)) return { Icon: Headphones }
  if (/(咨询方法论|研究方法论|framework|顶级咨询|consulting|战略咨询)/i.test(t)) return { Icon: Award }

  // ── Blue（工具 / 分析 / 规划 / 办公类） ──
  if (/(提词|teleprompter|演讲稿|speech|presentation|演讲)/i.test(t)) return { Icon: Presentation }
  if (/(月会|例会|会议|meeting|周会|晨会)/i.test(t)) return { Icon: CalendarRange }
  if (/(财务|金融|finance|报销|账单|receipt|invoice|expense|会计|钱包|wallet|bank)/i.test(t)) return { Icon: Wallet }
  if (/(视频格式|ffprobe|视频检查|video.?fmt|视频质量)/i.test(t)) return { Icon: Film }
  if (/(旅行|trip|travel|行程|itinerary|出游|旅游)/i.test(t)) return { Icon: Plane }
  if (/(零售|卖货|选品|门店|商超|retail)/i.test(t)) return { Icon: ShoppingBag }
  if (/(培训|教学|课程|training|course|讲师|师资|带教)/i.test(t)) return { Icon: GraduationCap }
  if (/(需求分析|业务分析|analy|数据分析|竞品分析|用户研究)/i.test(t)) return { Icon: BarChart3 }
  if (/(评估|审查|审核|质检|合规|inspect|review|audit|合规检查)/i.test(t)) return { Icon: ShieldCheck }
  if (/(规划|计划|策划|planning|plan|路线图|roadmap|五年规划)/i.test(t)) return { Icon: Target }
  if (/(调研|研究|research|白皮书|行业报告|调研报告)/i.test(t)) return { Icon: BookOpen }
  if (/(agent|助手|assistan|skill|技能|机器人|bot)/i.test(t)) return { Icon: Bot }

  // 默认兜底
  return { Icon: FileText }
}

export default function SkillsPanel() {
  const [searchQuery, setSearchQuery] = useState('')
  const [activeTop, setActiveTop] = useState<'experts' | 'teams' | 'skills'>('experts')
  const [activeCategory, setActiveCategory] = useState<string>('all')
  /** 插件 tab 二级导航：当前选中的插件名；null = 停留在一级插件列表 */
  const [selectedPlugin, setSelectedPlugin] = useState<string | null>(null)
  const level2ScrollRef = useRef<HTMLDivElement>(null)
  const [level2CanScroll, setLevel2CanScroll] = useState<{ left: boolean; right: boolean }>({
    left: false,
    right: false,
  })
  const { token } = useAuth()
  const activeWorkspaceId = useUiStore((s: ReturnType<typeof useUiStore.getState>) => s.activeWorkspaceId)
  const [skills, setSkills] = useState<PanelSkill[]>([])
  const [skillsLoading, setSkillsLoading] = useState(false)
  const [, setSkillsLoaded] = useState(false)
  const [skillsError, setSkillsError] = useState<string | null>(null)
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null)
  /** 专家名 → 头像 URL。图像来源唯一：`/api/experts` 的 avatarUrl。 */
  const [avatarIndex, setAvatarIndex] = useState<ExpertAvatarIndex>(() => new Map())
  const fetchSeqRef = useRef(0)

  const loadExpertAvatars = async (): Promise<void> => {
    if (!token) return
    try {
      const res = await fetch(apiUrl('/api/experts'), {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const rows = (await res.json()) as Array<{ id?: unknown; avatarUrl?: unknown }>
      const next: ExpertAvatarIndex = new Map()
      for (const row of Array.isArray(rows) ? rows : []) {
        if (typeof row?.id === 'string' && typeof row?.avatarUrl === 'string' && row.avatarUrl) {
          next.set(row.id, row.avatarUrl)
        }
      }
      setAvatarIndex(next)
    } catch {
      // 头像缺失只影响观感：保留上次索引，卡片回落默认图标块。
    }
  }

  useEffect(() => {
    if (!token) return
    void loadExpertAvatars()
  }, [token])

  const loadSkills = async (): Promise<void> => {
    if (!token) return
    const seq = (fetchSeqRef.current += 1)
    setSkillsLoading(true)
    setSkillsLoaded(false)
    setSkillsError(null)
    try {
      const wsId = (activeWorkspaceId || '').trim() || 'default'
      const res = await fetch(
        apiUrl(`/api/skills/list?scope=all&workspace_id=${encodeURIComponent(wsId)}`),
        { headers: { Authorization: `Bearer ${token}` } },
      )
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = (await res.json()) as unknown
      if (seq !== fetchSeqRef.current) return
      const raw = Array.isArray(data) ? (data as ServerSkillItem[]) : []
      const mapped: PanelSkill[] = raw
        .filter((s) => s && typeof s === 'object' && typeof (s as any).name === 'string')
        .map((s) => {
          const name = (s.name || '').trim()
          const desc = typeof s.description === 'string' ? s.description : ''
          const source = typeof s.source === 'string' ? s.source : ''
          const tags = source ? [source] : ['本地']
          const VALID_ORIGINS = ['plaza', 'workspace', 'system', 'expert', 'project', 'bundled', 'personal', 'managed', 'mcp', 'marketplace', 'builtin'] as const
          const rawOrigin = typeof s.origin === 'string' ? s.origin : ''
          const origin = (VALID_ORIGINS as readonly string[]).includes(rawOrigin) ? rawOrigin as Skill['origin'] : 'system' as Skill['origin']
          const groupPath = Array.isArray(s.group_path)
            ? s.group_path.filter((part): part is string => typeof part === 'string' && !!part.trim())
            : []
          const groupLabel = typeof s.group_label === 'string' && s.group_label.trim()
            ? s.group_label.trim()
            : undefined
          return {
            id: name,
            title: name,
            desc,
            tags,
            installed: true,
            origin,
            location: typeof s.location === 'string' ? s.location : undefined,
            groupPath,
            groupLabel,
            category: typeof s.category === 'string' ? s.category : null,
            editable: typeof s.editable === 'boolean' ? s.editable : false,
            expert_name:
              typeof s.expert_name === 'string'
                ? s.expert_name
                : pluginNameOf({ id: name, groupPath, expert_name: null }),
            expert_description:
              typeof s.expert_description === 'string' ? s.expert_description : null,
            expertType: (s.expert_type === 'team'
              ? 'team'
              : s.expert_type === 'agent'
                ? 'agent'
                : null) as PanelSkill['expertType'],
          }
        })
        .filter((s) => s.id)
      setSkills(mapped)
    } catch (err) {
      if (seq !== fetchSeqRef.current) return
      const msg = err instanceof Error ? err.message : '加载失败'
      setSkillsError(msg)
      setSkills([])
    } finally {
      if (seq === fetchSeqRef.current) {
        setSkillsLoading(false)
        setSkillsLoaded(true)
      }
    }
  }

  useEffect(() => {
    if (!token) return
    void loadSkills()
  }, [activeWorkspaceId, token])

  useEffect(() => {
    const onChanged = () => {
      if (!token) return
      void loadSkills()
    }
    window.addEventListener('prismclaw.skills.changed', onChanged)
    return () => window.removeEventListener('prismclaw.skills.changed', onChanged)
  }, [activeWorkspaceId, token])

  const deleteSkill = async (skillId: string): Promise<boolean> => {
    if (!token) return false
    const wsId = (activeWorkspaceId || '').trim() || 'default'
    const snapshot = skills
    setSkills((prev) => prev.filter((s) => s.id !== skillId))
    try {
      const res = await fetch(apiUrl(`/api/skills/${encodeURIComponent(skillId)}?workspace_id=${encodeURIComponent(wsId)}`), {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      return true
    } catch {
      setSkills(snapshot)
      return false
    }
  }

  const editSkill = async (
    skillId: string,
    patch: { description?: string; body?: string },
  ): Promise<boolean> => {
    if (!token) return false
    const wsId = (activeWorkspaceId || '').trim() || 'default'
    try {
      const res = await fetch(
        apiUrl(`/api/skills/${encodeURIComponent(skillId)}?workspace_id=${encodeURIComponent(wsId)}`),
        {
          method: 'PATCH',
          headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify(patch),
        },
      )
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = (await res.json().catch(() => null)) as any
      const newDesc = typeof data?.description === 'string' ? data.description : undefined
      setSkills((prev) =>
        prev.map((s) => (s.id === skillId && newDesc !== undefined ? { ...s, desc: newDesc } : s)),
      )
      setSelectedSkill((prev) => {
        if (!prev || prev.id !== skillId) return prev
        const next = { ...prev }
        if (newDesc !== undefined) next.desc = newDesc
        if (patch.body !== undefined) next.content = patch.body
        return next
      })
      return true
    } catch {
      return false
    }
  }

  // 一级 tab 作用域：专家 = agent 类型的专家包；专家团 = team 类型的专家包；技能 = 非专家包来源
  const scopeSkills = useMemo(() => {
    const isExpertPackage = (s: PanelSkill) => (s.origin || '') === 'expert'
    if (activeTop === 'skills') return skills.filter((s) => !isExpertPackage(s))
    if (activeTop === 'teams') {
      return skills.filter((s) => isExpertPackage(s) && s.expertType === 'team')
    }
    return skills.filter((s) => isExpertPackage(s) && s.expertType !== 'team')
  }, [activeTop, skills])

  // 一级 tab 切换时重置二级为「全部」，并清空插件二级导航选中态
  useEffect(() => {
    setActiveCategory('all')
    setSelectedPlugin(null)
  }, [activeTop])

  // 专家 / 专家团：按插件包聚合，并标注领域分类（供二级 tab 与筛选使用）
  const expertPackages = useMemo(() => {
    if (activeTop === 'skills') return []
    const map = new Map<string, PanelSkill[]>()
    for (const s of scopeSkills) {
      const name = pluginNameOf(s)
      const arr = map.get(name)
      if (arr) arr.push(s)
      else map.set(name, [s])
    }
    return Array.from(map.entries())
      .map(([name, list]) => ({
        name,
        skills: list,
        domain: expertDomainOf(name, list),
        ...expertCardMeta(list),
      }))
      .sort((a, b) => a.name.localeCompare(b.name))
  }, [activeTop, scopeSkills])

  // 二级 tab：技能按 category 分组；专家 / 专家团按领域分组（仅保留实际存在的领域）
  const level2Tabs = useMemo(() => {
    const tabs: Array<{ key: string; label: string }> = [{ key: 'all', label: '全部' }]
    if (activeTop !== 'skills') {
      const present = new Set(expertPackages.map((p) => p.domain))
      for (const domain of EXPERT_DOMAIN_ORDER) {
        if (present.has(domain)) tabs.push({ key: domain, label: domain })
      }
      return tabs
    }
    const present = new Map<string, number>()
    for (const s of scopeSkills) {
      const cat = (s.category || '').trim()
      const key = cat || '__uncategorized__'
      present.set(key, (present.get(key) || 0) + 1)
    }
    const cats = Array.from(present.keys())
      .filter((k) => k !== '__uncategorized__')
      .sort((a, b) => (present.get(b) || 0) - (present.get(a) || 0) || a.localeCompare(b))
    for (const c of cats) tabs.push({ key: c, label: c })
    if (present.has('__uncategorized__')) tabs.push({ key: '__uncategorized__', label: '未分类' })
    return tabs
  }, [activeTop, expertPackages, scopeSkills])

  // 二级 tab 横向溢出检测（超出宽度时显示左右滑动箭头）
  const updateLevel2Overflow = () => {
    const el = level2ScrollRef.current
    if (!el) return
    const left = el.scrollLeft > 1
    const right = el.scrollLeft + el.clientWidth < el.scrollWidth - 1
    setLevel2CanScroll({ left, right })
  }
  useEffect(() => {
    const el = level2ScrollRef.current
    if (el) el.scrollLeft = 0
    updateLevel2Overflow()
  }, [level2Tabs])
  useEffect(() => {
    updateLevel2Overflow()
    const onResize = () => updateLevel2Overflow()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const scrollLevel2 = (dir: number) => {
    const el = level2ScrollRef.current
    if (!el) return
    el.scrollBy({ left: dir * 240, behavior: 'smooth' })
  }

  const displayedSkills = useMemo(() => {
    let base = scopeSkills
    if (activeCategory === 'all') {
      // 全部：不过滤
    } else if (activeCategory === '__uncategorized__') {
      base = base.filter((s) => !(s.category || '').trim())
    } else {
      base = base.filter((s) => (s.category || '').trim() === activeCategory)
    }
    const q = searchQuery.trim().toLowerCase()
    if (!q) return base
    return base.filter((s) => {
      if (s.title.toLowerCase().includes(q)) return true
      if (s.desc.toLowerCase().includes(q)) return true
      if (s.tags.some((t) => t.toLowerCase().includes(q))) return true
      return false
    })
  }, [activeCategory, searchQuery, scopeSkills])

  const groupedSkills = useMemo(() => {
    const groups = new Map<string, { label: string; skills: Skill[] }>()
    for (const skill of displayedSkills) {
      const label = (skill.category || '').trim() || '未分类'
      const existing = groups.get(label)
      if (existing) {
        existing.skills.push(skill)
        continue
      }
      groups.set(label, { label, skills: [skill] })
    }
    return Array.from(groups.values()).sort((a, b) => a.label.localeCompare(b.label))
  }, [displayedSkills])

  // ── 专家 tab：按插件包聚合为单层卡片 ──
  // 专家 / 专家团卡片：在聚合结果上套用二级领域筛选与搜索（命中包名 / 能力标签 / 描述）。
  const expertCards = useMemo(() => {
    if (activeTop === 'skills') return []
    const q = searchQuery.trim().toLowerCase()
    return expertPackages.filter((pg) => {
      if (activeCategory !== 'all' && pg.domain !== activeCategory) return false
      if (!q) return true
      if (pg.name.toLowerCase().includes(q)) return true
      if (pg.tags.some((t) => t.toLowerCase().includes(q))) return true
      return pg.description.toLowerCase().includes(q)
    })
  }, [activeTop, expertPackages, activeCategory, searchQuery])

  // 二级：当前选中插件下的技能
  const selectedPluginSkills = useMemo(() => {
    if (activeTop === 'skills' || !selectedPlugin) return []
    return scopeSkills.filter((s) => pluginNameOf(s) === selectedPlugin)
  }, [activeTop, selectedPlugin, scopeSkills])

  // 二级：选中插件内按 category 分组（遵循搜索过滤；插件详情页不显示分类 tab，故不受 activeCategory 影响）
  const pluginDetailGroups = useMemo(() => {
    if (activeTop === 'skills' || !selectedPlugin) return []
    const q = searchQuery.trim().toLowerCase()
    const base = q
      ? selectedPluginSkills.filter(
          (s) =>
            s.title.toLowerCase().includes(q) ||
            s.desc.toLowerCase().includes(q) ||
            s.tags.some((t) => t.toLowerCase().includes(q)),
        )
      : selectedPluginSkills
    const groups = new Map<string, { label: string; skills: Skill[] }>()
    for (const skill of base) {
      const label = (skill.category || '').trim() || '未分类'
      const existing = groups.get(label)
      if (existing) {
        existing.skills.push(skill)
        continue
      }
      groups.set(label, { label, skills: [skill] })
    }
    return Array.from(groups.values()).sort((a, b) => a.label.localeCompare(b.label))
  }, [activeTop, selectedPlugin, selectedPluginSkills, searchQuery])

  const openSkillDetail = async (skill: Skill) => {
    if (!token) return
    const wsId = (activeWorkspaceId || '').trim() || 'default'
    setSelectedSkill({ ...skill, content: '加载中…' })
    try {
      const res = await fetch(
        apiUrl(`/api/skills/${encodeURIComponent(skill.id)}?workspace_id=${encodeURIComponent(wsId)}`),
        { headers: { Authorization: `Bearer ${token}` } },
      )
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = (await res.json()) as any
      const content = typeof data?.body === 'string' ? data.body : ''
      const desc = typeof data?.description === 'string' ? data.description : ''
      const source = typeof data?.source === 'string' ? data.source : ''
      const origin =
        data?.origin === 'plaza' ||
        data?.origin === 'workspace' ||
        data?.origin === 'system' ||
        data?.origin === 'expert'
          ? data.origin
          : undefined
      const location = typeof data?.location === 'string' ? data.location : undefined
      const tags = source ? [source] : skill.tags
      const loadedAt = Date.now()
      setSelectedSkill((prev) =>
        prev && prev.id === skill.id
          ? { ...prev, content, desc: desc || prev.desc, tags, origin: origin ?? prev.origin, location, loadedAt, editable: typeof data?.editable === 'boolean' ? data.editable : prev.editable }
          : prev,
      )
    } catch (err) {
      const msg = err instanceof Error ? err.message : '加载失败'
      setSelectedSkill((prev) => (prev && prev.id === skill.id ? { ...prev, content: `加载失败：${msg}` } : prev))
    }
  }

  /** 技能卡片：小色块图标 + 标题（带「社区」徽章）+ 描述，整卡支持点击预览；「技能」tab 与插件二级详情页共用 */
  const renderSkillCard = (card: Skill) => {
    const { Icon } = skillVisualFor(card)
    const toneStyle = { background: 'var(--c-icon-blue-soft)', color: 'var(--c-icon-blue)' }
    const showCommunityBadge = card.origin === 'plaza'
    return (
      <div
        key={card.id}
        role="button"
        tabIndex={0}
        onClick={() => void openSkillDetail(card)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            void openSkillDetail(card)
          }
        }}
        className="app-card relative px-[16px] pt-[16px] pb-[18px] flex flex-col gap-[12px] cursor-pointer transition-colors hover:border-[var(--c-border-strong)] focus:outline-none focus-visible:ring-1 focus-visible:ring-[var(--c-accent)]/30"
      >
        <div className="flex items-start gap-[10px] min-w-0">
          <div
            className="w-[36px] h-[36px] rounded-[10px] grid place-items-center shrink-0"
            style={toneStyle}
          >
            <Icon size={18} strokeWidth={2} />
          </div>
          <div className="flex items-center gap-1.5 min-w-0 flex-1 pt-0.5">
            <div className="text-[14px] leading-[21px] font-semibold text-[var(--c-text)] truncate" title={card.title}>
              {card.title}
            </div>
            {showCommunityBadge ? (
              <span className="shrink-0 inline-flex items-center h-[18px] px-[6px] rounded-[4px] text-[12px] font-medium bg-[var(--c-tag-pink-bg)] text-[var(--c-tag-pink-fg)]">
                社区
              </span>
            ) : null}
          </div>
        </div>

        <div className="text-[14px] leading-[21px] text-[color-mix(in_srgb,var(--c-text-secondary)_60%,var(--c-text-tertiary))] line-clamp-2">
          {card.desc || '暂无描述'}
        </div>
      </div>
    )
  }

  /** 分组区块（分组标题 + 数量 + 三列卡片网格）：「技能」tab 与插件二级详情页共用 */
  const renderGroupedSections = (groups: Array<{ label: string; skills: Skill[] }>) => (
    <div className="space-y-[28px]">
      {groups.map((group) => (
        <section key={group.label}>
          <div className="mb-[18px] text-[14px] leading-[21px] font-semibold text-[var(--c-text)]">
            {group.label} ({group.skills.length})
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-[16px]">
            {group.skills.map((card) => renderSkillCard(card))}
          </div>
        </section>
      ))}
    </div>
  )

  /**
   * 专家卡片：图标 + 名称 + 发布方 + 描述 + 能力标签，右上角勾选表示已安装。
   * 整卡可点击进入专家详情（该专家的技能列表）。
   */
  const renderExpertCard = (pg: {
    name: string
    skills: Skill[]
    description: string
    tags: string[]
    publisher: string
  }) => {
    const { Icon } = expertVisualFor(pg.name, pg.description)
    const displayName = formatPluginName(pg.name)

    return (
      <button
        key={pg.name}
        type="button"
        onClick={() => setSelectedPlugin(pg.name)}
        className="app-card group h-full w-full px-[16px] pt-[16px] pb-[18px] flex flex-col text-left rounded-[var(--radius-card)] cursor-pointer focus:outline-none focus-visible:ring-1 focus-visible:ring-[var(--c-accent)]/30"
      >
        <span className="flex items-start gap-[10px] min-w-0">
          <ExpertAvatar
            name={pg.name}
            Icon={Icon}
            avatarUrl={avatarIndex.get(pg.name) ?? null}
            token={token}
          />

          <span className="flex items-start gap-2 flex-1 min-w-0">
            <span className="block flex-1 min-w-0">
              <span
                className="block text-[14px] leading-[21px] font-semibold text-[var(--c-text)] truncate"
                title={displayName}
              >
                {displayName}
              </span>
              <span className="mt-[2px] block text-[14px] leading-[21px] text-[color-mix(in_srgb,var(--c-text-secondary)_60%,var(--c-text-tertiary))] truncate">
                @{pg.publisher}
                <span className="mx-1.5">·</span>
                {pg.skills.length} 项能力
              </span>
            </span>
            <span
              className="shrink-0 mt-[2px] grid place-items-center w-5 h-5 text-[var(--c-text-secondary)]"
              title="已安装"
              aria-label="已安装"
            >
              <Check size={14} strokeWidth={2.4} />
            </span>
          </span>
        </span>

        <span className="mt-[12px] text-[14px] leading-[21px] text-[color-mix(in_srgb,var(--c-text-secondary)_60%,var(--c-text-tertiary))] line-clamp-3">
          {pg.description}
        </span>

        {pg.tags.length > 0 ? (
          <span className="mt-auto pt-[18px] flex flex-wrap items-center gap-y-1.5">
            {pg.tags.map((tag, i) => (
              <span key={tag} className="inline-flex items-center">
                <span className="px-[8px] h-[22px] inline-flex items-center rounded-[4px] bg-[var(--c-surface-2)] text-[12px] leading-none text-[var(--c-text-secondary)]">
                  {tag}
                </span>
                {i < pg.tags.length - 1 ? (
                  <ChevronRight
                    size={11}
                    className="mx-1 shrink-0 text-[var(--c-text-tertiary)]"
                    aria-hidden
                  />
                ) : null}
              </span>
            ))}
          </span>
        ) : null}
      </button>
    )
  }

  const searchPlaceholder =
    activeTop === 'teams' ? '搜索专家团' : activeTop === 'experts' ? '搜索专家' : '搜索技能'

  return (
    <div className="flex-1 min-w-0 min-h-0 flex flex-col relative app-panel overflow-hidden">
    <div className="flex-1 overflow-hidden flex flex-col min-w-0">
      <div className="px-6 md:px-[28px] pt-[56px] pb-[20px] shrink-0">
        <div className="max-w-[1200px] mx-auto w-full flex items-start justify-between gap-6">
          <div className="flex items-center gap-[26px] min-w-0 pt-[2px]">
            {([
              ['experts', '专家'],
              ['teams', '专家团'],
              ['skills', '技能'],
            ] as const).map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => setActiveTop(key)}
                className={`text-[19px] leading-[26px] transition-colors cursor-pointer ${
                  activeTop === key
                    ? 'font-semibold text-[var(--c-text)]'
                    : 'font-normal text-[color-mix(in_srgb,var(--c-text-secondary)_55%,var(--c-text-tertiary))] hover:text-[var(--c-text)]'
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          <div className="shrink-0 flex items-center gap-[10px] mt-[2px]">
            <div className="relative">
              <Search
                size={14}
                className="pointer-events-none absolute left-[12px] top-1/2 -translate-y-1/2 text-[var(--c-text-tertiary)]"
              />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder={searchPlaceholder}
                aria-label={searchPlaceholder}
                className="h-[32px] w-[180px] pl-[32px] pr-[12px] rounded-[var(--radius-control)] border border-[var(--c-border)] bg-[var(--c-surface)]
                 text-[14px] text-[var(--c-text)] placeholder:text-[var(--c-text-tertiary)]
                 hover:border-[var(--c-border-strong)] focus:outline-none focus:border-[var(--c-accent)]
                 focus:ring-1 focus:ring-[var(--c-accent)]/20 transition-colors"
              />
            </div>
            <button
              type="button"
              onClick={() => {
                void loadSkills()
                void loadExpertAvatars()
              }}
              className="btn btn-secondary h-[32px] w-[32px] !px-0 text-[var(--c-text-secondary)]"
              title="刷新"
              aria-label="刷新"
            >
              <RefreshIcon size={14} />
            </button>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 md:px-[28px] pb-10">
        <div className="max-w-[1200px] mx-auto w-full">
          {level2Tabs.length > 1 && (
          <div className="relative mb-[16px]">
            {level2CanScroll.left ? (
              <button
                type="button"
                onClick={() => scrollLevel2(-1)}
                className="absolute left-0 top-1/2 -translate-y-1/2 z-10 h-[24px] w-[24px] grid place-items-center rounded-full bg-[var(--c-surface)] border border-[var(--c-border)]/60 text-[var(--c-text-secondary)] hover:text-[var(--c-text)] cursor-pointer shadow-sm"
                aria-label="向左滑动"
              >
                <ChevronLeft size={13} />
              </button>
            ) : null}
            {level2CanScroll.right ? (
              <button
                type="button"
                onClick={() => scrollLevel2(1)}
                className="absolute right-0 top-1/2 -translate-y-1/2 z-10 h-[24px] w-[24px] grid place-items-center rounded-full bg-[var(--c-surface)] border border-[var(--c-border)]/60 text-[var(--c-text-secondary)] hover:text-[var(--c-text)] cursor-pointer shadow-sm"
                aria-label="向右滑动"
              >
                <ChevronRight size={13} />
              </button>
            ) : null}
            <div
              ref={level2ScrollRef}
              onScroll={updateLevel2Overflow}
              className={`flex items-center gap-[10px] overflow-x-auto whitespace-nowrap no-scrollbar ${
                level2CanScroll.left ? 'pl-[28px]' : ''
              } ${level2CanScroll.right ? 'pr-[28px]' : ''}`}
            >
              {level2Tabs.map((t) => {
                const active = activeCategory === t.key
                return (
                  <button
                    key={t.key}
                    type="button"
                    onClick={() => setActiveCategory(t.key)}
                    className={`shrink-0 h-[28px] px-[10px] rounded-[6px] text-[14px] transition-colors cursor-pointer ${
                      active
                        ? 'bg-[var(--c-accent-soft)] text-[var(--c-accent)] font-semibold'
                        : 'text-[var(--c-text-secondary)] font-medium hover:bg-[var(--c-surface-2)] hover:text-[var(--c-text)]'
                    }`}
                  >
                    {t.label}
                  </button>
                )
              })}
            </div>
          </div>
          )}

      {skillsLoading ? (
        <div className="py-24 text-center text-[length:var(--fs-md)] text-[var(--c-text-secondary)]">加载中…</div>
      ) : skillsError ? (
        <div className="py-24 text-center text-[length:var(--fs-md)] text-[var(--c-text-secondary)]">加载失败：{skillsError}</div>
      ) : activeTop !== 'skills' ? (
        selectedPlugin === null ? (
          expertCards.length === 0 ? (
            <div className="py-24 text-center text-[length:var(--fs-md)] text-[var(--c-text-secondary)]">
              {activeTop === 'teams' ? '暂无专家团' : '暂无专家'}
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-[16px]">
              {expertCards.map((pg) => renderExpertCard(pg))}
            </div>
          )
        ) : (
          <div>
            <div className="flex items-center gap-2.5 mb-[20px]">
              <button
                type="button"
                onClick={() => setSelectedPlugin(null)}
                className="inline-flex items-center gap-1 h-[28px] px-[10px] rounded-[6px] text-[14px] text-[var(--c-text-secondary)] hover:text-[var(--c-text)] hover:bg-[var(--c-surface-2)] transition-colors cursor-pointer"
              >
                <ChevronLeft size={15} /> 返回
              </button>
            </div>

            {(() => {
              const detailMeta = expertCardMeta(selectedPluginSkills)
              const { Icon } = expertVisualFor(selectedPlugin ?? '', detailMeta.description)
              return (
                <div className="mb-[28px] flex items-start gap-4">
                  <ExpertAvatar
                    name={selectedPlugin ?? ''}
                    Icon={Icon}
                    size={56}
                    avatarUrl={selectedPlugin ? (avatarIndex.get(selectedPlugin) ?? null) : null}
                    token={token}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2.5 min-w-0">
                      <h2
                        className="text-[16px] leading-[24px] font-semibold text-[var(--c-text)] truncate"
                        title={selectedPlugin ?? undefined}
                      >
                        {selectedPlugin ? formatPluginName(selectedPlugin) : ''}
                      </h2>
                      <span
                        className="shrink-0 grid place-items-center text-[var(--c-text-secondary)]"
                        title="已安装"
                        aria-label="已安装"
                      >
                        <Check size={15} strokeWidth={2.4} />
                      </span>
                    </div>

                    {/* 元信息：能力分类胶囊 */}
                    <div className="mt-[10px] flex flex-wrap items-center gap-[8px]">
                      {detailMeta.tags.map((tag) => (
                        <span
                          key={tag}
                          className="inline-flex items-center h-[22px] px-[8px] rounded-[4px] bg-[var(--c-surface-2)] text-[12px] leading-none text-[var(--c-text-secondary)]"
                        >
                          {tag}
                        </span>
                      ))}
                      <span className="text-[12px] text-[var(--c-text-tertiary)]">
                        {selectedPluginSkills.length} 项能力
                      </span>
                    </div>

                    <p className="mt-[10px] max-w-[860px] text-[14px] leading-[21px] text-[color-mix(in_srgb,var(--c-text-secondary)_60%,var(--c-text-tertiary))]">
                      {detailMeta.description}
                    </p>
                  </div>
                </div>
              )
            })()}

            {pluginDetailGroups.length === 0 ? (
              <div className="py-24 text-center text-[length:var(--fs-md)] text-[var(--c-text-secondary)]">暂无内容</div>
            ) : (
              renderGroupedSections(pluginDetailGroups)
            )}
          </div>
        )
      ) : displayedSkills.length === 0 ? (
        <div className="py-24 text-center text-[length:var(--fs-md)] text-[var(--c-text-secondary)]">暂无内容</div>
      ) : (
        renderGroupedSections(groupedSkills)
      )}
      
        </div>

      </div>

      <SkillDetailModal
        open={!!selectedSkill}
        skill={selectedSkill}
        onClose={() => setSelectedSkill(null)}
        onDelete={async (id) => {
          const ok = await deleteSkill(id)
          if (ok) setSelectedSkill(null)
          return ok
        }}
        onEdit={editSkill}
      />
    </div>
    </div>
  )
}
