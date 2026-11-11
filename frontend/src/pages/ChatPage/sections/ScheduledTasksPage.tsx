import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, ChevronDown, Clock, MessageSquare, MoreHorizontal, Play, RefreshCw, Trash2, X } from 'lucide-react'
import { apiUrl } from '../../../lib/system-adapter'
import { useSessionStore, useUiStore } from '../../../stores'
import { PrimaryButton } from '../../../components/ui/PrimaryButton'
import { HairlineDivider } from '../../../components/ui/HairlineDivider'
import { useToast } from '../../../lib/toast'

type ScheduleType = 'minute' | 'hour' | 'day' | 'week' | 'month'
type ScheduleWeekday = 'mon' | 'tue' | 'wed' | 'thu' | 'fri' | 'sat' | 'sun'
type ScheduleSpec =
  | { type: 'minute'; everyMinutes: number }
  | { type: 'hour'; minute: string }
  | { type: 'day'; hour: string; minute: string }
  | { type: 'week'; weekday: ScheduleWeekday; hour: string; minute: string }
  | { type: 'month'; day: string; hour: string; minute: string }

type ScheduledJob = {
  id: string
  name: string
  instruction: string
  schedule: ScheduleSpec
  enabled: boolean
  lastStatus: string
  workspaceId: string
  createdAt: number
}

type RunRecord = {
  id: string
  status: string
  fired_at: number
  duration_s: number | null
  error: string | null
  session_id: string | null
}

type HistoryRecord = RunRecord & { jobId: string; jobName: string }

type LiveRun = {
  runId: string
  status: 'pending' | 'running'
}

const WEEKDAY_LABEL: Record<ScheduleWeekday, string> = {
  mon: '周一',
  tue: '周二',
  wed: '周三',
  thu: '周四',
  fri: '周五',
  sat: '周六',
  sun: '周日',
}

// 「执行历史」分页：每轮每个任务拉取的运行条数（后端 /runs 支持 limit/offset，上限 100）
const HISTORY_PAGE_SIZE = 20

interface Props {
  token: string | null
  onSelectSession: (convId: string) => Promise<void>
  onLogout: () => void
}

export default function ScheduledTasksPage({ token, onSelectSession, onLogout }: Props) {
  const activeWorkspaceId = useUiStore((s) => s.activeWorkspaceId)
  const workspaces = useUiStore((s) => s.workspaces)
  const setActiveSection = useUiStore((s) => s.setActiveSection)
  const setPendingComposerPrefill = useUiStore((s) => s.setPendingComposerPrefill)
  const prependConversation = useSessionStore((s) => s.prependConversation)
  const { toast } = useToast()

  const [scheduledJobs, setScheduledJobs] = useState<ScheduledJob[]>([])
  // 执行状态由 WS 事件驱动；终态与运行历史统一由 /runs 恢复，不用定时器伪造。
  const [liveRuns, setLiveRuns] = useState<Record<string, LiveRun>>({})
  const [runsByJob, setRunsByJob] = useState<Record<string, RunRecord[]>>({})
  // 页面级视图切换：任务列表 / 执行历史（执行历史从卡片内嵌改为独立 tab 单独展示）
  const [view, setView] = useState<'tasks' | 'history'>('tasks')
  // 事件回调只需要读任务名。放进依赖数组会让每次 fetchTasks() 都注销/重注册
  // 监听，连续帧（熔断先发 disabled 再发 completed）存在丢失窗口，故用 ref。
  const scheduledJobsRef = useRef(scheduledJobs)
  useEffect(() => { scheduledJobsRef.current = scheduledJobs }, [scheduledJobs])

  // 执行历史分页：任务列表页只需每任务最新 1 条（供状态徽标）；「执行历史」tab 首次进入才按页
  // 加载，「加载更多」按 HISTORY_PAGE_SIZE 递增每任务的拉取深度。ref 供 WS 回调读取当前深度，
  // 避免监听器随 state 重注册（连续帧存在丢失窗口）。
  const [historyLimit, setHistoryLimit] = useState(HISTORY_PAGE_SIZE)
  const [historyLoading, setHistoryLoading] = useState(false)
  const historyLoadedRef = useRef(false)
  const historyLimitRef = useRef(HISTORY_PAGE_SIZE)

  // 全局按用户拉取（不过滤 workspace），与侧边栏 fetchSchedulerSidebar 保持一致。
  const fetchTasks = useCallback(async (): Promise<string[]> => {
    if (!token) return []
    try {
      const res = await fetch(apiUrl('/api/scheduled-tasks'), {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (res.status === 401) { onLogout(); return [] }
      if (!res.ok) return []
      const data: any[] = await res.json()
      const jobs: ScheduledJob[] = data.map((t) => ({
        id: t.id,
        name: t.name,
        instruction: t.instruction,
        schedule: t.schedule,
        enabled: t.enabled,
        lastStatus: t.last_status ?? 'idle',
        workspaceId: t.workspace_id ?? 'default',
        createdAt: typeof t.created_at === 'number' ? t.created_at : 0,
      }))
      setScheduledJobs(jobs)
      return jobs.map((j) => j.id)
    } catch {
      return []
    }
  }, [token, onLogout])

  // 启停单一真源：乐观翻转 + PATCH + 失败回滚 + 成功重拉。
  const setEnabled = useCallback(
    async (job: ScheduledJob, next: boolean) => {
      if (!token) return
      setScheduledJobs((prev) => prev.map((t) => (t.id === job.id ? { ...t, enabled: next } : t)))
      try {
        const res = await fetch(apiUrl(`/api/scheduled-tasks/${encodeURIComponent(job.id)}`), {
          method: 'PATCH',
          headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: next }),
        })
        if (res.status === 401) { onLogout(); return }
        if (!res.ok) {
          setScheduledJobs((prev) => prev.map((t) => (t.id === job.id ? { ...t, enabled: job.enabled } : t)))
          return
        }
        void fetchTasks()
      } catch {
        setScheduledJobs((prev) => prev.map((t) => (t.id === job.id ? { ...t, enabled: job.enabled } : t)))
      }
    },
    [token, onLogout, fetchTasks],
  )

  const fetchRuns = useCallback(
    async (jobId: string, limit: number) => {
      if (!token) return
      try {
        const res = await fetch(
          apiUrl(`/api/scheduled-tasks/${encodeURIComponent(jobId)}/runs?limit=${limit}`),
          { headers: { Authorization: `Bearer ${token}` } },
        )
        if (res.status === 401) { onLogout(); return }
        if (!res.ok) return
        const rows: RunRecord[] = await res.json()
        setRunsByJob((prev) => ({ ...prev, [jobId]: rows }))
      } catch {}
    },
    [token, onLogout],
  )

  // 按页加载执行历史：后端按 fired_at 倒序返回，增大 limit 即向更早翻页。
  const loadHistoryPage = useCallback(
    async (limit: number) => {
      if (!token) return
      setHistoryLoading(true)
      historyLimitRef.current = limit
      setHistoryLimit(limit)
      try {
        const ids = scheduledJobsRef.current.map((j) => j.id)
        await Promise.all(ids.map((id) => fetchRuns(id, limit)))
      } finally {
        setHistoryLoading(false)
      }
    },
    [token, fetchRuns],
  )

  // 进入页面时只恢复每任务最新 1 条（状态徽标用）；完整历史留给「执行历史」tab 按页懒加载。
  useEffect(() => {
    void (async () => {
      const ids = await fetchTasks()
      await Promise.all(ids.map((id) => fetchRuns(id, 1)))
    })()
  }, [fetchTasks, fetchRuns])

  // 首次切到「执行历史」tab 才按页拉取完整历史（任务列表页不做全量请求）。
  useEffect(() => {
    if (view !== 'history' || historyLoadedRef.current || scheduledJobs.length === 0) return
    historyLoadedRef.current = true
    void loadHistoryPage(HISTORY_PAGE_SIZE)
  }, [view, scheduledJobs, loadHistoryPage])

  // 真实执行状态：started / completed 驱动，终态与历史交给 /runs。
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as { type?: string; data?: any } | undefined
      const type = detail?.type
      const data = detail?.data
      const taskId = typeof data?.task_id === 'string' ? data.task_id : null
      const name = scheduledJobsRef.current.find((j) => j.id === taskId)?.name ?? '自动化任务'

      if (type === 'scheduled_task.started' && taskId) {
        setLiveRuns((prev) => ({
          ...prev,
          [taskId]: { runId: String(data?.run_id ?? ''), status: 'running' },
        }))
        void fetchRuns(taskId, historyLoadedRef.current ? historyLimitRef.current : 1)
      } else if (type === 'scheduled_task.completed' && taskId) {
        const finishedRunId = typeof data?.run_id === 'string' ? data.run_id : ''
        setLiveRuns((prev) => {
          const current = prev[taskId]
          if (!current) return prev
          // 同一任务可并发多次执行；旧 run 的完成事件不能清掉新 run 的进行态。
          if (current.runId && finishedRunId && current.runId !== finishedRunId) return prev
          const next = { ...prev }
          delete next[taskId]
          return next
        })
        void fetchRuns(taskId, historyLoadedRef.current ? historyLimitRef.current : 1)
        if (data?.status !== 'success') {
          toast(`「${name}」执行失败${data?.error ? `：${String(data.error)}` : ''}`, 'error')
        }
      } else if (type === 'scheduled_task.disabled') {
        toast(
          `「${name}」因连续执行失败已被自动停用${data?.reason ? `（${data.reason}）` : ''}，可在自动化页重新启用。`,
          'info',
        )
      }
      void fetchTasks()
    }
    window.addEventListener('prismclaw:scheduler-event', handler)
    return () => window.removeEventListener('prismclaw:scheduler-event', handler)
  }, [fetchTasks, fetchRuns, toast])

  const [scheduleModalOpen, setScheduleModalOpen] = useState(false)
  const [scheduleEditingJobId, setScheduleEditingJobId] = useState<string | null>(null)
  const [scheduleDraftName, setScheduleDraftName] = useState('')
  const [scheduleDraftInstruction, setScheduleDraftInstruction] = useState('')
  const [scheduleDraftType, setScheduleDraftType] = useState<ScheduleType>('day')
  const [scheduleDraftEveryMinutes, setScheduleDraftEveryMinutes] = useState('30')
  const [scheduleDraftHourMinute, setScheduleDraftHourMinute] = useState('00')
  const [scheduleDraftDayHour, setScheduleDraftDayHour] = useState('09')
  const [scheduleDraftDayMinute, setScheduleDraftDayMinute] = useState('00')
  const [scheduleDraftWeekday, setScheduleDraftWeekday] = useState<ScheduleWeekday>('mon')
  const [scheduleDraftMonthDay, setScheduleDraftMonthDay] = useState('1')
  const [scheduleDraftWorkspaceId, setScheduleDraftWorkspaceId] = useState(activeWorkspaceId || 'default')
  const [scheduledMenuJobId, setScheduledMenuJobId] = useState<string | null>(null)

  const isEditingJob = scheduleEditingJobId !== null

  const canConfirm = scheduleDraftName.trim().length > 0 && scheduleDraftInstruction.trim().length > 0

  useEffect(() => {
    if (!scheduledMenuJobId) return
    function onDoc(event: MouseEvent) {
      const target = event.target
      if (!(target instanceof Element)) return
      if (target.closest('[data-scheduled-menu-root]')) return
      if (target.closest('[data-scheduled-menu-button]')) return
      setScheduledMenuJobId(null)
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') setScheduledMenuJobId(null)
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [scheduledMenuJobId])

  const scheduleLabel = useCallback((spec: ScheduleSpec): string => {
    const pad2 = (v: string | number) => String(v).padStart(2, '0')
    if (spec.type === 'minute') return `每 ${spec.everyMinutes} 分钟`
    if (spec.type === 'hour') return `每小时 ${pad2(spec.minute)} 分`
    if (spec.type === 'day') return `每天 ${pad2(spec.hour)}:${pad2(spec.minute)}`
    if (spec.type === 'week') return `每周${WEEKDAY_LABEL[spec.weekday]} ${pad2(spec.hour)}:${pad2(spec.minute)}`
    return `每月 ${spec.day} 日 ${pad2(spec.hour)}:${pad2(spec.minute)}`
  }, [])

  const displayedJobs = useMemo(() => {
    // 按创建时间展示（最新创建的在前），不再按 enabled/name 排序
    return [...scheduledJobs].sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0))
  }, [scheduledJobs])

  // 执行历史：聚合所有任务的运行记录，按触发时间倒序（最新在前），供独立「执行历史」tab 展示。
  const historyRecords = useMemo<HistoryRecord[]>(() => {
    const jobNameById = new Map(scheduledJobs.map((j) => [j.id, j.name]))
    const all: HistoryRecord[] = []
    for (const [jobId, runs] of Object.entries(runsByJob)) {
      const jobName = jobNameById.get(jobId) ?? '已删除任务'
      for (const run of runs) all.push({ ...run, jobId, jobName })
    }
    all.sort((a, b) => b.fired_at - a.fired_at)
    return all
  }, [runsByJob, scheduledJobs])

  // 是否还有更早的历史：任一任务本次拉取返回了整页（== historyLimit）即认为可能还有更多。
  // 边界情况（某任务恰好 historyLimit 条且已取尽）会多一次点击后按钮消失，可接受。
  const hasMoreHistory = useMemo(
    () => Object.values(runsByJob).some((runs) => runs.length >= historyLimit),
    [runsByJob, historyLimit],
  )

  const createTitledSession = useCallback(
    async (title: string, type: 'normal' | 'scheduled' = 'normal'): Promise<string> => {
      let convId: string | null = null
      if (token) {
        try {
          const res = await fetch(apiUrl('/api/sessions'), {
            method: 'POST',
            headers: {
              Authorization: `Bearer ${token}`,
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({ workspace_id: activeWorkspaceId || 'default' }),
          })
          if (res.ok) {
            const data = await res.json()
            convId = typeof data?.session_id === 'string' ? data.session_id : null
          } else if (res.status === 401) {
            onLogout()
          }
        } catch {}
      }
      if (!convId) {
        convId = `conv_${Date.now()}`
      }

      const safeTitle = (title || '').trim() || '新任务'
      // 与 useConversationController.sendMessage 保持一致：必须带 workspaceId，
      // 否则 groupedHistoryConversations 会把它错放到"默认"分组。
      prependConversation({
        id: convId,
        title: safeTitle,
        updatedAt: Date.now(),
        active: false,
        type,
        workspaceId: activeWorkspaceId || 'default',
      })

      if (token) {
        try {
          const res = await fetch(apiUrl(`/api/sessions/${encodeURIComponent(convId)}`), {
            method: 'PATCH',
            headers: {
              Authorization: `Bearer ${token}`,
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({ title: safeTitle }),
          })
          if (!res.ok && res.status === 401) onLogout()
        } catch {}
      }

      return convId
    },
    [activeWorkspaceId, onLogout, prependConversation, token],
  )

  const triggerScheduledRun = useCallback(
    async (jobId: string) => {
      if (!token) return
      // 仅覆盖 trigger → started 之间的空窗；真实状态由 WS 事件与 /runs 决定。
      setLiveRuns((prev) => ({ ...prev, [jobId]: { runId: '', status: 'pending' } }))
      const clear = () =>
        setLiveRuns((prev) => {
          if (!(jobId in prev)) return prev
          const next = { ...prev }
          delete next[jobId]
          return next
        })
      try {
        const res = await fetch(apiUrl(`/api/scheduled-tasks/${encodeURIComponent(jobId)}/trigger`), {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        })
        if (res.status === 401) { clear(); onLogout(); return }
        if (!res.ok) {
          let detail = `请求失败（${res.status}）`
          try {
            const body = await res.json()
            if (body && body.detail) detail = String(body.detail)
          } catch {}
          toast(`执行失败：${detail}`, 'error')
          clear()
          return
        }
        const body = await res.json().catch(() => null)
        const runId = typeof body?.run_id === 'string' ? body.run_id : ''
        setLiveRuns((prev) => {
          const current = prev[jobId]
          // started / completed 可能先于 HTTP 响应抵达浏览器：
          // 已完成就不要把 pending 塞回去，已执行中只补 run_id、不回退状态。
          if (!current) return prev
          return { ...prev, [jobId]: { runId: current.runId || runId, status: current.status } }
        })
        toast('已触发执行，任务已加入执行队列', 'success')
        void fetchTasks()
      } catch {
        toast('执行请求失败，请检查网络后重试', 'error')
        clear()
      }
    },
    [fetchTasks, onLogout, toast, token],
  )

  /** Re-populate schedule-spec derived drafts from a ScheduleSpec. Does not
   * touch name/instruction — those are caller-owned (openCreate clears them;
   * openEdit seeds them from the existing job first). */
  const resetDraftFromSpec = useCallback((spec: ScheduleSpec | null) => {
    if (spec === null) {
      // 新建：用 openCreate 的静态默认值（每分钟 + 30 分钟执行）
      setScheduleDraftType('minute')
      setScheduleDraftEveryMinutes('30')
      setScheduleDraftHourMinute('00')
      setScheduleDraftDayHour('09')
      setScheduleDraftDayMinute('00')
      setScheduleDraftWeekday('mon')
      setScheduleDraftMonthDay('1')
      return
    }
    // 编辑：按已有 spec 反推每个 draft 字段
    setScheduleDraftType(spec.type)
    setScheduleDraftEveryMinutes(
      spec.type === 'minute' ? String(spec.everyMinutes ?? 30) : '30',
    )
    setScheduleDraftHourMinute(spec.type === 'hour' ? spec.minute : '00')
    setScheduleDraftDayHour(
      spec.type === 'day' || spec.type === 'week' || spec.type === 'month'
        ? spec.hour
        : '09',
    )
    setScheduleDraftDayMinute(
      spec.type === 'day' || spec.type === 'week' || spec.type === 'month'
        ? spec.minute
        : '00',
    )
    setScheduleDraftWeekday(spec.type === 'week' ? spec.weekday : 'mon')
    setScheduleDraftMonthDay(spec.type === 'month' ? spec.day : '1')
  }, [])

  const openCreateModal = useCallback(() => {
    setScheduleEditingJobId(null)
    setScheduleDraftName('')
    setScheduleDraftInstruction('')
    setScheduleDraftWorkspaceId(activeWorkspaceId || 'default')
    resetDraftFromSpec(null)
    setScheduleModalOpen(true)
  }, [resetDraftFromSpec, activeWorkspaceId])

  const openEditModal = useCallback((job: ScheduledJob) => {
    // 先设置 caller-owned 字段；resetDraftFromSpec 不能碰它们（避免最后一行 setState 覆盖前面的赋值）
    setScheduleEditingJobId(job.id)
    setScheduledMenuJobId(null)
    setScheduleDraftName(job.name)
    setScheduleDraftInstruction(job.instruction)
    setScheduleDraftWorkspaceId(job.workspaceId || activeWorkspaceId || 'default')
    resetDraftFromSpec(job.schedule)
    setScheduleModalOpen(true)
  }, [resetDraftFromSpec, activeWorkspaceId])

  const closeScheduleModal = useCallback(() => {
    setScheduleModalOpen(false)
    setScheduleEditingJobId(null)
  }, [])

  const selectedScheduleSpec = useMemo<ScheduleSpec>(() => {
    return scheduleDraftType === 'minute'
      ? { type: 'minute', everyMinutes: Math.max(1, Number(scheduleDraftEveryMinutes || '1')) }
      : scheduleDraftType === 'hour'
        ? { type: 'hour', minute: scheduleDraftHourMinute }
        : scheduleDraftType === 'day'
          ? { type: 'day', hour: scheduleDraftDayHour, minute: scheduleDraftDayMinute }
          : scheduleDraftType === 'week'
            ? { type: 'week', weekday: scheduleDraftWeekday, hour: scheduleDraftDayHour, minute: scheduleDraftDayMinute }
            : { type: 'month', day: scheduleDraftMonthDay, hour: scheduleDraftDayHour, minute: scheduleDraftDayMinute }
  }, [
    scheduleDraftDayHour,
    scheduleDraftDayMinute,
    scheduleDraftEveryMinutes,
    scheduleDraftHourMinute,
    scheduleDraftMonthDay,
    scheduleDraftType,
    scheduleDraftWeekday,
  ])

  const submitModal = useCallback(async () => {
    const name = scheduleDraftName.trim()
    const instruction = scheduleDraftInstruction.trim()
    if (!name || !instruction || !token) return
    const schedule = selectedScheduleSpec
    const isEdit = scheduleEditingJobId !== null

    try {
      const url = isEdit
        ? apiUrl(`/api/scheduled-tasks/${encodeURIComponent(scheduleEditingJobId!)}`)
        : apiUrl('/api/scheduled-tasks')
      const res = await fetch(url, {
        method: isEdit ? 'PATCH' : 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, instruction, schedule, workspace_id: scheduleDraftWorkspaceId }),
      })
      if (res.status === 401) { onLogout(); return }
      if (!res.ok) return
    } catch {
      return
    }

    setScheduleModalOpen(false)
    setScheduleEditingJobId(null)
    void fetchTasks()
  }, [
    fetchTasks,
    onLogout,
    scheduleDraftInstruction,
    scheduleDraftName,
    scheduleDraftWorkspaceId,
    scheduleEditingJobId,
    selectedScheduleSpec,
    token,
  ])

  return (
    <div className="flex-1 min-w-0 min-h-0 flex flex-col relative app-panel overflow-hidden">
    <div className="flex-1 overflow-hidden flex flex-col min-w-0">
      <div className="px-8 pt-7 pb-5 shrink-0">
        <div className="max-w-[1200px] mx-auto w-full flex items-start justify-between gap-6">
          <div className="min-w-0">
            <div className="text-[length:var(--fs-xl)] font-semibold text-[var(--c-text)] tracking-tight">自动化</div>
            <div className="mt-1 text-[length:var(--fs-md)] text-[var(--c-text-secondary)]">
              设置定时任务，让助手按时自动执行，或通过对话快速创建
            </div>
          </div>
          <div className="shrink-0 flex items-center gap-2">
            <button
              type="button"
              onClick={() => void fetchTasks()}
              className="h-10 w-10 rounded-[var(--radius-card)] hover:bg-[var(--c-surface-2)] transition-colors cursor-pointer grid place-items-center"
              title="刷新"
            >
              <RefreshCw size={18} strokeWidth={2} className="text-[var(--c-text-secondary)]" />
            </button>

            <button
              type="button"
              onClick={() => {
                void (async () => {
                  const convId = await createTitledSession('通过对话创建自动化')
                  // 跳转会话页后预填创建自动化任务的指令，用户可直接发送或微调。
                  setPendingComposerPrefill({
                    text: '我想创建一个自动化任务「每日AI新闻整理」，需要「搜索过去24小时内的AI相关新闻，完成分类与重要性评级后，在界面以结构化方式输出结果」，「每天上午10点」执行，创建后立即启用。',
                    nonce: Date.now(),
                  })
                  setActiveSection('chat')
                  await onSelectSession(convId)
                })()
              }}
              className="btn btn-lg btn-secondary"
            >
              <MessageSquare size={15} strokeWidth={2} />
              通过对话创建
            </button>

            <PrimaryButton
              type="button"
              onClick={openCreateModal}
              size="lg"
            >
              + 创建新任务
            </PrimaryButton>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6">
        <div className="max-w-[1200px] mx-auto w-full pb-10">
          <div className="flex items-center gap-8 mb-5 border-b border-[var(--c-border)]/50">
            <button
              type="button"
              onClick={() => setView('tasks')}
              className={`relative pl-3 pr-3 pb-2.5 text-[length:var(--fs-lg)] font-semibold transition-colors cursor-pointer ${
                view === 'tasks'
                  ? 'text-[var(--c-text)]'
                  : 'text-[var(--c-text-secondary)] hover:text-[var(--c-text)]'
              }`}
            >
              任务列表
              {view === 'tasks' ? (
                <span className="absolute left-3 right-3 -bottom-px h-0.5 rounded-full bg-[var(--c-accent)]" />
              ) : null}
            </button>
            <button
              type="button"
              onClick={() => setView('history')}
              className={`relative pl-3 pr-3 pb-2.5 text-[length:var(--fs-lg)] font-semibold transition-colors cursor-pointer ${
                view === 'history'
                  ? 'text-[var(--c-text)]'
                  : 'text-[var(--c-text-secondary)] hover:text-[var(--c-text)]'
              }`}
            >
              执行历史
              {view === 'history' ? (
                <span className="absolute left-3 right-3 -bottom-px h-0.5 rounded-full bg-[var(--c-accent)]" />
              ) : null}
            </button>
          </div>

          {view === 'tasks' ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {displayedJobs.map((job) => (
              <div
                key={job.id}
                role="button"
                tabIndex={0}
                onClick={() => openEditModal(job)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    openEditModal(job)
                  }
                }}
                className="app-card flex flex-col h-full px-5 pt-5 pb-4 cursor-pointer transition-all hover:border-[var(--c-border-strong)] hover:bg-[var(--c-surface)]/40 focus:outline-none focus-visible:ring-1 focus-visible:ring-[var(--c-accent)]/30"
              >
                <div className="flex items-start justify-between">
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation()
                      void setEnabled(job, !job.enabled)
                    }}
                    className={`h-6 w-11 rounded-full border transition-colors cursor-pointer flex items-center shrink-0 ${
                      job.enabled
                        ? 'bg-[var(--c-accent)] border-[var(--c-accent)]'
                        : 'bg-[var(--c-border)] border-[var(--c-border)]'
                    }`}
                    title={job.enabled ? '已开启' : '已关闭'}
                    aria-label={job.enabled ? '已开启' : '已关闭'}
                  >
                    <span
                      className={`h-5 w-5 rounded-full bg-white transition-transform ${
                        job.enabled ? 'translate-x-5' : 'translate-x-1'
                      }`}
                    />
                  </button>

                  <div className="relative" data-scheduled-menu-root>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation()
                        setScheduledMenuJobId((v) => (v === job.id ? null : job.id))
                      }}
                      data-scheduled-menu-button
                      className="h-8 w-8 rounded-[var(--radius-control)] hover:bg-[var(--c-surface-2)]/50 transition-colors cursor-pointer grid place-items-center"
                      aria-label="更多"
                      title="更多"
                    >
                      <MoreHorizontal size={16} className="text-[var(--c-text-secondary)]" />
                    </button>

                    {scheduledMenuJobId === job.id ? (
                      <div className="absolute right-0 top-full mt-1 z-50">
                        <div className="overlay-panel min-w-[148px] py-1 rounded-[var(--radius-card)] overflow-hidden animate-in">
                          <button
                            type="button"
                            disabled={Boolean(liveRuns[job.id])}
                            onClick={(e) => {
                              e.stopPropagation()
                              setScheduledMenuJobId(null)
                              void triggerScheduledRun(job.id)
                            }}
                            className="w-full flex items-center gap-2 px-3 py-1.5 rounded-[var(--radius-md)] text-left text-[length:var(--fs-sm)] font-medium text-[var(--c-text)] hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer whitespace-nowrap disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {liveRuns[job.id] ? (
                              <span className="h-3.5 w-3.5 rounded-full border-2 border-[var(--c-text-secondary)] border-t-transparent animate-spin shrink-0" />
                            ) : (
                              <Play size={14} className="shrink-0" />
                            )}
                            {liveRuns[job.id]?.status === 'pending' ? '排队中…' : liveRuns[job.id] ? '执行中…' : '立即执行'}
                          </button>
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation()
                              if (!token) return
                              if (!confirm(`确认删除任务「${job.name}」？此操作不可撤销。`)) return
                              fetch(apiUrl(`/api/scheduled-tasks/${encodeURIComponent(job.id)}`), {
                                method: 'DELETE',
                                headers: { Authorization: `Bearer ${token}` },
                              }).then((res) => { if (res.status === 401) onLogout(); void fetchTasks() }).catch(() => {})
                              setScheduledMenuJobId(null)
                            }}
                            className="w-full flex items-center gap-2 px-3 py-1.5 rounded-[var(--radius-md)] text-left text-[length:var(--fs-sm)] font-medium text-[var(--c-error)] hover:bg-[var(--c-error)]/10 transition-colors cursor-pointer whitespace-nowrap"
                          >
                            <Trash2 size={14} className="shrink-0" />
                            删除任务
                          </button>
                        </div>
                      </div>
                    ) : null}
                  </div>
                </div>

                {!job.enabled && job.lastStatus === 'disabled' ? (
                  <div className="mt-3 flex items-center gap-2 rounded-[var(--radius-md)] bg-[var(--c-danger)]/10 px-3 py-2 text-[length:var(--fs-sm)] text-[var(--c-danger)]">
                    <AlertTriangle size={14} className="shrink-0" />
                    <span className="flex-1 leading-snug">因连续执行失败已被自动停用</span>
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); void setEnabled(job, true) }}
                      className="shrink-0 font-medium underline underline-offset-2 hover:opacity-80 cursor-pointer"
                    >
                      重新启用
                    </button>
                  </div>
                ) : null}

                <h3 className="mt-4 text-[length:var(--fs-md)] font-semibold text-[var(--c-text)] tracking-tight truncate" title={job.name}>{job.name}</h3>

                <p className="mt-2 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)] leading-relaxed line-clamp-2 min-h-[3.25em]">
                  {job.instruction}
                </p>

                <HairlineDivider className="mt-5 mb-5" />

                <div className="flex flex-wrap items-center gap-2">
                  <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-[var(--c-surface-2)] text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">
                    <Clock size={13} className="shrink-0" />
                    <span className="tabular-nums">{scheduleLabel(job.schedule)}</span>
                  </div>
                  <RunStatusBadge run={liveRuns[job.id]} latest={runsByJob[job.id]?.[0]} />
                </div>
              </div>
            ))}
          </div>
          ) : (
            <>
              <ExecutionHistoryList records={historyRecords} onSelectSession={onSelectSession} />
              {hasMoreHistory ? (
                <div className="mt-8 text-center">
                  <button
                    type="button"
                    disabled={historyLoading}
                    onClick={() => void loadHistoryPage(historyLimit + HISTORY_PAGE_SIZE)}
                    className="h-10 px-6 rounded-[var(--radius-card)] border border-[var(--c-border)] bg-[var(--c-surface)] text-[length:var(--fs-sm)] font-medium text-[var(--c-text-secondary)] hover:bg-[var(--c-surface-2)]/50 hover:text-[var(--c-text)] transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {historyLoading ? '加载中…' : '加载更多'}
                  </button>
                </div>
              ) : null}
            </>
          )}

        </div>
      </div>

      {scheduleModalOpen && (
        <div
          className="overlay-scrim fixed inset-0 z-50 grid place-items-center px-4"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) closeScheduleModal()
          }}
        >
          <div
            className="overlay-panel w-full max-w-[760px] rounded-[var(--radius-card)] overflow-hidden flex flex-col max-h-[min(720px,calc(100vh-64px))]"
            style={{
              background: 'var(--c-surface)',
              border: '1px solid var(--c-border)',
              backdropFilter: 'none',
              WebkitBackdropFilter: 'none',
            }}
          >
            <div className="px-8 pt-6 pb-5 flex items-start justify-between border-b border-[var(--c-border)]/60">
              <div className="space-y-1">
                <div className="text-[length:var(--fs-xl)] font-bold text-[var(--c-text)] tracking-tight">
                  {isEditingJob ? '编辑任务' : '创建新任务'}
                </div>
                <div className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">
                  {isEditingJob
                    ? `正在修改「${scheduleDraftName || '未命名任务'}」的指令与执行周期`
                    : '填写基本信息、选择工作区，再设定执行周期'}
                </div>
              </div>
              <button
                type="button"
                onClick={closeScheduleModal}
                className="-mr-1 -mt-1 btn btn-icon btn-ghost"
                aria-label="关闭"
              >
                <X size={18} />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto px-8 py-6 space-y-6">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="text-[length:var(--fs-md)] font-medium text-[var(--c-text)]">
                    任务名称 <span className="text-[var(--c-error)]">*</span>
                  </div>
                  <div className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">{scheduleDraftName.length}/50</div>
                </div>
                <input
                  value={scheduleDraftName}
                  onChange={(e) => setScheduleDraftName(e.target.value)}
                  placeholder="请输入"
                  maxLength={50}
                  className="w-full h-11 px-4 rounded-[var(--radius-md)] bg-[var(--c-surface-2)] border border-[var(--c-border)]
                             text-[length:var(--fs-md)] text-[var(--c-text)] placeholder:text-[var(--c-text-secondary)]/70
                             hover:border-[var(--c-border-strong)] focus:outline-none focus:ring-1 focus:ring-[var(--c-accent)]/20 focus:border-[var(--c-accent)] focus:bg-[var(--c-surface)] transition-colors"
                />
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="text-[length:var(--fs-md)] font-medium text-[var(--c-text)]">
                    任务指令 <span className="text-[var(--c-error)]">*</span>
                  </div>
                  <div className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">
                    {scheduleDraftInstruction.length}/3000
                  </div>
                </div>
                <textarea
                  value={scheduleDraftInstruction}
                  onChange={(e) => setScheduleDraftInstruction(e.target.value)}
                  placeholder="请输入"
                  maxLength={3000}
                  rows={5}
                  className="w-full px-4 py-3 rounded-[var(--radius-md)] bg-[var(--c-surface-2)] border border-[var(--c-border)]
                             text-[length:var(--fs-md)] text-[var(--c-text)] placeholder:text-[var(--c-text-secondary)]/70 resize-none
                             hover:border-[var(--c-border-strong)] focus:outline-none focus:ring-1 focus:ring-[var(--c-accent)]/20 focus:border-[var(--c-accent)] focus:bg-[var(--c-surface)] transition-colors"
                />
              </div>

              <div className="space-y-2">
                <div className="text-[length:var(--fs-md)] font-medium text-[var(--c-text)]">
                  执行工作区
                </div>
                <SelectField
                  value={scheduleDraftWorkspaceId}
                  onChange={(v) => setScheduleDraftWorkspaceId(v)}
                >
                  {workspaces.map((ws) => (
                    <option key={ws.id} value={ws.id}>{ws.name}</option>
                  ))}
                </SelectField>
                <div className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)] leading-relaxed">
                  任务将在所选工作区中独立执行，不设定则使用默认工作区
                </div>
              </div>

              <div className="space-y-2">
                <div className="text-[length:var(--fs-md)] font-medium text-[var(--c-text)]">
                  执行周期 <span className="text-[var(--c-error)]">*</span>
                </div>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-3">
                  <SelectField
                    value={scheduleDraftType}
                    onChange={(v) => setScheduleDraftType(v as ScheduleType)}
                    inline
                    className="min-w-[140px]"
                  >
                    <option value="minute">每分钟执行</option>
                    <option value="hour">每小时执行</option>
                    <option value="day">每天执行</option>
                    <option value="week">每周执行</option>
                    <option value="month">每月执行</option>
                  </SelectField>

                  {scheduleDraftType === 'minute' ? (
                    <div className="flex flex-nowrap items-center gap-2 text-[length:var(--fs-md)] text-[var(--c-text-secondary)]">
                      <span className="whitespace-nowrap">每</span>
                      <input
                        value={scheduleDraftEveryMinutes}
                        onChange={(e) =>
                          setScheduleDraftEveryMinutes(e.target.value.replace(/[^\d]/g, '').slice(0, 4))
                        }
                        inputMode="numeric"
                        className="flex-shrink-0 w-20 h-11 px-4 rounded-[var(--radius-md)] bg-[var(--c-surface-2)] border border-[var(--c-border)]
                                   text-[length:var(--fs-md)] text-[var(--c-text)] hover:border-[var(--c-border-strong)]
                                   focus:outline-none focus:ring-1 focus:ring-[var(--c-accent)]/20 focus:border-[var(--c-accent)] focus:bg-[var(--c-surface)] transition-colors"
                      />
                      <span className="whitespace-nowrap">分钟执行</span>
                    </div>
                  ) : scheduleDraftType === 'hour' ? (
                    <div className="flex flex-nowrap items-center gap-2 text-[length:var(--fs-md)] text-[var(--c-text-secondary)]">
                      <span className="whitespace-nowrap">每小时第</span>
                      <SelectField
                        value={scheduleDraftHourMinute}
                        onChange={(v) => setScheduleDraftHourMinute(v)}
                        inline
                        className="min-w-[120px]"
                      >
                        {Array.from({ length: 60 }).map((_, i) => {
                          const v = String(i).padStart(2, '0')
                          return (
                            <option key={v} value={v}>
                              {v} 分
                            </option>
                          )
                        })}
                      </SelectField>
                      <span className="whitespace-nowrap">执行</span>
                    </div>
                  ) : scheduleDraftType === 'day' ? (
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-2 text-[length:var(--fs-md)] text-[var(--c-text-secondary)]">
                      <span className="whitespace-nowrap">每天</span>
                      <SelectField
                        value={scheduleDraftDayHour}
                        onChange={(v) => setScheduleDraftDayHour(v)}
                        inline
                        className="min-w-[120px]"
                      >
                        {Array.from({ length: 24 }).map((_, i) => {
                          const v = String(i).padStart(2, '0')
                          return (
                            <option key={v} value={v}>
                              {v} 时
                            </option>
                          )
                        })}
                      </SelectField>
                      <SelectField
                        value={scheduleDraftDayMinute}
                        onChange={(v) => setScheduleDraftDayMinute(v)}
                        inline
                        className="min-w-[120px]"
                      >
                        {Array.from({ length: 60 }).map((_, i) => {
                          const v = String(i).padStart(2, '0')
                          return (
                            <option key={v} value={v}>
                              {v} 分
                            </option>
                          )
                        })}
                      </SelectField>
                      <span className="whitespace-nowrap">执行</span>
                    </div>
                  ) : scheduleDraftType === 'week' ? (
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-2 text-[length:var(--fs-md)] text-[var(--c-text-secondary)]">
                      <span className="whitespace-nowrap">每周</span>
                      <SelectField
                        value={scheduleDraftWeekday}
                        onChange={(v) => setScheduleDraftWeekday(v as ScheduleWeekday)}
                        inline
                        className="min-w-[120px]"
                      >
                        {(
                          [
                            ['mon', '周一'],
                            ['tue', '周二'],
                            ['wed', '周三'],
                            ['thu', '周四'],
                            ['fri', '周五'],
                            ['sat', '周六'],
                            ['sun', '周日'],
                          ] as const
                        ).map(([v, label]) => (
                          <option key={v} value={v}>
                            {label}
                          </option>
                        ))}
                      </SelectField>
                      <SelectField
                        value={scheduleDraftDayHour}
                        onChange={(v) => setScheduleDraftDayHour(v)}
                        inline
                        className="min-w-[120px]"
                      >
                        {Array.from({ length: 24 }).map((_, i) => {
                          const v = String(i).padStart(2, '0')
                          return (
                            <option key={v} value={v}>
                              {v} 时
                            </option>
                          )
                        })}
                      </SelectField>
                      <SelectField
                        value={scheduleDraftDayMinute}
                        onChange={(v) => setScheduleDraftDayMinute(v)}
                        inline
                        className="min-w-[120px]"
                      >
                        {Array.from({ length: 60 }).map((_, i) => {
                          const v = String(i).padStart(2, '0')
                          return (
                            <option key={v} value={v}>
                              {v} 分
                            </option>
                          )
                        })}
                      </SelectField>
                      <span className="whitespace-nowrap">执行</span>
                    </div>
                  ) : (
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-2 text-[length:var(--fs-md)] text-[var(--c-text-secondary)]">
                      <span className="whitespace-nowrap">每月</span>
                      <SelectField
                        value={scheduleDraftMonthDay}
                        onChange={(v) => setScheduleDraftMonthDay(v)}
                        inline
                        className="min-w-[120px]"
                      >
                        {Array.from({ length: 31 }).map((_, i) => {
                          const day = String(i + 1)
                          return (
                            <option key={day} value={day}>
                              {day} 日
                            </option>
                          )
                        })}
                      </SelectField>
                      <SelectField
                        value={scheduleDraftDayHour}
                        onChange={(v) => setScheduleDraftDayHour(v)}
                        inline
                        className="min-w-[120px]"
                      >
                        {Array.from({ length: 24 }).map((_, i) => {
                          const v = String(i).padStart(2, '0')
                          return (
                            <option key={v} value={v}>
                              {v} 时
                            </option>
                          )
                        })}
                      </SelectField>
                      <SelectField
                        value={scheduleDraftDayMinute}
                        onChange={(v) => setScheduleDraftDayMinute(v)}
                        inline
                        className="min-w-[120px]"
                      >
                        {Array.from({ length: 60 }).map((_, i) => {
                          const v = String(i).padStart(2, '0')
                          return (
                            <option key={v} value={v}>
                              {v} 分
                            </option>
                          )
                        })}
                      </SelectField>
                      <span className="whitespace-nowrap">执行</span>
                    </div>
                  )}
                </div>
              </div>
            </div>
            <div className="px-8 py-3.5 border-t border-[var(--c-border)]/70 flex items-center justify-between gap-3">
              <span className="text-[length:var(--fs-xs)] text-[var(--c-text-tertiary)] truncate">
                {isEditingJob ? '保存后按新的周期执行' : '创建后按设定的周期自动执行'}
              </span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={closeScheduleModal}
                  className="btn btn-md btn-ghost"
                >
                  取消
                </button>
                <PrimaryButton
                  type="button"
                  onClick={submitModal}
                  disabled={!canConfirm}
                >
                  {isEditingJob ? '保存' : '确认'}
                </PrimaryButton>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
    </div>
  )
}

function formatRunTime(ts: number): string {
  const d = new Date(ts * 1000)
  const pad = (v: number) => String(v).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function RunStatusBadge({ run, latest }: { run?: LiveRun; latest?: RunRecord }) {
  if (run) {
    return (
      <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-[var(--c-accent)]/10 text-[length:var(--fs-sm)] font-medium text-[var(--c-accent)]">
        <span className="h-3 w-3 rounded-full border-2 border-[var(--c-accent)] border-t-transparent animate-spin" />
        {run.status === 'pending' ? '排队中…' : '执行中…'}
      </div>
    )
  }
  if (!latest) return null
  // 进程崩溃后 at-most-once 语义会让 run 永久停在 pending/running；
  // 既不能伪装成「执行中」，也不能误报成「上次失败」。
  if (latest.status === 'pending' || latest.status === 'running') {
    return (
      <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-[var(--c-surface-2)] text-[length:var(--fs-sm)] font-medium text-[var(--c-text-secondary)]">
        <span className="h-1.5 w-1.5 rounded-full bg-[var(--c-text-tertiary)]" />
        {latest.status === 'pending' ? '上次排队未完成' : '上次执行未完成'}
      </div>
    )
  }
  const ok = latest.status === 'success'
  return (
    <div className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[length:var(--fs-sm)] font-medium ${ok ? 'bg-[var(--c-success)]/10 text-[var(--c-success)]' : 'bg-[var(--c-danger)]/10 text-[var(--c-danger)]'}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${ok ? 'bg-[var(--c-success)]' : 'bg-[var(--c-danger)]'}`} />
      上次{ok ? '成功' : '失败'}
      {latest.duration_s != null ? ` · ${latest.duration_s.toFixed(1)}s` : ''}
    </div>
  )
}

function ExecutionHistoryList({
  records,
  onSelectSession,
}: {
  records: HistoryRecord[]
  onSelectSession: (convId: string) => Promise<void>
}) {
  if (records.length === 0) {
    return (
      <div className="text-center py-24 text-[length:var(--fs-md)] text-[var(--c-text-secondary)]">
        暂无执行历史
      </div>
    )
  }

  // 按自然日（本地时区）分组，组内保持触发时间倒序。
  const groups: { dateLabel: string; items: HistoryRecord[] }[] = []
  for (const rec of records) {
    const d = new Date(rec.fired_at * 1000)
    const pad = (v: number) => String(v).padStart(2, '0')
    const dateLabel = `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())}`
    const last = groups[groups.length - 1]
    if (last && last.dateLabel === dateLabel) last.items.push(rec)
    else groups.push({ dateLabel, items: [rec] })
  }

  return (
    <div className="space-y-8">
      {groups.map((group) => (
        <div key={group.dateLabel}>
          <div className="mb-3 text-[length:var(--fs-sm)] font-medium text-[var(--c-text-secondary)]">{group.dateLabel}</div>
          <div className="relative">
            {/* 连续竖向时间轴：单条轴线贯穿当日全部记录，消除逐条卡片的密集边框 */}
            <div className="absolute left-[5px] top-1.5 bottom-1.5 w-px bg-[var(--c-border)]" />
            <div className="space-y-5">
              {group.items.map((rec) => {
                const ok = rec.status === 'success'
                const failed = rec.status === 'failed'
                const dot = ok ? 'bg-[var(--c-success)]' : failed ? 'bg-[var(--c-danger)]' : 'bg-[var(--c-text-tertiary)]'
                const statusText =
                  rec.status === 'success' ? '成功'
                  : rec.status === 'failed' ? '失败'
                  : rec.status === 'running' ? '执行中'
                  : rec.status === 'pending' ? '排队中'
                  : rec.status
                return (
                  <div key={rec.id} className="relative pl-6">
                    <span className={`absolute left-0 top-1.5 h-2.5 w-2.5 rounded-full ${dot}`} />
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-[length:var(--fs-md)] font-medium text-[var(--c-text)] truncate">{rec.jobName}</span>
                        <span className={`text-[length:var(--fs-xs)] ${failed ? 'text-[var(--c-danger)]' : 'text-[var(--c-text-secondary)]'}`}>{statusText}</span>
                      </div>
                      <div className="mt-0.5 flex items-center gap-3 text-[length:var(--fs-xs)] text-[var(--c-text-tertiary)] tabular-nums flex-wrap">
                        <span>{formatRunTime(rec.fired_at)}</span>
                        {rec.duration_s != null ? <span>耗时 {rec.duration_s.toFixed(1)}s</span> : null}
                      </div>
                      {failed && rec.error ? (
                        <div className="mt-1 text-[length:var(--fs-xs)] text-[var(--c-danger)] leading-relaxed line-clamp-2">{rec.error}</div>
                      ) : null}
                      {rec.session_id ? (
                        <button
                          type="button"
                          onClick={() => void onSelectSession(rec.session_id as string)}
                          className="mt-1 text-[length:var(--fs-xs)] text-[var(--c-text-secondary)] underline underline-offset-2 hover:text-[var(--c-text)] cursor-pointer"
                        >
                          查看会话
                        </button>
                      ) : null}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

// Browser-native <select> ships with an inline chevron whose border radius does
// not track the wrapper's rounded-[var(--radius-md)], so the chevron's squared corner visibly
// abuts the wrapper edge ("the corner sits on the border"). Suppress the native
// chrome via appearance-none and overlay our own absolute-positioned ChevronDown
// — radius, color, and spacing are then fully controlled.
type SelectFieldProps = {
  value: string
  onChange: (next: string) => void
  className?: string
  /** 横向流（多控件同行）时设为 true；wrapper 改为 inline-flex 自适应内容宽度，
   *  否则默认 w-full 占满父级宽度（适合独立字段独占一行的纵向布局）。 */
  inline?: boolean
  children: React.ReactNode
}

function SelectField({ value, onChange, className, inline = false, children }: SelectFieldProps) {
  return (
    <div className={`relative ${inline ? 'inline-flex w-auto' : 'w-full'}`}>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={
          'appearance-none w-full h-11 pl-4 pr-10 rounded-[var(--radius-md)] border border-[var(--c-border)] bg-[var(--c-surface-2)] text-[length:var(--fs-md)] text-[var(--c-text)] cursor-pointer hover:border-[var(--c-border-strong)] focus:outline-none focus:ring-1 focus:ring-[var(--c-accent)]/20 focus:border-[var(--c-accent)] focus:bg-[var(--c-surface)] transition-colors ' +
          (className ?? '')
        }
      >
        {children}
      </select>
      <ChevronDown
        size={15}
        className="pointer-events-none absolute right-3.5 top-1/2 -translate-y-1/2 text-[var(--c-text-secondary)]"
      />
    </div>
  )
}
