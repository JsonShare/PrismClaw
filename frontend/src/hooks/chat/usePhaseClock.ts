import { useEffect, useState } from 'react'
import type { HeartbeatPayload } from '../../types/trajectory'

/**
 * 执行秒表（「任务正在执行 Xs」）。
 *
 * 计时纪元 = 本轮开始时刻（runStartedAt，store 在 initStreaming 时记下），
 * 与后端 elapsed_s「从 run_started（任务发起时刻）起算」的口径一致：
 * 反映用户感知的全程时间，且**不依赖心跳帧到达**——心跳只在相位切换 /
 * 长时间静默时才发（实测首个心跳可晚至十几秒甚至更久），若以心跳到达
 * 时刻为纪元，这段窗口内秒表恒为 0。
 *
 * phase/phaseAt 仅作为权威校正：后端 elapsedS 是帧到达时的基线值，加上
 * 到达后的本地漂移，与本地纪元取 max（两者都单调，取 max 不产生回跳）。
 *
 * 返回值：当前已执行秒数（未进位小数，展示方自行 Math.floor）。
 */
export function usePhaseClock(
  phase: HeartbeatPayload | null,
  phaseAt: number | null | undefined,
  runStartedAt?: number | null,
  active?: boolean,
): number {
  const [, setTick] = useState(0)
  const running = active ?? phase !== null
  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => setTick((n) => n + 1), 1000)
    return () => window.clearInterval(timer)
  }, [running])
  const local = runStartedAt ? Math.max(0, (Date.now() - runStartedAt) / 1000) : 0
  const heartbeat = phase
    ? phase.elapsedS + (phaseAt ? Math.max(0, (Date.now() - phaseAt) / 1000) : 0)
    : 0
  return Math.max(local, heartbeat)
}
