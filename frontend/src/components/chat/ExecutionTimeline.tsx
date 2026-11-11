import { useEffect, useMemo, useRef, useState } from 'react'
import { CheckCircle2, ChevronDown, Snowflake, Sparkles } from 'lucide-react'
import { StepDetailList } from './StepDetailList'
import { useCompletingTransition } from '../../hooks/chat/useCompletingTransition'
import type { TimelineNode } from '../../types'
import { buildStepRows, buildTimelineSummary } from '../../lib/chat/stepRows'

/* ─── ExecutionTimeline（执行过程视图，2026-09-03 轻/重路径合并）──────────────
 *
 * 原为两个互斥组件（LightToolSummary / StepTimeline），由 MessageBubble 按
 * isReadOnlyTimeline 三元选择。该判定在流式期间会翻转（一旦出现思考、写入、
 * 旁白等即由轻转重），导致两个组件整体对调：DOM 根节点被替换、入场动画重播、
 * 用户已展开的分组被重置——即用户反馈的「页面突然就切换」。
 *
 * 合并后判定结果降级为 weight 参数，只影响样式与可选子元素（完成行、连接线），
 * DOM 根节点身份稳定，weight 变化不再销毁重建。
 *
 *   L1 感知层（流式期间）：折叠头 + 累加步骤列表（StepList），逐行追加
 *                          （✓/⟳/✕/○），尾部跟当前相位行或完成行。
 *   L2 理解层（执行结束）：折叠头 + 步骤详情列表（StepDetailList），默认折叠，
 *                          展开显示该步骤内的思考 + 操作 + 检索/阅读对象。
 * ────────────────────────────────────────────────────────────────────────── */

interface Props {
  nodes: TimelineNode[]
  /** 渲染权重：light = 纯只读操作的紧凑摘要；heavy = 含思考/写入/技能的完整过程 */
  weight: 'light' | 'heavy'
  /** 流式执行中 → 渲染 L1；结束 → L2 */
  isExecuting?: boolean
  /** 答案生成中已开始吐字（调用方 MessageBubble 传入）→ L1 显示「正在组织回答…」 */
  isNarrating?: boolean
  /** runGate：HITL 单一真相源。phase 已不承载 awaiting_clarification 语义 */
  runGate?: 'awaiting_clarification' | 'awaiting_confirmation' | null
  /** 心跳秒表当前值（usePhaseClock 前端每秒推进），与 phase 配对（仅 L1 消费） */
  elapsedS?: number
  /** 重路径的外层间距（轻路径一律用更紧凑的内置间距 mb-3） */
  className?: string
}

/* ─── 完成状态条（仅重路径；设计图标准：灰色勾 + 「执行完成」，无耗时） ─── */

function DoneEntry() {
  // 延迟 120ms 显示，跳过 finalizeStreaming → appendMessage 之间的中间帧，
  // 避免"执行完成"在空 timeline 上短暂闪烁。
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    const timer = setTimeout(() => setVisible(true), 120)
    return () => clearTimeout(timer)
  }, [])
  if (!visible) return null
  return (
    <div className="flex items-start gap-4 mt-4">
      <div className="shrink-0 w-4">
        <div className="flex items-center justify-center h-[22px]">
          <CheckCircle2 size={16} strokeWidth={2} className="shrink-0 text-[var(--c-text-secondary)]/70" />
        </div>
      </div>
      <div className="flex-1 min-w-0">
        <span className="block truncate text-[length:var(--fs-sm)] leading-[22px] font-normal text-[var(--c-text-secondary)]/85">
          执行完成
        </span>
      </div>
    </div>
  )
}

export default function ExecutionTimeline({
  nodes,
  weight,
  isExecuting,
  isNarrating = false,
  runGate = null,
  elapsedS = 0,
  className,
}: Props) {
  const light = weight === 'light'

  // 展开状态在关键生命周期点由系统重置，用户可在任意时刻手动 toggle：
  // - 新一轮开始（isExecuting false→true）：重置为展开（展示执行过程）
  // - 执行完成（isExecuting true→false）：自动折叠（收起执行过程）
  // - 用户手动 toggle 后：跟随用户选择，直到下一轮开始
  const wasOpenRef = useRef(false)
  const [userOpen, setUserOpen] = useState(true)
  const prevExecutingRef = useRef(false)

  useEffect(() => {
    const prev = prevExecutingRef.current
    const exec = !!isExecuting
    if (exec && !prev) {
      // 新一轮开始：重置为展开（展示执行过程）
      wasOpenRef.current = false
      setUserOpen(true)
    } else if (!exec && prev) {
      // 执行完成：自动折叠（收起整个思考执行区域）
      wasOpenRef.current = true
      setUserOpen(false)
    }
    prevExecutingRef.current = exec
  }, [isExecuting])

  const effectiveOpen = wasOpenRef.current ? userOpen : true

  // 把 orphan_operations 的 iterations 并入其前置 step，消除「未归组操作」卡片。
  // 用浅拷贝替换而非原地 mutate —— nodes 来自 store，直接改写共享对象会在
  // memo 重跑时对同一 step 反复追加 iterations，且违反 React 数据不可变约定。
  // 无前置 step 的 orphan 保持独立节点（StepDetailList 的 head-orphan 分支
  // 会为它生成独立步骤行）。
  const displayNodes = useMemo(() => {
    const result: TimelineNode[] = []
    for (const n of nodes) {
      if (n.type === 'orphan_operations') {
        let merged = false
        for (let j = result.length - 1; j >= 0; j--) {
          if (result[j].type === 'step') {
            const prevStep = result[j] as Extract<TimelineNode, { type: 'step' }>
            result[j] = { ...prevStep, iterations: [...prevStep.iterations, ...n.iterations] }
            merged = true
            break
          }
        }
        if (merged) continue
      }
      result.push(n)
    }
    return result
  }, [nodes])

  // 把执行→静态拆为三态：active（流式步骤列表）/ completing（刚结束，末尾
  // 追加「执行完成 · 共 Xs」短暂停留）/ done（卸载 L1，交 L2）。避免 React
  // 早返回硬切导致 L1 消失后 L2 瞬间出现的无过渡跳变。
  const transition = useCompletingTransition(isExecuting)

  // rows/summary 在 L1 和 L2 都需要，提前计算
  const rows = useMemo(() => buildStepRows(displayNodes), [displayNodes])
  const summary = useMemo(() => buildTimelineSummary(rows), [rows])

  const toggleOpen = () => {
    wasOpenRef.current = true
    setUserOpen((v) => !v)
  }

  // 折叠头（设计图标准）：无图标，深色标题 + 紧随文本的灰色 chevron。
  // 用返回元素的函数而非子组件——子组件会在每次 render 产生新类型标识，React
  // 判定为不同类型而重建 DOM，正是本组件要消除的跳变。
  const renderHeader = () => (
    <button
      type="button"
      onClick={toggleOpen}
      className={`flex w-full items-center gap-2 text-left transition-colors cursor-pointer rounded-[var(--radius-control)] hover:bg-[var(--c-surface-2)]/30 ${
        light ? 'py-1' : 'py-1.5'
      }`}
    >
      <span className="min-w-0 truncate text-[length:var(--fs-sm)] font-medium text-[var(--c-text-secondary)]">
        {summary.label}
      </span>
      <ChevronDown
        size={14}
        strokeWidth={2}
        className={`shrink-0 text-[var(--c-text-secondary)]/50 transition-transform duration-200 ${
          effectiveOpen ? 'rotate-0' : '-rotate-90'
        }`}
      />
    </button>
  )

  // ── L1 感知层：流式期间渲染折叠头 + 步骤详情列表（StepDetailList，色块结构）──
  //
  // 色块结构在流式期间直接生效（defaultCollapsed=false），用户能实时看到每个
  // 步骤的工具类型色块、状态、参数与输出；执行结束后交 L2（同组件，默认折叠）。
  if (transition === 'active' || transition === 'completing') {
    if (rows.length === 0) return null
    const completed = transition === 'completing'
    return (
      <div className={`${light ? 'mb-3' : (className ?? '')} animate-in fade-in slide-in-from-top-1 duration-300`}>
        {/* 标题与展开内容共享左边界（与 L2 分支一致，不可加左内边距）。
            若此处加 pl-1：header 图标列相对 StepDetailList 的行图标列右移 4px，
            且 L1→L2 切换时标题会横向跳动——正是「图标没对齐」的成因。 */}
        <div>{renderHeader()}</div>
        {effectiveOpen && (
          <div className="mt-0.5 animate-in fade-in duration-200">
            <StepDetailList
              nodes={displayNodes}
              isStreaming={isExecuting}
            />
            {/* 底部常驻状态行：执行中显示秒表；完成时灰色勾 + 「执行完成」（设计图标准，无耗时） */}
            <div className="flex items-start gap-4 mt-4 animate-in fade-in duration-300">
              <div className="relative shrink-0 w-4">
                <div className="flex items-center justify-center h-[22px]">
                  {completed ? (
                    <CheckCircle2 size={16} strokeWidth={2} className="shrink-0 text-[var(--c-text-secondary)]/70" />
                  ) : isNarrating ? (
                    <Snowflake size={16} strokeWidth={2} className="shrink-0 text-[var(--c-accent)] animate-spin" />
                  ) : (
                    <Sparkles size={16} strokeWidth={2} className="shrink-0 text-[var(--c-accent)] animate-pulse" />
                  )}
                </div>
              </div>
              <div className="flex flex-1 items-center gap-2 min-w-0">
                {completed ? (
                  <span className="block min-w-0 flex-1 truncate text-[length:var(--fs-sm)] leading-[22px] font-normal text-[var(--c-text-secondary)]/85">
                    执行完成
                  </span>
                ) : (
                  <span className="block min-w-0 flex-1 truncate text-[length:var(--fs-sm)] leading-[22px] font-normal text-[var(--c-text-secondary)]/85">
                    {/* HITL 等待期：后端在 await 用户回答（事件流静默），必须显式
                        显示「等待你的回答」——否则沿用「任务正在执行」会让用户以
                        为执行没被提问阻断。runGate 是 HITL 单一真相源（取代 phase.state）。 */}
                    {runGate === 'awaiting_clarification'
                      ? '等待你的回答…'
                      : isNarrating
                        ? '正在组织回答…'
                        : '任务正在执行'}
                    {' '}
                    <span className="tabular-nums text-[var(--c-text-secondary)]/55">
                      {Math.floor(elapsedS)}s
                    </span>
                  </span>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    )
  }

  // ── L2 理解层：执行结束后的静态视图 ──
  // 判据与 L1 一致（rows 是实际渲染内容的唯一来源），避免两层空态判定打架。
  if (rows.length === 0) return null

  // L2 根节点**不带**入场动画：L2 是执行结束后的静态理解层，挂载时内容已在
  // 屏幕上（或来自历史回放），再播一次 fade-in 就是纯闪烁。实测 chat.done
  // 落库时流式消息被换成落库消息（新 id → 新 MessageBubble），若 L2 带
  // animate-in，执行过程区域会从 opacity 0 起播、约 180ms 淡回——即用户看到
  // 的「执行完闪一下」。入场动画只属于 L1（执行过程真正出现的那一刻）。
  return (
    <div className={light ? 'mb-3' : (className ?? '')}>
      {/* 标题与展开内容共享左边界（StepDetailList 无内部 pl-1） */}
      <div>
        {renderHeader()}
      </div>
      {effectiveOpen && (
        <div className={`${light ? 'mt-0.5' : 'mt-1'}`}>
          <StepDetailList nodes={displayNodes} />
          {!light && <DoneEntry />}
        </div>
      )}
    </div>
  )
}
