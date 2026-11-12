import { useCallback, useMemo } from 'react'
import { useChatStore, useSessionStore } from '../../stores'
import { usePhaseClock } from './usePhaseClock'
import { normalizeWorkspacePath } from '../../lib/chat/paths'
import { buildPhaseSubtitle, PHASE_OVERRIDE_PROTECTED_STATES } from '../../lib/chat/phase'
import type { SubagentPanelItem, SubagentProgressItem, TimelineNode, ToolCallInfo } from '../../types'

/**
 * 咨询页右侧面板派生数据（单真相源：全部从 store 直订计算）。
 *
 * 此前这些 useMemo/useCallback 散落在 ChatPage 主体（~270 行），把页面组件
 * 变成「面板数据工厂 + 页面编排」双职责。现收口为一个 hook：ChatPage 只取
 * 返回值传给 ChatRightPanel，不再感知派生细节。
 */

// zustand selector fallback 稳定引用
const EMPTY_MESSAGES: import('../../types').Message[] = []
const EMPTY_TOOL_CALLS: ToolCallInfo[] = []
const EMPTY_TIMELINE: TimelineNode[] = []
const EMPTY_SUBAGENT_TRACE: SubagentProgressItem[] = []

function fileExt(path: string): string {
  const name = (path || '').split('/').pop() || ''
  const ext = (name.split('.').pop() || '').trim().toLowerCase()
  return ext
}

function shouldHideTaskPanelFile(path: string): boolean {
  const normalized = (path || '').replace(/\\+/g, '/').trim()
  if (!normalized) return true
  const lowerPath = `/${normalized.replace(/^\/+/g, '').toLowerCase()}/`
  if (lowerPath.includes('/.git/') || lowerPath.includes('/node_modules/')) return true
  if (lowerPath.includes('/skills/') || lowerPath.includes('/scripts/') || lowerPath.includes('/.trae/')) return true
  const ext = fileExt(normalized)
  if (ext && new Set(['md', 'txt', 'csv', 'tsv', 'html', 'htm', 'json']).has(ext)) {
    const deliverableDirs = ['outputs', 'output', 'out', 'artifacts', 'exports', 'reports', 'results', 'generated']
    if (!deliverableDirs.some((d) => lowerPath.includes(`/${d}/`))) return true
  }
  return false
}

function filterPanelTaskFiles(paths: string[], opts: { allowImagesIfNoDocs: boolean }): string[] {
  const nonImageAllow = new Set(['docx', 'doc', 'pdf', 'xlsx', 'xls', 'pptx', 'ppt', 'zip', 'md', 'txt', 'csv', 'tsv', 'html', 'htm', 'json'])
  const imageAllow = new Set(['png', 'jpg', 'jpeg', 'webp', 'gif'])
  const visible = paths.filter((p) => !shouldHideTaskPanelFile(p))
  const nonImage = visible.filter((p) => nonImageAllow.has(fileExt(p)))
  if (nonImage.length > 0) return nonImage
  if (!opts.allowImagesIfNoDocs) return []
  return visible.filter((p) => {
    const ext = fileExt(p)
    return nonImageAllow.has(ext) || imageAllow.has(ext)
  })
}

export interface TaskRuntimeSummary {
  title: string
  subtitle: string
  icon: 'streaming' | 'finalizing'
}

export interface TaskPanelData {
  panelTaskOutputFiles: string[]
  allToolCallsForPanel: Array<{
    id: string
    name: string
    status: 'running' | 'done' | 'error'
    arguments: Record<string, unknown>
  }>
  skillsForPanel: string[]
  toolNamesForPanel: string[]
  subagentsForPanel: SubagentPanelItem[]
  runningToolCallsForPanel: ToolCallInfo[]
  formatToolProgressText: (tc: ToolCallInfo) => string
  taskRuntimeSummary: TaskRuntimeSummary
}

export function useTaskPanelData(): TaskPanelData {
  const activeConvId = useSessionStore((s) => s.activeConvId)
  const currentMessages = useChatStore((s) =>
    activeConvId ? s.convStates[activeConvId]?.messages ?? EMPTY_MESSAGES : EMPTY_MESSAGES,
  )
  const outboxLength = useChatStore((s) =>
    activeConvId ? s.convStates[activeConvId]?.outbox.length ?? 0 : 0,
  )
  const isStreaming = useChatStore((s) =>
    activeConvId ? s.convStates[activeConvId]?.isStreaming ?? false : false,
  )
  const liveTaskRuntime = useChatStore((s) =>
    activeConvId ? s.convStates[activeConvId]?.activeTaskRuntime ?? null : null,
  )
  const liveToolCalls = useMemo(
    () => liveTaskRuntime?.tool_calls ?? EMPTY_TOOL_CALLS,
    [liveTaskRuntime],
  )
  const liveSubagentTrace = useMemo(
    () => liveTaskRuntime?.subagent_trace ?? EMPTY_SUBAGENT_TRACE,
    [liveTaskRuntime],
  )
  const liveTimeline = useChatStore((s) =>
    activeConvId ? s.convStates[activeConvId]?.timeline ?? EMPTY_TIMELINE : EMPTY_TIMELINE,
  )
  const livePhase = useChatStore((s) =>
    activeConvId ? s.convStates[activeConvId]?.phase ?? null : null,
  )
  const livePhaseAt = useChatStore((s) =>
    activeConvId ? s.convStates[activeConvId]?.phaseAt ?? null : null,
  )
  const liveRunStartedAt = useChatStore((s) =>
    activeConvId ? s.convStates[activeConvId]?.runStartedAt ?? null : null,
  )
  // runGate: HITL 单一真相源。phase 不再承载 awaiting_clarification 语义。
  const liveRunGate = useChatStore((s) =>
    activeConvId ? s.convStates[activeConvId]?.runGate ?? null : null,
  )
  const liveElapsedS = usePhaseClock(livePhase, livePhaseAt, liveRunStartedAt, isStreaming)

  const explicitOutputFiles = useMemo(() => {
    const collected: string[] = []
    for (const message of currentMessages) {
      if (message.role !== 'assistant') continue
      const files = Array.isArray(message.metadata?.outputFiles) ? message.metadata.outputFiles : []
      for (const file of files ?? []) {
        if (typeof file === 'string' && file.trim()) collected.push(file)
      }
    }
    const out: string[] = []
    const seen = new Set<string>()
    for (const raw of collected) {
      const normalized = normalizeWorkspacePath(raw)
      if (!normalized) continue
      if (seen.has(normalized)) continue
      seen.add(normalized)
      out.push(normalized)
    }
    return out
  }, [currentMessages])

  // 交付物列表只来自 explicitOutputFiles（LLM 通过 present_files 工具声明的最终交付物，
  // 经后端 event:outputs 推送 + message.metadata.outputFiles 持久化）。
  // 不再从 timeline kind=write 提取——那包含中间文件，不是交付物信号，
  // 且任务完成后 liveTimeline 被 finalizeStreaming 清空会导致交付物间歇性消失。
  const panelTaskOutputFiles = useMemo(() => {
    const out: string[] = []
    const seen = new Set<string>()
    for (const raw of explicitOutputFiles) {
      const normalized = normalizeWorkspacePath(raw)
      if (!normalized) continue
      if (seen.has(normalized)) continue
      seen.add(normalized)
      out.push(normalized)
    }
    return filterPanelTaskFiles(out, { allowImagesIfNoDocs: true })
  }, [explicitOutputFiles])

  const allToolCallsForPanel = useMemo(() => {
    const out: Array<{
      id: string
      name: string
      status: 'running' | 'done' | 'error'
      arguments: Record<string, unknown>
    }> = []
    for (const m of currentMessages) {
      for (const tc of m.toolCalls ?? []) {
        out.push(tc)
      }
    }
    if (activeConvId) {
      for (const tc of liveToolCalls) out.push(tc)
    }
    const byId = new Map<string, (typeof out)[number]>()
    for (const tc of out) {
      if (!tc?.id) continue
      byId.set(tc.id, tc)
    }
    return Array.from(byId.values())
  }, [activeConvId, currentMessages, liveToolCalls])

  const extractSkillNameFromToolCall = useCallback(
    (tc: { name: string; arguments: Record<string, unknown> }): string | null => {
      const toolName = (tc.name || '').trim()
      const args = tc.arguments || {}

      // skill_invoke / apply_skill / activate_skill → skill_name field
      if (toolName === 'skill_invoke' || toolName === 'apply_skill' || toolName === 'activate_skill') {
        const n = args.skill_name ?? args.name
        return typeof n === 'string' && n.trim() ? n.trim() : null
      }

      // sub_agent → agent_name field
      if (toolName === 'sub_agent') {
        const n = args.agent_name ?? args.subagent_type ?? args.name
        return typeof n === 'string' && n.trim() ? n.trim() : null
      }

      return null
    },
    [],
  )

  const skillsForPanel = useMemo(() => {
    const seen = new Set<string>()
    const out: string[] = []
    for (const tc of allToolCallsForPanel) {
      const skill = extractSkillNameFromToolCall(tc)
      if (!skill) continue
      if (seen.has(skill)) continue
      seen.add(skill)
      out.push(skill)
    }
    for (const item of liveSubagentTrace) {
      const activeSkillName =
        typeof item.active_skill_name === 'string' && item.active_skill_name.trim()
          ? item.active_skill_name.trim()
          : null
      if (activeSkillName && !seen.has(activeSkillName)) {
        seen.add(activeSkillName)
        out.push(activeSkillName)
      }
      const invokedSkills = Array.isArray(item.invoked_skill_names) ? item.invoked_skill_names : []
      for (const rawName of invokedSkills) {
        const skillName = typeof rawName === 'string' ? rawName.trim() : ''
        if (!skillName || seen.has(skillName)) continue
        seen.add(skillName)
        out.push(skillName)
      }
    }
    return out.slice(-6)
  }, [allToolCallsForPanel, extractSkillNameFromToolCall, liveSubagentTrace])

  const toolNamesForPanel = useMemo(() => {
    const seen = new Set<string>()
    const out: string[] = []
    for (let index = allToolCallsForPanel.length - 1; index >= 0; index -= 1) {
      const tc = allToolCallsForPanel[index]
      const toolName = (tc?.name || '').trim()
      const key = toolName.toLowerCase()
      if (!toolName) continue
      if (key === 'skill_invoke' || key === 'apply_skill' || key === 'activate_skill') continue
      const label = toolName
      if (seen.has(label)) continue
      seen.add(label)
      out.push(label)
      if (out.length >= 10) break
    }
    return out.reverse()
  }, [allToolCallsForPanel])

  // sub_agent 的调用参数里含 agents[].task / agents[].model，但进度项只带
  // sub_session_id，没有 call 关联。这里按 agent_name + 出现顺序做一次
  // best-effort 对齐，纯展示用途，不参与运行态判断。
  // 参数来源必须是**本轮** liveToolCalls：allToolCallsForPanel 混入了历史消息，
  // 同名 agent 会优先命中上一轮的任务描述。
  // 同时过滤 $synthetic 项（链路上限超限的合成失败态），它不是真实子代理。
  const subagentsForPanel = useMemo<SubagentPanelItem[]>(() => {
    const hints: Array<{ agentName: string; task: string | null; model: string | null }> = []
    for (const tc of liveToolCalls) {
      if (tc.name !== 'sub_agent') continue
      const agents = (tc.arguments as Record<string, unknown> | undefined)?.agents
      if (!Array.isArray(agents)) continue
      for (const raw of agents) {
        if (!raw || typeof raw !== 'object') continue
        const entry = raw as Record<string, unknown>
        const agentName = typeof entry.agent_name === 'string' ? entry.agent_name.trim() : ''
        if (!agentName) continue
        const task = typeof entry.task === 'string' && entry.task.trim() ? entry.task.trim() : null
        const model = typeof entry.model === 'string' && entry.model.trim() ? entry.model.trim() : null
        hints.push({ agentName, task, model })
      }
    }
    const used = new Set<number>()
    return liveSubagentTrace
      .filter((item) => !item.sub_session_id.startsWith('$synthetic'))
      .map((item) => {
        let matched = -1
        for (let i = 0; i < hints.length; i += 1) {
          if (used.has(i) || hints[i].agentName !== item.subagent_name) continue
          matched = i
          break
        }
        if (matched >= 0) used.add(matched)
        return {
          ...item,
          task_hint: matched >= 0 ? hints[matched].task : null,
          model_hint: matched >= 0 ? hints[matched].model : null,
        }
      })
  }, [liveToolCalls, liveSubagentTrace])

  const runningToolCallsForPanel = useMemo(() => {
    return liveToolCalls
      .filter((tc): tc is ToolCallInfo => tc.status === 'running')
      .sort((a, b) => (a.startedAt ?? a.lastProgressAt ?? 0) - (b.startedAt ?? b.lastProgressAt ?? 0))
  }, [liveToolCalls])

  const formatToolProgressText = useCallback((tc: ToolCallInfo) => {
    const elapsed = typeof tc.elapsedSeconds === 'number' && tc.elapsedSeconds > 0 ? `（${Math.max(1, Math.round(tc.elapsedSeconds))}s）` : ''
    if (tc.progressPhase === 'waiting_approval') return `等待确认${elapsed || '…'}`
    if (tc.progressPhase === 'finalizing') return `整理结果中${elapsed || '…'}`
    return `执行中${elapsed || '…'}`
  }, [])

  const taskRuntimeSummary = useMemo<TaskRuntimeSummary>(() => {
    const hasAssistantReply = currentMessages.some((message) => message.role === 'assistant')
    // 重构后 active_revision / current_step_id 已删除：用实时时间线的"当前步骤"还原执行中副标题
    const currentStepTitle = (() => {
      const step = liveTimeline.find(
        (n) => n.type === 'step' && (n as { status?: string }).status === 'current',
      )
      return step ? ((step as { title?: string }).title ?? '') : ''
    })()
    const runtime = liveTaskRuntime?.task
    if (runtime) {
      // HITL 暂停期优先：runGate 是单一真相源，比 phase subtitle 优先级更高
      // （phase 已不再承载 awaiting_clarification 语义，避免双真相源文案分裂）
      if (liveRunGate === 'awaiting_clarification') {
        return {
          title: '等待你的回答',
          subtitle: '请在左下方提问卡片作答后继续',
          icon: 'finalizing' as const,
        }
      }
      // P0.4：执行期心跳相位优先驱动 spinner 文案（消假死）。只要 phase 在场且非受保护
      // 状态即生效——覆盖纯文本 / 非分步任务（兼容性门控），不抢占确认 / 终态 / 校验态。
      // P2.4：phase 来源改为 ConvState.phase（set_phase timeline patch 驱动）
      if (livePhase && !PHASE_OVERRIDE_PROTECTED_STATES.has(runtime.state)) {
        return {
          title: '处理中',
          subtitle: buildPhaseSubtitle(livePhase, liveElapsedS),
          icon: 'streaming' as const,
        }
      }
      if (runtime.state === 'awaiting_confirmation') {
        return {
          title: '需要确认方向',
          subtitle: runtime.pending_confirmation_reason || currentStepTitle || '等待确认后继续',
          icon: 'finalizing' as const,
        }
      }
      if (runtime.state === 'awaiting_risk_confirmation') {
        return {
          title: '等待风险确认',
          subtitle: runtime.pending_confirmation_reason || currentStepTitle || '高风险任务需先确认',
          icon: 'finalizing' as const,
        }
      }
      if (runtime.state === 'resuming') {
        const recoveryProbe = liveTaskRuntime!.recovery_probe
        return {
          title: '恢复现场',
          subtitle: recoveryProbe?.summary || '正在执行恢复探针…',
          icon: 'streaming' as const,
        }
      }
      if (runtime.state === 'reviewing') {
        return {
          title: '校验结果',
          subtitle: liveTaskRuntime!.latest_review?.reason || currentStepTitle || '正在核对事实证据…',
          icon: 'finalizing' as const,
        }
      }
      if (runtime.state === 'executing') {
        return {
          title: '处理中',
          subtitle: currentStepTitle || '正在执行当前步骤…',
          icon: 'streaming' as const,
        }
      }
      if (runtime.state === 'intaking' || runtime.state === 'clarifying') {
        const preflightProbe = liveTaskRuntime!.preflight_probe
        return {
          title: '整理处理思路',
          subtitle:
            preflightProbe?.summary ||
            '正在建立任务方案…',
          icon: 'streaming' as const,
        }
      }
      if (runtime.state === 'blocked') {
        return {
          title: '已阻塞',
          subtitle: runtime.pending_confirmation_reason || '当前任务已阻塞',
          icon: 'finalizing' as const,
        }
      }
      if (runtime.state === 'completed') {
        return {
          title: '任务完成',
          subtitle: '当前任务已完成',
          icon: 'finalizing' as const,
        }
      }
      if (runtime.state === 'cancelled') {
        return {
          title: '任务已取消',
          subtitle: '当前任务已取消',
          icon: 'finalizing' as const,
        }
      }
    }
    if (isStreaming) {
      return {
        title: '处理中',
        subtitle: '正在处理当前任务…',
        icon: 'streaming' as const,
      }
    }
    if (outboxLength > 0) {
      return {
        title: '等待执行',
        subtitle: '任务已提交，等待开始处理…',
        icon: 'streaming' as const,
      }
    }
    if (hasAssistantReply) {
      return {
        title: '当前空闲',
        subtitle: '上一轮任务已完成，可继续输入新任务',
        icon: 'finalizing' as const,
      }
    }
    return {
      title: '等待新任务',
      subtitle: '输入任务后会在这里显示执行状态',
      icon: 'streaming' as const,
    }
  }, [currentMessages, outboxLength, isStreaming, liveTaskRuntime, liveTimeline, livePhase, liveElapsedS])

  return {
    panelTaskOutputFiles,
    allToolCallsForPanel,
    skillsForPanel,
    toolNamesForPanel,
    subagentsForPanel,
    runningToolCallsForPanel,
    formatToolProgressText,
    taskRuntimeSummary,
  }
}
