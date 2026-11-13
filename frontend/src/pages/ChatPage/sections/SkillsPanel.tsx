import { useEffect, useMemo, useRef, useState } from 'react'
import {
  RefreshCw, Search, ChevronLeft, ChevronRight, Check,
  Store, Headphones, Award, FileText,
  PenLine, Clapperboard, Rocket, Presentation, CalendarRange, Wallet, Film,
  Plane, GraduationCap, BarChart3, ShoppingBag, ShieldCheck, Target, BookOpen, Bot,
  Scale, Briefcase, Building2, Users, Lock, Package, Landmark, Cpu,
  HeartPulse, Database, FlaskConical, Sparkles, Activity, Lightbulb, Puzzle,
  type LucideIcon,
} from 'lucide-react'
import SkillDetailModal from '../../../components/modals/SkillDetailModal'
import legalPluginIcon from '../../../images/plugin_icon/legal-chineselaw.png'
import medicalPluginIcon from '../../../images/plugin_icon/medical-healthcare.png'
import { apiUrl } from '../../../lib/system-adapter'
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
  workspace_trusted?: boolean | null
  editable?: boolean | null
  marketplace_name?: string | null
  marketplace_description?: string | null
}

/**
 * 推导技能所属插件名。
 * 后端对 plugin 来源技能的 group_path 恒为 [plugin_name]（origin.py classify_skill_group_path），
 * 技能 name 也是 `plugin:skill` 命名空间格式（extension_manager _namespaced_capability_name），
 * 故优先取 groupPath[0]，兜底取 id 冒号前缀。
 */
function pluginNameOf(skill: Skill): string {
  const gp = skill.groupPath?.[0]
  if (gp && gp.trim()) return gp.trim()
  const idx = skill.id.indexOf(':')
  return idx > 0 ? skill.id.slice(0, idx) : skill.id
}

/** 去掉插件命名空间前缀，返回技能基础名（plugin:skill → skill）。 */
function skillBaseName(skill: Skill): string {
  const idx = skill.id.indexOf(':')
  return idx > 0 ? skill.id.slice(idx + 1) : skill.id
}

/** 将插件 ID 格式化为可读标题："medical-clinical" → "Medical Clinical" */
function formatPluginName(name: string): string {
  return name
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(' ')
}

/**
 * 专家（插件）卡片缩略图标：按插件 ID + 描述派生。
 * 规则自上而下匹配，越具体的领域越靠前；无匹配时回落 Puzzle。
 */
const EXPERT_ICON_RULES: Array<[RegExp, LucideIcon]> = [
  [/(ai-|-ai|agent|人工智能)/, Cpu],
  [/(commercial|contract|deal|vendor|nda|renewal|合同)/, Briefcase],
  [/(corporate|company|board|公司|治理)/, Building2],
  [/(employment|labor|hiring|leave|劳动|用工)/, Users],
  [/(intellectual|patent|trademark|copyright|invention|ip-legal|\bip\b|知识产权)/, Lightbulb],
  [/(privacy|security|compliance|privilege|隐私|合规)/, Lock],
  [/(product|launch|marketing|产品)/, Package],
  [/(regulatory|policy|监管|政策)/, Landmark],
  [/(clinical|medical|patient|drug|prescription|healthcare|临床|医疗)/, HeartPulse],
  [/(informatics|fhir|数据|信息学)/, Database],
  [/(research|science|mining|science|科研|研究)/, FlaskConical],
  [/(wellness|健康)/, Sparkles],
  [/(operations|ops|运营)/, Activity],
  [/(litigation|court|law|clinic|student|诉讼|法)/, Scale],
  [/(legal|法律)/, Scale],
]

function expertVisualFor(name: string, desc: string): { Icon: LucideIcon } {
  const t = (name + ' ' + desc).toLowerCase()
  for (const [re, Icon] of EXPERT_ICON_RULES) {
    if (re.test(t)) return { Icon }
  }
  return { Icon: Puzzle }
}

/**
 * 领域配色：图标语义 → 主色（深色用于图形，亮色用于底纹）。
 * 固定映射而非散列，保证「法律=藏蓝、医疗=绛红、科研=墨绿」等语义稳定。
 */
const EXPERT_TINTS = new Map<LucideIcon, [string, string]>([
  [Scale, ['#1E3A5F', '#3E6D99']],
  [HeartPulse, ['#8C1D40', '#C2527A']],
  [FlaskConical, ['#1F5139', '#3E8A63']],
  [Briefcase, ['#8A5A16', '#C08A33']],
  [Cpu, ['#3B3A8C', '#6A68D8']],
  [Users, ['#6B3F8C', '#9B6BC4']],
  [Lock, ['#3F4A5A', '#66788F']],
  [Building2, ['#5A4635', '#8A6B52']],
  [Landmark, ['#4A4128', '#7A6B3F']],
  [Package, ['#7A4A12', '#B47A2C']],
  [Database, ['#1F4F66', '#3D8AA8']],
  [Lightbulb, ['#7A5A0A', '#B08A1E']],
  [Activity, ['#0F5947', '#2C8A70']],
  [Sparkles, ['#7A2E63', '#B05A93']],
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
 * 插件图标：文件名与插件目录名一致（images/plugin_icon/<plugin>.png）。
 * 未收录的插件回落为 ExpertIconTile 的默认 Lucide 图标。
 */
const PLUGIN_ICONS: Record<string, string> = {
  'legal-chineselaw': legalPluginIcon,
  'medical-healthcare': medicalPluginIcon,
}

function pluginIconOf(name: string): string | null {
  return PLUGIN_ICONS[name] ?? PLUGIN_ICONS[name.toLowerCase()] ?? null
}

/** 专家图标：优先用插件同名图标，找不到时用默认图标块。 */
function ExpertAvatar({ name, Icon, size = 36 }: { name: string; Icon: LucideIcon; size?: number }) {
  const src = pluginIconOf(name)
  if (!src) return <ExpertIconTile name={name} Icon={Icon} size={size} />
  return (
    <img
      src={src}
      alt=""
      aria-hidden
      className="shrink-0 object-cover bg-[var(--c-surface)]"
      style={{
        width: size,
        height: size,
        borderRadius: size >= 48 ? 12 : 10,
      }}
    />
  )
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

  const marketplace = skills.find((s) => s.marketplace_name)?.marketplace_name || null
  const marketplaceDesc = skills.find((s) => s.marketplace_description)?.marketplace_description || ''
  const firstDesc = skills.find((s) => s.desc && s.desc.trim())?.desc.trim() || ''
  // SKILL.md 描述里常带「参考：」前缀（适配版标注），展示时去掉。
  const description = firstDesc.replace(/^(参考|reference)：?\s*/i, '').trim() || marketplaceDesc || '暂无描述'

  return { description, tags, publisher: marketplace || 'PrismClaw 官方' }
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

/** 这些目录名是通用的插件/技能根目录，而非具体的插件市场（外层分组）目录。 */
const GENERIC_PLUGIN_ROOTS = new Set(['plugins', 'skills', '.prismclaw', '.claude-plugin'])

/**
 * 从技能的 location（绝对路径，形如 .../{marketplace}/{plugin}/skills/{skill}/SKILL.md）
 * 推导其所属的插件市场目录（外层分组，如 "legal-chineselaw"）。
 *
 * 市场清单形式的插件目录结构为 `.prismclaw/assets/plugins/{marketplace}/{plugin}/skills/...`，
 * 故取「插件同名目录」上一级作为市场目录；若上一级是通用根目录（直接扫描安装的非市场插件），
 * 则返回 null（视为无外层分组）。
 */
function marketplaceDirOf(skill: Skill): string | null {
  const loc = (skill.location || '').trim()
  if (!loc) return null
  const plugin = pluginNameOf(skill)
  if (!plugin) return null
  const segs = loc.split(/[\\/]+/).filter(Boolean)
  // 优先匹配 .../{marketplace}/{plugin}/skills/... 结构
  for (let i = segs.length - 1; i >= 2; i--) {
    if (segs[i] === plugin && segs[i + 1] === 'skills') {
      const parent = segs[i - 1]
      return GENERIC_PLUGIN_ROOTS.has(parent) ? null : parent
    }
  }
  // 兜底：仅按插件同名目录的上一级判断
  for (let i = segs.length - 1; i >= 1; i--) {
    if (segs[i] === plugin) {
      const parent = segs[i - 1]
      return GENERIC_PLUGIN_ROOTS.has(parent) ? null : parent
    }
  }
  return null
}

export default function SkillsPanel() {
  const [searchQuery, setSearchQuery] = useState('')
  const [isSearchExpanded, setIsSearchExpanded] = useState(false)
  const [activeTop, setActiveTop] = useState<'skills' | 'plugins'>('plugins')
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
  const [skills, setSkills] = useState<Skill[]>([])
  const [skillsLoading, setSkillsLoading] = useState(false)
  const [, setSkillsLoaded] = useState(false)
  const [skillsError, setSkillsError] = useState<string | null>(null)
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null)
  const fetchSeqRef = useRef(0)

  const loadSkills = async (): Promise<void> => {
    if (!token) return
    const seq = (fetchSeqRef.current += 1)
    setSkillsLoading(true)
    setSkillsLoaded(false)
    setSkillsError(null)
    try {
      const wsId = (activeWorkspaceId || '').trim() || 'default'
      const res = await fetch(
        apiUrl(`/api/skills/list?workspace_id=${encodeURIComponent(wsId)}`),
        { headers: { Authorization: `Bearer ${token}` } },
      )
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = (await res.json()) as unknown
      if (seq !== fetchSeqRef.current) return
      const raw = Array.isArray(data) ? (data as ServerSkillItem[]) : []
      const mapped: Skill[] = raw
        .filter((s) => s && typeof s === 'object' && typeof (s as any).name === 'string')
        .map((s) => {
          const name = (s.name || '').trim()
          const desc = typeof s.description === 'string' ? s.description : ''
          const source = typeof s.source === 'string' ? s.source : ''
          const tags = source ? [source] : ['本地']
          const VALID_ORIGINS = ['plaza', 'workspace', 'system', 'plugin', 'project', 'bundled', 'personal', 'managed', 'mcp', 'marketplace', 'builtin'] as const
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
            trusted: typeof s.workspace_trusted === 'boolean' ? s.workspace_trusted : undefined,
            editable: typeof s.editable === 'boolean' ? s.editable : false,
            marketplace_name: typeof s.marketplace_name === 'string' ? s.marketplace_name : null,
            marketplace_description:
              typeof s.marketplace_description === 'string' ? s.marketplace_description : null,
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

  // 一级 tab 作用域：技能 = 非插件来源；插件 = 插件来源
  const scopeSkills = useMemo(() => {
    const pluginOrigins = new Set(['plugin', 'marketplace', 'builtin'])
    if (activeTop === 'plugins') return skills.filter((s) => pluginOrigins.has(s.origin || ''))
    return skills.filter((s) => !pluginOrigins.has(s.origin || ''))
  }, [activeTop, skills])

  // 二级 tab：当前作用域内的「全部」+ 各技能分组（category = 专属特点）
  const level2Tabs = useMemo(() => {
    const present = new Map<string, number>()
    for (const s of scopeSkills) {
      const cat = (s.category || '').trim()
      const key = cat || '__uncategorized__'
      present.set(key, (present.get(key) || 0) + 1)
    }
    const tabs: Array<{ key: string; label: string }> = [{ key: 'all', label: '全部' }]
    const cats = Array.from(present.keys())
      .filter((k) => k !== '__uncategorized__')
      .sort((a, b) => (present.get(b) || 0) - (present.get(a) || 0) || a.localeCompare(b))
    for (const c of cats) tabs.push({ key: c, label: c })
    if (present.has('__uncategorized__')) tabs.push({ key: '__uncategorized__', label: '未分类' })
    return tabs
  }, [scopeSkills])

  // 一级 tab 切换时重置二级为「全部」，并清空插件二级导航选中态
  useEffect(() => {
    setActiveCategory('all')
    setSelectedPlugin(null)
  }, [activeTop])

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

  // ── 专家 tab：按插件聚合为单层卡片 ──
  // 搜索命中插件名 / 所属套件名 / 能力标签；同套件卡片相邻，便于扫读。
  const expertCards = useMemo(() => {
    if (activeTop !== 'plugins') return []
    const map = new Map<string, Skill[]>()
    for (const s of scopeSkills) {
      const name = pluginNameOf(s)
      const arr = map.get(name)
      if (arr) arr.push(s)
      else map.set(name, [s])
    }
    const q = searchQuery.trim().toLowerCase()
    return Array.from(map.entries())
      .map(([name, skills]) => {
        const marketplace = marketplaceDirOf(skills[0])
        const meta = expertCardMeta(skills)
        return { name, skills, marketplace, ...meta }
      })
      .filter(({ name, marketplace, tags, description }) => {
        if (!q) return true
        if (name.toLowerCase().includes(q)) return true
        if (marketplace && marketplace.toLowerCase().includes(q)) return true
        if (tags.some((t) => t.toLowerCase().includes(q))) return true
        return description.toLowerCase().includes(q)
      })
      .sort(
        (a, b) =>
          (a.marketplace ?? '\uffff').localeCompare(b.marketplace ?? '\uffff') ||
          a.name.localeCompare(b.name),
      )
  }, [activeTop, scopeSkills, searchQuery])

  // 二级：当前选中插件下的技能
  const selectedPluginSkills = useMemo(() => {
    if (activeTop !== 'plugins' || !selectedPlugin) return []
    return scopeSkills.filter((s) => pluginNameOf(s) === selectedPlugin)
  }, [activeTop, selectedPlugin, scopeSkills])

  // 二级：选中插件内按 category 分组（遵循搜索过滤；插件详情页不显示分类 tab，故不受 activeCategory 影响）
  const pluginDetailGroups = useMemo(() => {
    if (activeTop !== 'plugins' || !selectedPlugin) return []
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
        data?.origin === 'plugin'
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
        className="app-card relative px-4 pt-4 pb-4 flex flex-col gap-2.5 cursor-pointer transition-colors hover:border-[var(--c-border-strong)] hover:bg-[var(--c-surface-2)] focus:outline-none focus-visible:ring-1 focus-visible:ring-[var(--c-accent)]/40 min-h-[124px]"
      >
        <div className="flex items-start gap-3 min-w-0">
          <div
            className="w-10 h-10 rounded-[var(--radius-md)] grid place-items-center shrink-0"
            style={toneStyle}
          >
            <Icon size={20} strokeWidth={2} />
          </div>
          <div className="flex items-center gap-1.5 min-w-0 flex-1 pt-0.5">
            <div className="text-[length:var(--fs-md)] font-semibold text-[var(--c-text)] leading-snug line-clamp-1 truncate" title={card.title}>
              {card.title}
            </div>
            {showCommunityBadge ? (
              <span className="shrink-0 px-1.5 py-0.5 rounded-[var(--radius-control)] text-[length:var(--fs-sm)] font-medium bg-[var(--c-tag-pink-bg)] text-[var(--c-tag-pink-fg)]">
                社区
              </span>
            ) : null}
          </div>
        </div>

        <div className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)] leading-relaxed line-clamp-2 min-h-[2.6em]">
          {card.desc || '暂无描述'}
        </div>
      </div>
    )
  }

  /** 分组区块（分组标题 + 数量角标 + 三列卡片网格）：「技能」tab 与插件二级详情页共用 */
  const renderGroupedSections = (groups: Array<{ label: string; skills: Skill[] }>) => (
    <div className="space-y-7">
      {groups.map((group) => (
        <section key={group.label} className="space-y-3">
          <div className="flex items-center gap-2">
            <div className="text-[length:var(--fs-md)] font-semibold tracking-[0.08em] uppercase text-[var(--c-text-secondary)]">
              {group.label}
            </div>
            <span className="shrink-0 px-2 py-0.5 rounded-[var(--radius-md)] text-[length:var(--fs-sm)] font-medium bg-[var(--c-surface-2)] text-[var(--c-text-secondary)]">
              {group.skills.length}
            </span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
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
    marketplace: string | null
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
        className="app-card group h-full w-full px-5 py-5 flex flex-col gap-3.5 text-left rounded-[var(--radius-card)] cursor-pointer focus:outline-none focus-visible:ring-1 focus-visible:ring-[var(--c-accent)]/40"
      >
        <span className="flex items-start gap-3.5 min-w-0">
          <ExpertAvatar name={pg.name} Icon={Icon} />

          <span className="flex items-start gap-2 flex-1 min-w-0">
            <span className="block flex-1 min-w-0">
              <span
                className="block text-[length:var(--fs-lg)] font-semibold leading-tight text-[var(--c-text)] truncate"
                title={displayName}
              >
                {displayName}
              </span>
              <span className="mt-1 block text-[length:var(--fs-sm)] leading-tight text-[var(--c-text-secondary)] truncate">
                @{pg.publisher}
                <span className="mx-1.5 text-[var(--c-text-tertiary)]">·</span>
                {pg.skills.length} 项能力
              </span>
            </span>
            <span
              className="shrink-0 mt-0.5 grid place-items-center w-5 h-5 text-[var(--c-text-secondary)]"
              title="已安装"
              aria-label="已安装"
            >
              <Check size={15} strokeWidth={2.4} />
            </span>
          </span>
        </span>

        <span className="text-[length:var(--fs-sm)] leading-[1.7] text-[var(--c-text-secondary)] line-clamp-3">
          {pg.description}
        </span>

        {pg.tags.length > 0 ? (
          <span className="mt-auto flex flex-wrap items-center gap-y-1.5">
            {pg.tags.map((tag, i) => (
              <span key={tag} className="inline-flex items-center">
                <span className="px-2 py-[5px] rounded-[var(--radius-control)] bg-[var(--c-surface-2)] text-[length:var(--fs-xs)] leading-none text-[var(--c-text-secondary)]">
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

  return (
    <div className="flex-1 min-w-0 min-h-0 flex flex-col relative app-panel overflow-hidden">
    <div className="flex-1 overflow-hidden flex flex-col min-w-0">
      <div className="px-8 pt-7 pb-5 shrink-0">
        <div className="max-w-[1200px] mx-auto w-full flex items-start justify-between gap-6">
          <div className="min-w-0">
            <div className="text-[length:var(--fs-xl)] font-semibold text-[var(--c-text)] tracking-tight">
              专家·技能
            </div>
            <div className="mt-1 text-[length:var(--fs-md)] text-[var(--c-text-secondary)]">
              提供可复用的最佳实践与工具。
            </div>
          </div>
          <div className="shrink-0 flex items-center gap-2">
            <button
              type="button"
              onClick={() => void loadSkills()}
              className="h-10 w-10 rounded-[var(--radius-md)] hover:bg-[var(--c-surface-2)] transition-colors cursor-pointer grid place-items-center"
              title="刷新"
            >
              <RefreshCw size={18} strokeWidth={2} className="text-[var(--c-text-secondary)]" />
            </button>

            <div className="relative flex items-center h-10">
              <input
                type="text"
                placeholder={isSearchExpanded || searchQuery ? '搜索技能' : ''}
                aria-label="搜索技能"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onFocus={() => setIsSearchExpanded(true)}
                onBlur={(e) => {
                  if (!e.target.value) {
                    setIsSearchExpanded(false)
                  }
                }}
                className={`h-10 pl-9 pr-3 rounded-[var(--radius-md)] bg-[var(--c-surface)]
                           text-[length:var(--fs-md)] text-[var(--c-text)] placeholder:text-[var(--c-text-secondary)]/70
                           border border-[var(--c-border)] focus:outline-none
                           transition-all duration-300 ease-in-out cursor-pointer focus:cursor-text
                           ${isSearchExpanded || searchQuery ? 'w-48' : 'w-10 !px-0'}`}
              />
              <Search 
                size={18} 
                strokeWidth={2}
                className={`absolute text-[var(--c-text-secondary)] pointer-events-none transition-all duration-300
                           ${isSearchExpanded || searchQuery ? 'left-3' : 'left-1/2 -translate-x-1/2'}`} 
              />
            </div>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6">
        <div className="max-w-[1200px] mx-auto w-full pb-10">
          <div className="flex items-center gap-8 mb-5 border-b border-[var(--c-border)]/50">
            <button
              type="button"
              onClick={() => setActiveTop('plugins')}
              className={`relative pl-3 pr-3 pb-2.5 text-[length:var(--fs-lg)] font-semibold transition-colors cursor-pointer ${
                activeTop === 'plugins'
                  ? 'text-[var(--c-text)]'
                  : 'text-[var(--c-text-secondary)] hover:text-[var(--c-text)]'
              }`}
            >
              专家
              {activeTop === 'plugins' ? (
                <span className="absolute left-3 right-3 -bottom-px h-0.5 rounded-full bg-[var(--c-accent)]" />
              ) : null}
            </button>
            <button
              type="button"
              onClick={() => setActiveTop('skills')}
              className={`relative pl-3 pr-3 pb-2.5 text-[length:var(--fs-lg)] font-semibold transition-colors cursor-pointer ${
                activeTop === 'skills'
                  ? 'text-[var(--c-text)]'
                  : 'text-[var(--c-text-secondary)] hover:text-[var(--c-text)]'
              }`}
            >
              技能
              {activeTop === 'skills' ? (
                <span className="absolute left-3 right-3 -bottom-px h-0.5 rounded-full bg-[var(--c-accent)]" />
              ) : null}
            </button>
          </div>

          {activeTop === 'skills' && (
          <div className="relative mb-6">
            {level2CanScroll.left ? (
              <button
                type="button"
                onClick={() => scrollLevel2(-1)}
                className="absolute left-0 top-1/2 -translate-y-1/2 z-10 h-7 w-7 grid place-items-center rounded-full bg-[var(--c-surface)] border border-[var(--c-border)]/60 text-[var(--c-text-secondary)] hover:text-[var(--c-text)] cursor-pointer shadow-sm"
                aria-label="向左滑动"
              >
                <ChevronLeft size={14} />
              </button>
            ) : null}
            {level2CanScroll.right ? (
              <button
                type="button"
                onClick={() => scrollLevel2(1)}
                className="absolute right-0 top-1/2 -translate-y-1/2 z-10 h-7 w-7 grid place-items-center rounded-full bg-[var(--c-surface)] border border-[var(--c-border)]/60 text-[var(--c-text-secondary)] hover:text-[var(--c-text)] cursor-pointer shadow-sm"
                aria-label="向右滑动"
              >
                <ChevronRight size={14} />
              </button>
            ) : null}
            <div
              ref={level2ScrollRef}
              onScroll={updateLevel2Overflow}
              className={`flex items-center gap-2 overflow-x-auto whitespace-nowrap no-scrollbar py-1 ${
                level2CanScroll.left ? 'pl-9' : ''
              } ${level2CanScroll.right ? 'pr-9' : ''}`}
            >
              {level2Tabs.map((t) => {
                const active = activeCategory === t.key
                return (
                  <button
                    key={t.key}
                    type="button"
                    onClick={() => setActiveCategory(t.key)}
                    className={`shrink-0 px-3 py-1.5 rounded-[var(--radius-control)] text-[length:var(--fs-sm)] font-medium transition-all cursor-pointer ${
                      active
                        ? 'bg-[var(--c-surface)] border border-[var(--c-border)] text-[var(--c-text)] shadow-sm'
                        : 'text-[var(--c-text-secondary)] hover:text-[var(--c-text)] hover:bg-[var(--c-surface-2)]'
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
        <div className="py-20 text-center text-[var(--c-text-secondary)] text-[length:var(--fs-md)]">加载中…</div>
      ) : skillsError ? (
        <div className="py-20 text-center text-[var(--c-text-secondary)] text-[length:var(--fs-md)]">加载失败：{skillsError}</div>
      ) : activeTop === 'plugins' ? (
        selectedPlugin === null ? (
          expertCards.length === 0 ? (
            <div className="py-20 text-center text-[var(--c-text-secondary)] text-[length:var(--fs-md)]">暂无专家</div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              {expertCards.map((pg) => renderExpertCard(pg))}
            </div>
          )
        ) : (
          <div>
            <div className="flex items-center gap-2.5 mb-4">
              <button
                type="button"
                onClick={() => setSelectedPlugin(null)}
                className="inline-flex items-center gap-1 h-8 px-2.5 rounded-[var(--radius-control)] text-[length:var(--fs-sm)] text-[var(--c-text-secondary)] hover:text-[var(--c-text)] hover:bg-[var(--c-surface-2)] transition-colors cursor-pointer"
              >
                <ChevronLeft size={16} /> 返回
              </button>
            </div>

            {(() => {
              const detailMeta = expertCardMeta(selectedPluginSkills)
              const { Icon } = expertVisualFor(selectedPlugin ?? '', detailMeta.description)
              return (
                <div className="mb-7 flex items-start gap-4">
                  <ExpertAvatar name={selectedPlugin ?? ''} Icon={Icon} size={60} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2.5 min-w-0">
                      <h2
                        className="text-[length:var(--fs-xl)] font-semibold leading-tight text-[var(--c-text)] truncate"
                        title={selectedPlugin ?? undefined}
                      >
                        {selectedPlugin ? formatPluginName(selectedPlugin) : ''}
                      </h2>
                      <span
                        className="shrink-0 grid place-items-center text-[var(--c-text-secondary)]"
                        title="已安装"
                        aria-label="已安装"
                      >
                        <Check size={16} strokeWidth={2.4} />
                      </span>
                    </div>

                    {/* 元信息：能力分类胶囊 */}
                    <div className="mt-2.5 flex flex-wrap items-center gap-2">
                      {detailMeta.tags.map((tag) => (
                        <span
                          key={tag}
                          className="inline-flex items-center h-6 px-2.5 rounded-full bg-[var(--c-surface-2)] text-[length:var(--fs-xs)] leading-none text-[var(--c-text-secondary)]"
                        >
                          {tag}
                        </span>
                      ))}
                      <span className="text-[length:var(--fs-xs)] text-[var(--c-text-tertiary)]">
                        {selectedPluginSkills.length} 项能力
                      </span>
                    </div>

                    <p className="mt-3 max-w-[860px] text-[length:var(--fs-sm)] leading-[1.8] text-[var(--c-text-secondary)]">
                      {detailMeta.description}
                    </p>
                  </div>
                </div>
              )
            })()}

            {pluginDetailGroups.length === 0 ? (
              <div className="py-20 text-center text-[var(--c-text-secondary)] text-[length:var(--fs-md)]">暂无内容</div>
            ) : (
              renderGroupedSections(pluginDetailGroups)
            )}
          </div>
        )
      ) : displayedSkills.length === 0 ? (
        <div className="py-20 text-center text-[var(--c-text-secondary)] text-[length:var(--fs-md)]">暂无内容</div>
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
