import { ChevronLeft, ChevronRight, Sparkles } from 'lucide-react'
import { RefreshIcon } from '../icons/SystemIcons'
import {useCallback, useEffect, useMemo, useRef, useState} from 'react'
import suggestions from '../../data/emptyStateSuggestions.json'

/**
 * 空会话示例区。
 * 场景默认选中第一个；分组默认选中「全部」（不筛选），此时各分组轮换展示一条案例。
 * 文案维护在 src/data/emptyStateSuggestions.json。
 */

interface SuggestionItem {
    /** 列表展示的短标题；缺省时展示 prompt */
    title?: string
    /** 点击后回填到输入框的完整内容 */
    prompt: string
}

interface SuggestionGroup {
    title: string
    items: SuggestionItem[]
}

interface SuggestionScene {
    key: string
    label: string
    groups: SuggestionGroup[]
}

const SCENES = (suggestions as { scenes: SuggestionScene[] }).scenes

interface Props {
    onPick: (prompt: string) => void
}

export default function EmptyStateSuggestions({onPick}: Props) {
    const [sceneKey, setSceneKey] = useState(SCENES[0]?.key ?? 'medical')
    /** null = 不筛选分组 */
    const [groupTitle, setGroupTitle] = useState<string | null>(null)
    const [seed, setSeed] = useState(0)
    const railRef = useRef<HTMLDivElement>(null)
    const [canLeft, setCanLeft] = useState(false)
    const [canRight, setCanRight] = useState(false)

    const scene = useMemo(
        () => SCENES.find((s) => s.key === sceneKey) ?? SCENES[0],
        [sceneKey],
    )
    const groups = useMemo(
        () => (scene?.groups ?? []).filter((g) => g.items.length > 0),
        [scene],
    )
    const activeGroup = groupTitle
        ? groups.find((g) => g.title === groupTitle) ?? null
        : null

    useEffect(() => {
        setGroupTitle(null)
        setSeed(0)
    }, [sceneKey])

    /** 未筛选：每个分组各取一条；已筛选：展示该组全部 */
    const rows = useMemo(() => {
        if (!groups.length) return []
        if (activeGroup) {
            if (!activeGroup.items.length) return []
            const offset = seed % activeGroup.items.length
            return activeGroup.items.map((_, i) => ({
                group: undefined as string | undefined,
                item: activeGroup.items[(i + offset) % activeGroup.items.length],
            }))
        }
        return groups.map((g) => ({
            group: g.title as string | undefined,
            item: g.items[seed % g.items.length],
        }))
    }, [groups, activeGroup, seed])

    const syncArrows = useCallback(() => {
        const el = railRef.current
        if (!el) return
        setCanLeft(el.scrollLeft > 2)
        setCanRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 2)
    }, [])

    useEffect(() => {
        const el = railRef.current
        if (!el) return
        syncArrows()
        const ro = new ResizeObserver(syncArrows)
        ro.observe(el)
        return () => ro.disconnect()
    }, [syncArrows, groups])

    const scrollRail = (dir: -1 | 1) => {
        railRef.current?.scrollBy({left: dir * 220, behavior: 'smooth'})
    }

    return (
        <div className="mt-8 w-full">
            <div className="mb-3 flex items-center justify-center gap-4 text-[var(--c-text-secondary)]">
                <span
                    aria-hidden
                    className="h-px w-[120px] bg-gradient-to-r from-transparent to-[var(--c-border)]"
                />
                <div className="flex shrink-0 items-center gap-2">
                    <Sparkles size={16} className="text-[var(--c-accent)]"/>
                    <span className="text-[length:var(--fs-md)] font-medium">试一试</span>
                </div>
                <span
                    aria-hidden
                    className="h-px w-[120px] bg-gradient-to-l from-transparent to-[var(--c-border)]"
                />
            </div>

            <div
                role="tablist"
                aria-label="场景切换"
                className="flex items-center justify-center gap-[22px] overflow-x-auto
                 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
            >
                {SCENES.map((s) => {
                    const active = sceneKey === s.key
                    return (
                        <button
                            key={s.key}
                            role="tab"
                            aria-selected={active}
                            type="button"
                            onClick={() => setSceneKey(s.key)}
                            className={`relative inline-flex h-8 shrink-0 items-center justify-center whitespace-nowrap
                              text-[length:var(--fs-md)] transition-colors duration-200 cursor-pointer
                              ${active
                                ? 'font-semibold text-[var(--c-accent)]'
                                : 'font-normal text-[var(--c-text-secondary)] hover:text-[var(--c-text)]'
                              }`}
                        >
                            {s.label}
                            {active && (
                                <span
                                    aria-hidden
                                    className="pointer-events-none absolute bottom-0 left-1 right-1 h-[2px] rounded-full bg-[var(--c-accent)]"
                                />
                            )}
                        </button>
                    )
                })}
            </div>

            <div className="relative mt-2">
                <div
                    ref={railRef}
                    onScroll={syncArrows}
                    className="flex items-center gap-[18px] overflow-x-auto pl-7
                     [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
                >
                    {/* 「全部」= 不筛选分组，各分组轮换展示一条案例，默认选中 */}
                    <button
                        type="button"
                        aria-pressed={activeGroup === null}
                        onClick={() => {
                            setGroupTitle(null)
                            setSeed(0)
                        }}
                        className={`relative inline-flex h-8 shrink-0 items-center justify-center whitespace-nowrap
                          text-[length:var(--fs-md)] transition-colors cursor-pointer
                          ${activeGroup === null
                            ? 'font-medium text-[var(--c-accent)]'
                            : 'text-[var(--c-text-secondary)] hover:text-[var(--c-text)]'
                          }`}
                    >
                        全部
                        {activeGroup === null && (
                            <span
                                aria-hidden
                                className="pointer-events-none absolute bottom-0 left-1 right-1 h-[2px] rounded-full bg-[var(--c-accent)]"
                            />
                        )}
                    </button>
                    {groups.map((g) => {
                        const active = activeGroup?.title === g.title
                        return (
                            <button
                                key={`${scene?.key}-${g.title}`}
                                type="button"
                                aria-pressed={active}
                                onClick={() => {
                                    setGroupTitle(active ? null : g.title)
                                    setSeed(0)
                                }}
                                className={`relative inline-flex h-8 shrink-0 items-center justify-center whitespace-nowrap
                                  text-[length:var(--fs-md)] transition-colors cursor-pointer
                                  ${active
                                    ? 'font-medium text-[var(--c-accent)]'
                                    : 'text-[var(--c-text-secondary)] hover:text-[var(--c-text)]'
                                  }`}
                            >
                                {g.title}
                                {active && (
                                    <span
                                        aria-hidden
                                        className="pointer-events-none absolute bottom-0 left-1 right-1 h-[2px] rounded-full bg-[var(--c-accent)]"
                                    />
                                )}
                            </button>
                        )
                    })}
                </div>

                {canLeft && (
                    <button
                        type="button"
                        onClick={() => scrollRail(-1)}
                        aria-label="向左查看更多分组"
                        className="absolute left-0 top-1/2 -translate-y-1/2 grid place-items-center w-6 h-6 rounded-full
                         bg-[var(--c-surface)] text-[var(--c-text-secondary)]
                         hover:text-[var(--c-text)] transition-colors cursor-pointer"
                    >
                        <ChevronLeft size={14}/>
                    </button>
                )}
                {canRight && (
                    <button
                        type="button"
                        onClick={() => scrollRail(1)}
                        aria-label="向右查看更多分组"
                        className="absolute right-0 top-1/2 -translate-y-1/2 grid place-items-center w-6 h-6 rounded-full
                         bg-[var(--c-surface)] text-[var(--c-text-secondary)]
                         hover:text-[var(--c-text)] transition-colors cursor-pointer"
                    >
                        <ChevronRight size={14}/>
                    </button>
                )}
            </div>

            {/* 分组行高 32px、案例行高 42px，两者的文字都是垂直居中：
                只留 4px 会让「分组下划线 → 首条案例」比「案例之间」窄 6px，
                这里补到 10px，让下划线到首条案例的留白与案例间距一致。 */}
            <div className="-mx-3 mt-2.5 flex flex-col pl-7">
                {rows.map((row) => (
                    <button
                        key={`${row.group ?? 'flat'}-${row.item.prompt}`}
                        type="button"
                        onClick={() => onPick(row.item.prompt)}
                        title={row.item.title ? row.item.prompt : undefined}
                        className="group flex min-h-[42px] w-full items-center gap-3 rounded-[8px] px-3 py-2
                         text-left transition-colors hover:bg-[var(--c-hover-bg)] cursor-pointer"
                    >
                        {row.group && (
                            <>
                                <span className="shrink-0 whitespace-nowrap text-[length:var(--fs-md)]
                                 font-medium leading-[1.5] text-[var(--c-text)]">
                                    {row.group}
                                </span>
                                <span aria-hidden className="h-4 w-px shrink-0 bg-[var(--c-border)]"/>
                            </>
                        )}
                        <span className={`min-w-0 flex-1 text-[length:var(--fs-md)] leading-[1.55]
                          ${row.group ? 'text-[var(--c-text-secondary)]' : 'text-[var(--c-text)]'}`}>
                            {row.item.title || row.item.prompt}
                        </span>
                        <ChevronRight
                            size={15}
                            className="shrink-0 text-[var(--c-text-tertiary)] opacity-0 transition-all
                             group-hover:translate-x-0.5 group-hover:text-[var(--c-accent)] group-hover:opacity-100"
                        />
                    </button>
                ))}
            </div>

            <div className="mt-1 pl-7">
                <button
                    type="button"
                    onClick={() => setSeed((s) => s + 1)}
                    className="inline-flex h-9 items-center gap-[8px] text-[length:var(--fs-sm)]
                     text-[var(--c-text-tertiary)] transition-colors hover:text-[var(--c-text)] cursor-pointer"
                >
                    <RefreshIcon size={14} className="shrink-0"/>
                    <span className="leading-none">换一换</span>
                </button>
            </div>
        </div>
    )
}
