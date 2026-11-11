import { useCallback, useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'

/**
 * 聊天滚动控制器 —— 三态状态机，ChatArea 的全部滚动行为收敛于此。
 *
 *   bottom  跟随底部（历史载入 / 会话切换 / 用户点了回底之后的流式输出）
 *   pinned  问题钉顶（发问即进入：问题对齐视口顶部 + 自适应留白补足一屏，
 *           自愈循环每帧重钉，覆盖「AI 正在处理」无 token 窗口）。
 *           生成期间不自动滚底 —— 用户先看前面的内容。
 *   reading 用户滚离阅读（pinned 期间用户主动滚动：自由阅读、不重钉、
 *           不回底、留白缺口不塌）
 *
 * 迁移关系：
 *   bottom  --pinNewQuestion--> pinned
 *   pinned  --用户滚动偏离锚点>8px（须伴随 wheel/触摸/导航键等真实输入）--> reading
 *   reading --用户滚回锚点<=8px--> pinned
 *   pinned|reading --scrollToBottom()--> bottom（按钮点击：滚到底并贴底跟随）
 *   pinned|reading --isStreaming 变 false--> bottom（释放、留白塌除）
 *
 * 迁移的输入门控：浏览器 scroll-anchoring（配合 .chat-scroller 的
 * overflow-anchor:none 已禁用）与回流钳制仍可能产生无输入事件的程序性
 * scrollTop 变化，仅凭位移阈值判「用户滚离」会误杀自愈循环。
 *
 * 底部悬浮按钮（本体在 ChatArea）只有两态，永不向上定位：
 *   pinned   → spinner +「AI 正在处理」loading 态，点击滚动到底部；
 *   其余未贴底 → 「回到底部」，点击一键回底。
 *
 * 设计要点：
 * - pinned 的重钉用独立 rAF 循环而非 layout effect 每 token 重跑：循环不随 token
 *   重建，不存在 cleanup 取消 rAF 导致重钉永不执行的问题；同时覆盖无 token 的
 *   「AI 正在处理」窗口（虚拟器异步重测造成短暂空白，1-2 帧内自愈）。
 * - 留白占位是自适应缺口而非固定一整屏：spacer = max(0, 视口高 − 问题顶到
 *   内容末尾的距离)。发送瞬间补足一屏（问题立即可达视口顶部）；回答流式
 *   展开 1:1 消耗缺口（问题下方总高度恒定，滚动无跳变）；回答超过一屏后
 *   缺口归零——滚动范围终结在真实内容末尾。固定一整屏会把 maxScrollTop
 *   顶到内容末尾之外：滚到底 = 整屏纯空白、真实内容全部藏进上方隐藏区
 *   （2026-09-02 用户实测反馈）。
 * - pinned/reading 期间 handleScroll 只做模式迁移，绝不自动回底。
 * - 回流钳制判定：本帧 scrollHeight 较上一值减小 ⇒ 虚拟器重测引起的被动位移，
 *   不算用户滚动，不触发模式迁移。
 */

export type ChatScrollMode = 'bottom' | 'pinned' | 'reading'

const STICK_TO_BOTTOM_THRESHOLD_PX = 80
const PIN_DISPLACE_THRESHOLD_PX = 8
const QUIET_WINDOW_MS = 350
/** 用户输入有效期：窗口内视为「用户正在滚动」，自愈循环让位、迁移放行 */
const USER_INTENT_WINDOW_MS = 500

export interface ChatScrollControllerOptions {
  scrollerRef: RefObject<HTMLDivElement | null>
  isStreaming: boolean
  /** 底部跟随的重算信号：items 数 / 流式文本 / 工具调用等任一变化触发一次对齐 */
  activityKey: string
  /** 底部对齐后的虚拟器精修（scrollToIndex align:'end'），由持有 virtualizer 的一侧提供 */
  refineScrollToEnd?: () => void
  /** 会话标识：变化时整体重置状态机。ChatArea 不按会话 remount，pinned/锚点/
   *  留白会残留到新会话（新会话首条消息被错误钉顶 + 一整屏空白），必须重置。 */
  resetKey?: string
}

export interface ChatScrollController {
  mode: ChatScrollMode
  /** 距底部 <= 阈值（未贴底时显示「回到底部」按钮） */
  atBottom: boolean
  /** 内容超出视口（驱动底部状态指示条） */
  contentOverflow: boolean
  /** pinned/reading 期间的自适应留白缺口（不足一屏的补足量），bottom 模式为 0 */
  spacerHeight: number
  handleScroll: () => void
  /** 发问即调：新用户消息在 items 中的索引。所有发问入口统一走这里 */
  pinNewQuestion: (index: number) => void
  /** 一键回到底部：退出 pinned/reading 回 bottom 并贴底跟随（按钮唯一动作，不做向上定位） */
  scrollToBottom: () => void
}

export function useChatScrollController(
  options: ChatScrollControllerOptions,
): ChatScrollController {
  const { scrollerRef, isStreaming, activityKey } = options

  const [mode, setMode] = useState<ChatScrollMode>('bottom')
  const [atBottom, setAtBottom] = useState(true)
  const [contentOverflow, setContentOverflow] = useState(false)
  const [spacerHeight, setSpacerHeight] = useState(0)

  // 回调/effect 内读最新模式用（setState 异步提交，闭包里的 mode 会过期）
  const modeRef = useRef(mode)
  modeRef.current = mode
  const anchorIndexRef = useRef<number | null>(null)
  const anchorScrollTopRef = useRef(0)
  const scrollFrameRef = useRef<number | null>(null)
  const quietUntilRef = useRef(0)
  const lastScrollTopRef = useRef(0)
  const lastContentHeightRef = useRef(0)
  // 最近一次真实用户输入时间戳（wheel/触摸/导航键/滚动条拖拽）。pinned→reading
  // 迁移的唯一合法来源：浏览器 scroll-anchoring 与回流钳制产生的程序性 scrollTop
  // 变化没有对应输入事件，若仅凭 dev>8 判「用户滚离」会误杀自愈重钉循环
  // （症状：钉顶问题被推出视口 + 生成中胶囊误显「回到底部」，2026-09-02 实测回归）。
  const lastUserInputAtRef = useRef(0)

  const refineRef = useRef(options.refineScrollToEnd)
  refineRef.current = options.refineScrollToEnd

  // 会话切换重置：清锚点/簿记、回 bottom 贴底。放在其它 effect 之前声明，
  // 保证同一提交内先于流式释放 effect 执行（释放看到 mode 已是 bottom 即早退）。
  useEffect(() => {
    anchorIndexRef.current = null
    anchorScrollTopRef.current = 0
    lastScrollTopRef.current = 0
    lastContentHeightRef.current = 0
    modeRef.current = 'bottom'
    setAtBottom(true)
    setSpacerHeight(0)
    setMode('bottom')
  }, [options.resetKey])

  const markProgrammatic = useCallback(() => {
    quietUntilRef.current = performance.now() + QUIET_WINDOW_MS
  }, [])

  // 用户输入标记：监听挂 window（capture + passive），运行时用 scrollerRef.current
  // 动态 contains 判定——landing 与 default 是两棵 JSX 树、两个不同的
  // .chat-scroller 节点，首条消息后布局切换节点替换，挂元素级监听会随旧节点
  // 失效（ref 对象恒定，effect 不会重跑；2026-09-02 实测 wheel 逃逸因此失效）。
  // 导航键挂 window（Space/PageUp/PageDown/Home/End/方向键；打字键不标记，
  // 但误标记无害——迁移还需 dev>8 同时成立）。
  useEffect(() => {
    const inScroller = (target: EventTarget | null): boolean => {
      const el = scrollerRef.current
      if (!el || !(target instanceof Node)) return false
      return target === el || el.contains(target)
    }
    const markUser = () => { lastUserInputAtRef.current = performance.now() }
    const onWheel = (e: WheelEvent) => { if (inScroller(e.target)) markUser() }
    const onTouch = (e: TouchEvent) => { if (inScroller(e.target)) markUser() }
    const onMouse = (e: MouseEvent) => { if (inScroller(e.target)) markUser() }
    const NAV_KEYS = new Set(['PageUp', 'PageDown', 'Home', 'End', 'ArrowUp', 'ArrowDown', ' '])
    const onKey = (e: KeyboardEvent) => { if (NAV_KEYS.has(e.key)) markUser() }
    window.addEventListener('wheel', onWheel, { passive: true, capture: true })
    window.addEventListener('touchstart', onTouch, { passive: true, capture: true })
    window.addEventListener('mousedown', onMouse, true)
    window.addEventListener('keydown', onKey, true)
    return () => {
      window.removeEventListener('wheel', onWheel, true)
      window.removeEventListener('touchstart', onTouch, true)
      window.removeEventListener('mousedown', onMouse, true)
      window.removeEventListener('keydown', onKey, true)
    }
  }, [scrollerRef])

  /** bottom 模式对齐底部；refineScrollToEnd 做虚拟器精修 */
  const scrollToEnd = useCallback(() => {
    const el = scrollerRef.current
    if (!el) return
    markProgrammatic()
    el.scrollTop = el.scrollHeight
    lastScrollTopRef.current = el.scrollTop
    refineRef.current?.()
  }, [scrollerRef, markProgrammatic])

  /** 把锚定问题对齐到视口顶部（纯 DOM 对齐，幂等，每帧可安全重复调用）。
   *  不用 virtualizer.scrollToIndex：内容不足时会钳制到 maxScrollTop，且受浏览器
   *  scroll-anchoring 回拉影响。[data-index] 元素用 translateY 定位、offsetTop 恒 0，
   *  必须用 getBoundingClientRect 取真实偏移。 */
  const pinToAnchor = useCallback(() => {
    const el = scrollerRef.current
    const idx = anchorIndexRef.current
    if (!el || idx === null) return
    const q = el.querySelector(`[data-index="${idx}"]`)
    if (!q) return
    const target = q.getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop
    if (Math.abs(target - el.scrollTop) > 1) el.scrollTop = target
    lastScrollTopRef.current = el.scrollTop
    anchorScrollTopRef.current = el.scrollTop
  }, [scrollerRef])

  /** 重算自适应留白缺口：spacer = max(0, 视口高 − 问题顶到内容末尾的距离)。
   *  below（问题顶到内容末尾）用真实 DOM 矩形差求——[data-index] 元素以
   *  translateY 定位、offsetTop 恒 0；两矩形同帧相减与滚动位置无关。
   *  问题元素未渲染（用户滚到远处、被虚拟器卸载）时维持上次值：缺口是
   *  「滚回锚点仍可置顶」的保证，不能凭空塌掉。
   *  回答超过一屏后 below ≥ 视口高 → 缺口 0，滚动范围终结在真实内容末尾。 */
  const measureSpacer = useCallback(() => {
    const el = scrollerRef.current
    const idx = anchorIndexRef.current
    if (!el || idx === null) return
    const q = el.querySelector(`[data-index="${idx}"]`)
    const container = q?.parentElement ?? null
    if (!q || !container) return
    const below = Math.max(
      0,
      container.getBoundingClientRect().bottom - q.getBoundingClientRect().top,
    )
    const next = Math.max(0, el.clientHeight - below)
    setSpacerHeight((prev) => (prev === next ? prev : next))
  }, [scrollerRef])

  // pinned 自愈重钉循环：pinned 期间每帧把问题对齐到视口顶部（含流式与「AI 正在处理」窗口）。
  // 让位规则：近期有真实用户输入且已偏离锚点（用户滚轮/触摸正滚离）时不回拽——
  // 否则循环会在 pinned→reading 迁移的提交窗口内当帧回拽，回拽的 scroll 事件
  // （dev=0）又把 reading→pinned 翻回，滚轮逃逸被完全中和（2026-09-02 实测）。
  useEffect(() => {
    if (mode !== 'pinned') return
    let raf = 0
    const tick = () => {
      if (modeRef.current !== 'pinned') return
      const el = scrollerRef.current
      const userLeaving =
        el !== null &&
        performance.now() - lastUserInputAtRef.current < USER_INTENT_WINDOW_MS &&
        Math.abs(el.scrollTop - anchorScrollTopRef.current) > PIN_DISPLACE_THRESHOLD_PX
      if (!userLeaving) pinToAnchor()
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [mode, pinToAnchor, scrollerRef])

  // 发问 → pinned：同步取消尚未执行的底部跟随帧（布局 effect 可能已在本提交排帧，
  // 不取消会把刚发出的新问题拽回底部），切换模式；留白先取一整屏基线（保证首个
  // pinned 渲染即可置顶），随后重测 effect 收敛到自适应缺口。缺口随回答流式
  // 展开 1:1 收缩，回答超过一屏后归零。
  const pinNewQuestion = useCallback(
    (index: number) => {
      if (scrollFrameRef.current !== null) {
        cancelAnimationFrame(scrollFrameRef.current)
        scrollFrameRef.current = null
      }
      anchorIndexRef.current = index
      anchorScrollTopRef.current = 0
      const baseline =
        scrollerRef.current?.clientHeight ||
        (typeof window !== 'undefined' ? window.innerHeight : 0) ||
        600
      setSpacerHeight(baseline)
      // 同步写 ref：setMode 异步提交，期间 programmatic 滚动的 scroll 事件
      // 若读到旧 mode（bottom）会走贴底重算/误迁移，必须立刻生效。
      modeRef.current = 'pinned'
      setAtBottom(false)
      setMode('pinned')
    },
    [scrollerRef],
  )

  // 流式结束 → bottom：释放锚定、留白塌除，按当前位置重算贴底状态。
  // 用户停在顶部时 atBottom=false → 按钮显示「回到底部」。
  useEffect(() => {
    if (isStreaming) return
    if (modeRef.current === 'bottom') return
    anchorIndexRef.current = null
    const el = scrollerRef.current
    if (el) {
      setAtBottom(
        el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_TO_BOTTOM_THRESHOLD_PX,
      )
    }
    setSpacerHeight(0)
    setMode('bottom')
  }, [isStreaming, scrollerRef])

  // 自适应留白重测（pinned/reading 期间）：内容变化（token/工具调用/条目数）
  // → below 增大 → 缺口收缩。进入 pinned 的首个提交也在此完成从整屏基线到
  // 精确缺口的收敛。setState 同值跳过，token 不改变缺口时不触发渲染。
  useEffect(() => {
    if (mode === 'bottom') return
    measureSpacer()
  }, [mode, activityKey, measureSpacer])

  // 留白缺口重测触发器（pinned/reading 期间）：
  //   ① 虚拟列表容器（q.parentElement）尺寸变化 → 虚拟器异步测量修正；
  //   ② scroller 自身尺寸变化 → 视口高（ch）变化（窗口缩放、输入框自动增高
  //      占据底部空间），缺口必须随之下调/上调，否则 ANALYZING 窗口内会出现
  //      「上次旧值」造成的瞬时死白或钉顶钳制——与本修复初衷背离。
  //   ③ 同步首次 measure：首 attach 时容器与 scroller 尚未触发 RO，先取一次基线。
  //   ResizeObserver 自身不会循环（spacer 是容器的兄弟节点，不影响其尺寸）。
  useEffect(() => {
    if (mode === 'bottom') return
    const el = scrollerRef.current
    const idx = anchorIndexRef.current
    if (!el || idx === null || typeof ResizeObserver === 'undefined') return
    const q = el.querySelector(`[data-index="${idx}"]`)
    const container = q?.parentElement ?? null
    if (!container) return
    measureSpacer()
    const ro = new ResizeObserver(() => measureSpacer())
    ro.observe(container)
    ro.observe(el)
    return () => ro.disconnect()
  }, [mode, measureSpacer, scrollerRef])

  // bottom 模式底部跟随：内容变化（历史载入 / 流式 token / 用户点了回底）→ rAF 对齐底部。
  // pinned/reading 不进入 —— 生成期间不自动滚底，用户先看前面的内容。
  useEffect(() => {
    if (mode !== 'bottom' || !atBottom) return
    if (scrollFrameRef.current !== null) return
    scrollFrameRef.current = requestAnimationFrame(() => {
      scrollFrameRef.current = null
      scrollToEnd()
      requestAnimationFrame(() => scrollToEnd())
    })
    return () => {
      if (scrollFrameRef.current !== null) {
        cancelAnimationFrame(scrollFrameRef.current)
        scrollFrameRef.current = null
      }
    }
  }, [activityKey, mode, atBottom, scrollToEnd])

  // 内容溢出测量（驱动底部状态指示条）。绘制后运行 + ResizeObserver 兜底重测；
  // setState 走常规渲染并同值跳过，规避 "Maximum update depth exceeded" 循环。
  useEffect(() => {
    const el = scrollerRef.current
    if (!el) return
    let raf = 0
    const measure = () => {
      raf = 0
      const next = el.scrollHeight - el.clientHeight > 4
      setContentOverflow((prev) => (prev === next ? prev : next))
    }
    const schedule = () => {
      if (!raf) raf = requestAnimationFrame(measure)
    }
    schedule()
    let ro: ResizeObserver | null = null
    const inner = el.firstElementChild
    if (inner && typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(schedule)
      ro.observe(inner)
    }
    return () => {
      if (raf) cancelAnimationFrame(raf)
      ro?.disconnect()
    }
  }, [activityKey, mode, scrollerRef])

  const handleScroll = useCallback(() => {
    const el = scrollerRef.current
    if (!el) return
    const curTop = el.scrollTop
    const prevTop = lastScrollTopRef.current
    lastScrollTopRef.current = curTop
    const curContentHeight = el.scrollHeight
    const reflowShrink = curContentHeight < lastContentHeightRef.current - 1
    lastContentHeightRef.current = curContentHeight

    const m = modeRef.current
    if (m === 'pinned' || m === 'reading') {
      // 置顶/阅读模式簿记：只做模式迁移，不重算贴底（杜绝自动回底）、不干预滚动位置。
      // 自愈循环每帧对齐锚点，程序性滚动偏差≈0；偏差>8px 且近期有真实用户输入
      // 才判「用户滚离」——scroll-anchoring/回流钳制的程序性位移无输入事件，
      // 不得迁移（迁移会停掉自愈循环，问题将永久脱离视口顶部）。
      if (!reflowShrink) {
        const userIntent = performance.now() - lastUserInputAtRef.current < USER_INTENT_WINDOW_MS
        const dev = Math.abs(curTop - anchorScrollTopRef.current)
        if (m === 'pinned' && userIntent && dev > PIN_DISPLACE_THRESHOLD_PX) setMode('reading')
        else if (m === 'reading' && dev <= PIN_DISPLACE_THRESHOLD_PX) setMode('pinned')
      }
      // 用户滚动会把锚点问题带回/带离虚拟渲染窗口，滚动中顺带重测缺口
      // （同值跳过，问题未渲染时维持上次值）。
      measureSpacer()
      return
    }

    // bottom 模式：用户上滚绕过 quiet window 立即响应，
    // 否则程序性底部对齐刷新的 quiet window 会永久屏蔽用户上滚。
    const userScrolledUp = curTop < prevTop - 3
    if (!userScrolledUp && performance.now() < quietUntilRef.current) return
    setAtBottom(el.scrollHeight - curTop - el.clientHeight <= STICK_TO_BOTTOM_THRESHOLD_PX)
    setContentOverflow(el.scrollHeight - el.clientHeight > 4)
  }, [scrollerRef, measureSpacer])

  /** 一键回到底部：退出 pinned/reading → bottom 贴底跟随。按钮唯一动作，不做向上定位。 */
  const scrollToBottom = useCallback(() => {
    anchorIndexRef.current = null
    // 同步写 ref：否则程序性滚动触发的 scroll 事件在 setMode 提交前派发时，
    // modeRef 仍是 pinned/reading，会误判「偏离锚点」迁移进 reading，导致不贴底跟随。
    modeRef.current = 'bottom'
    setAtBottom(true)
    setSpacerHeight(0)
    setMode('bottom')
    scrollToEnd()
  }, [scrollToEnd])

  return {
    mode,
    atBottom,
    contentOverflow,
    spacerHeight: mode === 'bottom' ? 0 : spacerHeight,
    handleScroll,
    pinNewQuestion,
    scrollToBottom,
  }
}
