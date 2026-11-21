import {
    BarChart3, BookOpen, ChevronLeft, ChevronRight, ClipboardList,
    FilePen, FileText, ListChecks, Search, Sparkles,
} from 'lucide-react'
import {useEffect, useMemo, useState} from 'react'

type SceneKey = 'medical' | 'legal' | 'office' | 'research'

interface SuggestionGroup {
    type: string
    icon: typeof FileText
    items: string[]
}

const SCENES: { key: SceneKey; label: string; icon: typeof FileText }[] = [
    {key: 'medical', label: '医疗', icon: Search},
    {key: 'legal', label: '法律', icon: BookOpen},
    {key: 'office', label: '办公', icon: ClipboardList},
    {key: 'research', label: '科研', icon: BarChart3},
]

// ═══════════════════════════════════════════════════════════════
// 每个场景围绕「企业医院市场」聚焦两大方向：
//   ① 肿瘤质子治疗（医院端核心差异化能力）
//   ② 亚健康管理（企业端员工健康服务刚需）
// 所有示例 prompt 必须直接可运行，不依赖用户上传文件。
// ═══════════════════════════════════════════════════════════════

const MEDICAL_GROUPS: SuggestionGroup[] = [
    {
        type: '质子治疗 · 适应症',
        icon: Sparkles,
        items: [
            '肿瘤质子治疗适合哪些癌种？头颈部、前列腺、儿童肿瘤的适应症分别是什么',
            '检索 NCCN 2025 指南，哪些肿瘤已经把质子治疗列为推荐方案',
            '对比质子 vs 光子放疗在早期肺癌中的剂量分布和正常组织损伤差异',
        ],
    },
    {
        type: '质子治疗 · 临床证据',
        icon: BookOpen,
        items: [
            '帮我搜近三年质子治疗前列腺癌的临床研究，生化复发率和晚期毒性数据',
            '总结儿童脑肿瘤质子治疗的生存率变化和神经认知保护效果',
            '查找质子 vs SBRT 治疗早期肺癌的 head-to-head 或 meta 分析',
        ],
    },
    {
        type: '亚健康 · 企业健康管理',
        icon: FileText,
        items: [
            '企业员工常见亚健康问题（颈肩腰椎/睡眠/代谢）的流行病学数据和干预建议',
            '检索职场人群高血压、糖尿病、脂肪肝的患病率趋势和筛查指南',
            '总结企业健康管理中心可以落地的亚健康干预手段，哪些有临床证据支持',
        ],
    },
]

const LEGAL_GROUPS: SuggestionGroup[] = [
    {
        type: '质子 · 合规准入',
        icon: Search,
        items: [
            '质子重离子治疗中心的医疗机构执业许可证申请流程和核心审批条件',
            '国产质子设备拿到 NMPA 三类证后，医疗机构采购和临床应用还需要哪些资质',
            '帮我梳理一份质子治疗项目从立项到临床运营的合规 checklist',
        ],
    },
    {
        type: '企业医疗 · 数据合规',
        icon: BookOpen,
        items: [
            '企业健康管理中心收集员工体检数据，个保法下需要哪些知情同意和数据保护措施',
            '医疗体检数据的跨境传输（如第三方影像中心在境外）需要走什么审批流程',
            '用人单位可以获取员工的哪些健康信息，哪些是绝对禁止的',
        ],
    },
    {
        type: '医保与商业险',
        icon: FilePen,
        items: [
            '质子治疗目前哪些项目已经纳入医保？各省市报销比例差异如何',
            '企业补充医疗保险覆盖质子治疗的合规要点和常见免责条款',
            '帮我总结商业重疾险中"重疾定义"里与质子治疗相关的理赔条件',
        ],
    },
]

const OFFICE_GROUPS: SuggestionGroup[] = [
    {
        type: '医院 · 运营分析',
        icon: BarChart3,
        items: [
            '一家质子中心的单患者平均治疗周期是多长？按病种列出频次和资源占用',
            '对比国内主要质子中心的年治疗量、设备利用率和单疗程收费区间',
            '帮我估算一家新建质子中心在运营第 3 年的盈亏平衡患者量是多少',
        ],
    },
    {
        type: '企业 · 健康管理方案',
        icon: ClipboardList,
        items: [
            '设计一份 500 人规模科技公司的年度健康管理方案，覆盖体检/干预/保险三层',
            '列出企业医务室可以独立处理的亚健康问题清单，哪些需要转诊到专科',
            '帮我写一份企业与质子中心合作的员工体检/转诊服务框架协议要点',
        ],
    },
    {
        type: '市场 · 竞争情报',
        icon: ListChecks,
        items: [
            '国内目前已运营和在建的质子重离子中心有多少家？按地区和设备类型分类',
            '帮我汇总 2024-2025 年新获批的质子/重离子医疗机构，列出审批编号和投资主体',
            '对比三家头部民营质子中心（上海/浙江/山东）的定位差异和核心竞争力',
        ],
    },
]

const RESEARCH_GROUPS: SuggestionGroup[] = [
    {
        type: '质子 · 剂量学研究',
        icon: BarChart3,
        items: [
            '帮我检索近三年关于质子 Bragg peak 剂量分布优化的关键研究，列出技术路线演进',
            '比较 IMPT（调强质子治疗） vs PBT（被动散射）在复杂靶区中的剂量学优势',
            '查找质子治疗中 RBE（相对生物学效应）模型的最新进展，对临床处方剂量有什么影响',
        ],
    },
    {
        type: '亚健康 · 流行病学',
        icon: Sparkles,
        items: [
            '检索中国职场人群亚健康患病率的最新大型流行病学调查，按行业和年龄分组数据',
            '帮我总结 2020 年后关于久坐与代谢综合征风险的 meta 分析，给出合并效应量',
            '查找企业健康管理干预的 RCT 研究，哪些干预手段确实降低了心血管事件风险',
        ],
    },
    {
        type: '卫生经济 · 成本效益',
        icon: BookOpen,
        items: [
            '帮我搜质子治疗相比光子放疗的卫生经济学研究，给出每 QALY 的增量成本数据',
            '企业健康管理投入的 ROI 分析框架：发病率降低 × 缺勤减少 × 医疗成本节约',
            '查找各国质子治疗纳入医保的卫生经济学评估标准，对比中国和发达国家的差异',
        ],
    },
]

const GROUP_MAP: Record<SceneKey, SuggestionGroup[]> = {
    medical: MEDICAL_GROUPS,
    legal: LEGAL_GROUPS,
    office: OFFICE_GROUPS,
    research: RESEARCH_GROUPS,
}

interface Props {
    onPick: (prompt: string) => void
}

export default function EmptyStateSuggestions({onPick}: Props) {
    const [scene, setScene] = useState<SceneKey>(SCENES[0].key)
    const [page, setPage] = useState(0)

    const groups = GROUP_MAP[scene]
    const totalPages = Math.ceil(groups.length / 3)
    const pageGroups = useMemo(() => {
        const start = page * 3
        return groups.slice(start, start + 3)
    }, [page, groups])

    useEffect(() => {
        setPage(0)
    }, [scene])

    const canPrev = page > 0
    const canNext = page < totalPages - 1

    return (
        <div className="mt-10 w-full max-w-[960px] mx-auto">
            <div className="flex items-center justify-center gap-4 mb-6">
                <div className="h-px flex-1 max-w-[120px] bg-gradient-to-r from-transparent to-[var(--c-border)]"/>
                <div className="flex items-center gap-2 text-[var(--c-text-secondary)]">
                    <Sparkles size={16} className="text-[var(--c-accent)]"/>
                    <span className="text-[length:var(--fs-md)] font-medium">试试这些</span>
                </div>
                <div className="h-px flex-1 max-w-[120px] bg-gradient-to-l from-transparent to-[var(--c-border)]"/>
            </div>

            {/* 场景 Tab — 极简直觉：去容器化 + 发丝线分隔 + 渐变滑入指示条 */}
            <div className="flex justify-center mb-6">
                <div role="tablist" aria-label="场景切换" className="relative">
                    {/* 整体底部分隔发丝线：让指示条像"浮在"发丝线上，制造聚焦层级感 */}
                    <div className="absolute inset-x-2 bottom-0 h-px bg-[var(--c-border)] opacity-60" aria-hidden/>
                    <div className="flex items-center gap-1 px-1">
                        {SCENES.map((s) => {
                            const Icon = s.icon
                            const active = scene === s.key
                            return (
                                <button
                                    key={s.key}
                                    role="tab"
                                    aria-selected={active}
                                    type="button"
                                    onClick={() => setScene(s.key)}
                                    className={`group relative px-4 h-9 flex items-center gap-2
                                      transition-colors duration-300 ease-out
                                      ${active
                                        ? 'text-[var(--c-text)] font-semibold'
                                        : 'text-[var(--c-text-secondary)] hover:text-[var(--c-text)] font-medium'
                                      }`}
                                >
                                    <Icon
                                        size={14}
                                        strokeWidth={active ? 2 : 1.75}
                                        className={`shrink-0 transition-[strokeWidth,opacity,transform] duration-300 ease-out
                                            ${active ? 'opacity-100 text-[var(--c-accent)]' : 'opacity-70 group-hover:opacity-90 group-hover:-translate-y-[0.5px]'}`}
                                    />
                                    <span className="text-[length:var(--fs-md)] tracking-tight whitespace-nowrap">
                                        {s.label}
                                    </span>
                                    {/* 渐变指示条：选中时从中心 expand，非选中时 collapse */}
                                    <span
                                        aria-hidden
                                        className={`absolute left-3 right-3 -bottom-px h-[2px] rounded-full
                                          origin-center transition-all duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]
                                          ${active
                                            ? 'scale-x-100 opacity-100 bg-gradient-to-r from-transparent via-[var(--c-accent)] to-transparent'
                                            : 'scale-x-0 opacity-0 bg-[var(--c-accent)]/40 group-hover:scale-x-60 group-hover:opacity-60'
                                          }`}
                                    />
                                </button>
                            )
                        })}
                    </div>
                </div>
            </div>

            <div className="relative flex items-center gap-3">
                <button
                    type="button"
                    onClick={() => setPage((p) => Math.max(0, p - 1))}
                    disabled={!canPrev}
                    className="shrink-0 w-9 h-9 rounded-full border border-[var(--c-border)] bg-[var(--c-surface)]
                     text-[var(--c-text-secondary)] hover:text-[var(--c-text)] hover:bg-[var(--c-surface-2)]
                     transition-all disabled:opacity-30 disabled:cursor-not-allowed grid place-items-center"
                    aria-label="上一页"
                >
                    <ChevronLeft size={18}/>
                </button>

                <div className="flex-1 grid grid-cols-1 md:grid-cols-3 gap-4">
                    {pageGroups.map((group) => {
                        const Icon = group.icon
                        return (
                            <div
                                key={`${scene}-${group.type}`}
                                className="app-card app-card--passive p-4"
                            >
                                <div className="flex items-center gap-2.5 mb-3">
                                    <div className={`w-8 h-8 rounded-[var(--radius-md)] flex items-center justify-center bg-[var(--c-accent-soft)] text-[var(--c-accent)]`}>
                                        <Icon size={16}/>
                                    </div>
                                    <div className="text-[length:var(--fs-md)] font-semibold text-[var(--c-text)]">{group.type}</div>
                                </div>

                                <div className="space-y-1">
                                    {group.items.map((item, idx) => (
                                        <div key={`${scene}-${group.type}-${idx}`} className="relative group/tip">
                                            <button
                                                type="button"
                                                onClick={() => onPick(item)}
                                                className="w-full flex items-center justify-between gap-3 px-3 py-2.5 rounded-[var(--radius-md)] text-left
                                   text-[length:var(--fs-md)] leading-[1.6] text-[var(--c-text-secondary)]
                                   hover:text-[var(--c-text)] hover:bg-[var(--c-surface-2)]
                                   transition-colors cursor-pointer"
                                            >
                                                <span className="line-clamp-2 flex-1">{item}</span>
                                                <ChevronRight size={14} className="shrink-0 opacity-0 group-hover/tip:opacity-100 transition-opacity"/>
                                            </button>
                                            {/* Tooltip —— 系统级极简浮层（100% 对齐设计令牌，零自定义调色，零花样装饰）：
                                                  · 背景：亮色=var(--c-surface)纯白（和 macOS tooltip 一致） / 暗色=var(--c-surface-3) #1E1E1E（最深画布上的浮起表面）
                                                  · 边框：var(--c-border-strong) 14% 中性描边
                                                  · 阴影：与全局 app-card 完全一致的系统规格
                                                  · 圆角：12px（全局设计的卡片级圆角档位）
                                                  · 箭头：同背景/同边框，纯中性色衔接 */}
                                            <div
                                                role="tooltip"
                                                style={{ ['--tt-bg' as any]: 'var(--c-surface)' }}
                                                className="
                  pointer-events-none absolute left-1/2 z-[60] -translate-x-1/2
                  bottom-full mb-2.5
                  w-[min(480px,calc(100vw-2rem))]
                  rounded-[var(--radius-card)]
                  border border-[var(--c-border-strong)]
                  bg-[var(--tt-bg)]
                  dark:[--tt-bg:var(--c-surface-3)]
                  px-4.5 py-3.5
                  text-[length:var(--fs-md)] leading-[1.7] text-[var(--c-text)]
                  shadow-[0_0_0_1px_rgba(17,24,39,0.04),0_2px_8px_-2px_rgba(0,0,0,0.04)]
                  dark:shadow-[0_0_0_1px_rgba(255,255,255,0.04),0_8px_20px_-12px_rgba(0,0,0,0.45)]
                  opacity-0 translate-y-2 group-hover/tip:opacity-100 group-hover/tip:translate-y-0
                  transition-[opacity,transform] duration-180 ease-out
                  origin-bottom
                "
                                            >
                                                <p className="whitespace-pre-wrap break-words">{item}</p>
                                                {/* 指向箭头：背景读取 --tt-bg（亮/暗自动切换），边框与本体 border-strong 严格一致 */}
                                                <span
                                                    aria-hidden
                                                    className="absolute left-1/2 top-full -z-10"
                                                    style={{
                                                        width: 11,
                                                        height: 11,
                                                        transform: 'translate(-50%, -50%) rotate(45deg)',
                                                        background: 'var(--tt-bg)',
                                                        borderRight: '1px solid var(--c-border-strong)',
                                                        borderBottom: '1px solid var(--c-border-strong)',
                                                    }}
                                                />
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )
                    })}
                </div>

                <button
                    type="button"
                    onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                    disabled={!canNext}
                    className="shrink-0 w-9 h-9 rounded-full border border-[var(--c-border)] bg-[var(--c-surface)]
                     text-[var(--c-text-secondary)] hover:text-[var(--c-text)] hover:bg-[var(--c-surface-2)]
                     transition-all disabled:opacity-30 disabled:cursor-not-allowed grid place-items-center"
                    aria-label="下一页"
                >
                    <ChevronRight size={18}/>
                </button>
            </div>

            <div className="flex justify-center gap-1.5 mt-4">
                {Array.from({length: totalPages}).map((_, idx) => (
                    <button
                        key={`${scene}-dot-${idx}`}
                        type="button"
                        onClick={() => setPage(idx)}
                        className={`w-2 h-2 rounded-full transition-all ${
                            idx === page ? 'bg-[var(--c-accent)] w-4' : 'bg-[var(--c-border)] hover:bg-[var(--c-text-secondary)]'
                        }`}
                        aria-label={`跳转到第 ${idx + 1} 页`}
                    />
                ))}
            </div>
        </div>
    )
}
