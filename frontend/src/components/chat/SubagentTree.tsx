import { useEffect, useRef, useState } from 'react'
import {
  Bot,
  CheckCircle2,
  ChevronUp,
  CircleSlash,
  LoaderCircle,
  Sparkles,
  XCircle,
} from 'lucide-react'
import type { SubagentPanelItem } from '../../types'

const RUNNING_STATUSES = new Set(['thinking', 'tool_calling', 'tool_running'])

const STATUS_LABEL: Record<SubagentPanelItem['partial_status'], string> = {
  thinking: '思考中',
  tool_calling: '准备工具',
  tool_running: '运行中',
  completed: '已完成',
  failed: '失败',
  aborted: '已中止',
}

function formatSeconds(value: number): string {
  const total = Math.max(0, Math.round(value))
  if (total < 60) return `${total}s`
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  return seconds > 0 ? `${minutes}m${seconds}s` : `${minutes}m`
}

function StatusIcon({ status }: { status: SubagentPanelItem['partial_status'] }) {
  if (status === 'completed') return <CheckCircle2 size={13} className="text-[var(--c-success)]" />
  if (status === 'failed') return <XCircle size={13} className="text-[var(--c-error)]" />
  if (status === 'aborted') return <CircleSlash size={13} className="text-[var(--c-text-secondary)]" />
  return <LoaderCircle size={13} className="text-[var(--c-accent)] animate-spin" />
}

/** 子代理卡片的一句话描述：优先用 sub_agent 调用参数里的 task。 */
function describe(item: SubagentPanelItem): string {
  if (item.task_hint) return item.task_hint
  if (item.from_subagent) return `由 ${item.from_subagent} 移交`
  if (item.current_tool) return `正在执行 ${item.current_tool}`
  return STATUS_LABEL[item.partial_status]
}

/**
 * 右栏「任务进程」里的只读 Subagent 执行视图：
 * 主 Agent 节点 + 各子代理卡片，展示并行数量、完成进度、耗时与当前状态。
 */
export function SubagentTree({ items }: { items: SubagentPanelItem[] }) {
  const [open, setOpen] = useState(true)
  const [now, setNow] = useState(() => Date.now())
  // 后端只在工具边界推送 duration_so_far，单次长工具期间会「停表」。
  // 用首次见到该子代理的时刻反推起点，本地每秒推进，避免计时看起来卡死。
  const anchorsRef = useRef(new Map<string, number>())
  const runningCount = items.filter((item) => RUNNING_STATUSES.has(item.partial_status)).length

  useEffect(() => {
    if (runningCount === 0) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [runningCount])

  useEffect(() => {
    const anchors = anchorsRef.current
    const stamp = Date.now()
    const liveIds = new Set<string>()
    for (const item of items) {
      liveIds.add(item.sub_session_id)
      const estimate = stamp - item.duration_so_far * 1000
      const prev = anchors.get(item.sub_session_id)
      anchors.set(item.sub_session_id, prev === undefined ? estimate : Math.min(prev, estimate))
    }
    // 本轮已结束的子代理不再保留锚点，避免跨轮次累积
    for (const id of Array.from(anchors.keys())) {
      if (!liveIds.has(id)) anchors.delete(id)
    }
  }, [items])

  if (items.length === 0) return null

  const elapsedOf = (item: SubagentPanelItem): number => {
    if (!RUNNING_STATUSES.has(item.partial_status)) return item.duration_so_far
    const anchor = anchorsRef.current.get(item.sub_session_id)
    if (anchor === undefined) return item.duration_so_far
    return Math.max(item.duration_so_far, (now - anchor) / 1000)
  }

  const completedCount = items.filter((item) => item.partial_status === 'completed').length
  const totalElapsed = items.reduce((max, item) => Math.max(max, elapsedOf(item)), 0)

  return (
    <div className="rounded-[var(--radius-card)] border border-[var(--c-border)] bg-[var(--c-surface)] overflow-hidden">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="w-full px-3 py-2.5 flex items-center gap-2.5 text-left cursor-pointer"
      >
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[var(--c-surface-2)]">
          <Bot size={13} className="text-[var(--c-accent)]" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[length:var(--fs-sm)] font-semibold text-[var(--c-text)]">
            Subagent {runningCount > 0 ? '正在工作' : '已结束'}
          </span>
          <span className="mt-0.5 block text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]/70 truncate">
            {items.length} 个 Subagent · 已完成 {completedCount}/{items.length} · {formatSeconds(totalElapsed)}
          </span>
        </span>
        <ChevronUp
          size={16}
          className={`shrink-0 text-[var(--c-text-secondary)] transition-transform ${open ? '' : '-rotate-180'}`}
        />
      </button>

      {open && (
        <div className="px-3 pb-3">
          <div className="rounded-[10px] bg-[var(--c-surface-2)] px-2.5 py-2 flex items-center gap-2">
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[var(--c-surface)]">
              <Sparkles size={12} className="text-[var(--c-accent)]" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[length:var(--fs-sm)] font-semibold text-[var(--c-text)]">主 Agent</span>
              <span className="mt-0.5 block text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]/70 truncate">
                {runningCount > 0
                  ? `正在协调 ${items.length} 个委派任务`
                  : `已协调 ${items.length} 个委派任务`}
              </span>
            </span>
          </div>

          <div className="ml-[5px] h-2 w-px bg-[var(--c-border)]" />

          {items.map((item, index) => {
            const isLast = index === items.length - 1
            return (
              <div key={item.sub_session_id} className="relative pl-4">
                <span
                  className={`absolute left-[5px] top-0 w-px bg-[var(--c-border)] ${
                    isLast ? 'h-[22px]' : 'bottom-0'
                  }`}
                />
                <span className="absolute left-[5px] top-[22px] h-px w-[11px] bg-[var(--c-border)]" />
                <div className="py-1">
                  <div className="rounded-[10px] border border-[var(--c-border)] bg-[var(--c-surface)] px-2.5 py-2">
                    <div className="flex items-center gap-2">
                      <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-[6px] bg-[var(--c-surface-2)]">
                        <StatusIcon status={item.partial_status} />
                      </span>
                      <span className="min-w-0 flex-1 truncate text-[length:var(--fs-sm)] font-semibold text-[var(--c-text)]">
                        {item.subagent_name}
                      </span>
                      <span className="shrink-0 text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]/60">
                        {item.model_hint ?? '默认模型'}
                      </span>
                    </div>
                    <div className="mt-1 pl-7 space-y-1">
                      <div className="text-[length:var(--fs-xs)] leading-relaxed text-[var(--c-text-secondary)]/80 line-clamp-2">
                        {describe(item)}
                      </div>
                      <div className="flex items-center gap-1.5 text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]/60">
                        <span>{STATUS_LABEL[item.partial_status]}</span>
                        <span>·</span>
                        <span>{formatSeconds(elapsedOf(item))}</span>
                        <span>·</span>
                        <span>
                          第 {Math.max(1, item.turn)}/{item.max_turns} 轮
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
