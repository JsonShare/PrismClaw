import type { HeartbeatPayload } from '../../types'

/**
 * P0.3/P0.4：将执行期心跳相位翻译成中文 spinner 文案（唯一实现）。
 * P2.4：phase 来源从 task.runtime 迁移至 ConvState.phase（camelCase HeartbeatPayload），
 * 由 set_phase timeline patch 驱动，单一真相源。
 * 秒数来自 usePhaseClock（前端秒表）：后端心跳帧只在相位切换发帧，
 * payload.elapsedS 是帧到达时的基线，不是持续时钟。
 * 消费方：useTaskPanelData（右侧面板副标题）、ChatArea（pinned 胶囊文案）。
 */
export function buildPhaseSubtitle(phase: HeartbeatPayload, elapsedS: number): string {
  const elapsed = Math.floor(elapsedS)
  switch (phase.state) {
    case 'generating': {
      const tool = phase.partialToolName
      const chars = phase.charsSoFar
      if (tool && chars != null) return `正在准备 ${tool} 参数…（${chars.toLocaleString()} 字符）`
      if (tool) return `正在准备 ${tool} 调用…`
      return '正在生成响应…'
    }
    case 'executing': {
      const tool = phase.partialToolName
      if (tool) return `仍在执行 ${tool}，已 ${elapsed}s`
      return `仍在执行工具调用，已 ${elapsed}s`
    }
    case 'compacting':
      return '正在压缩上下文…'
    case 'waiting_llm':
      return '正在等待模型响应…'
    default:
      return '处理中'
  }
}

// 注意：awaiting_clarification 不再出现在此 switch——它的文案由调用方
// 直接消费 ConvState.runGate === 'awaiting_clarification' 判断。
// phase 字段现在只承载 executing/generating/compacting/waiting_llm 进度语义。

// 这些 task.state 已有更具体的 UI 语义，心跳相位文案不抢占它们。
export const PHASE_OVERRIDE_PROTECTED_STATES = new Set([
  'awaiting_confirmation',
  'awaiting_risk_confirmation',
  'blocked',
  'completed',
  'cancelled',
  'reviewing',
])
