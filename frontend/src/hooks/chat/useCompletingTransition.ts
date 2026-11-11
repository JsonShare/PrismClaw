import { useEffect, useRef, useState } from 'react'

/**
 * 执行态 → 完成态 → 静态态 三态机。
 *
 * 解决 L1（流式动作行）→ L2/静态视图 直接硬切的无过渡问题：
 * - active（isExecuting/isStreaming 仍为 true）：渲染 L1
 * - completing（刚变为 false）：继续渲染 L1 但传 completed=true，
 *   让 L1 显示「执行完成 · 共 Xs」并以 ease-out 渐隐，给 L2/静态视图 200-300ms
 *   的入场窗口，形成「完成 → 细节」的连续叙事
 * - done：完全卸载 L1，渲染 L2/静态
 *
 * 初始挂载时若 active=false 直接进 done（不播空完成动画）。
 * duration 默认 400ms，与 .animate-in (200ms ease-out) + L1 渐隐 200ms 错位叠加。
 */
export function useCompletingTransition(
  active: boolean | undefined,
  duration = 400,
): 'active' | 'completing' | 'done' {
  const resolved = active ?? false
  const [phase, setPhase] = useState<'active' | 'completing' | 'done'>(resolved ? 'active' : 'done')
  const prev = useRef(resolved)

  useEffect(() => {
    if (resolved) {
      setPhase('active')
    } else if (prev.current) {
      setPhase('completing')
      const t = window.setTimeout(() => setPhase('done'), duration)
      return () => window.clearTimeout(t)
    }
    prev.current = resolved
  }, [resolved, duration])

  return phase
}
