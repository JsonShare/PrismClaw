import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  AlertTriangle, Calendar, ChevronDown, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Clock,
  MessageSquare, Mic, MoreHorizontal, Pencil, Play, Plus, Search, SquarePen, Trash2, X,
} from 'lucide-react'
import { FolderIcon, RefreshIcon } from '../../../components/icons/SystemIcons'
import { apiUrl } from '../../../lib/system-adapter'
import { useSessionStore, useUiStore } from '../../../stores'
import { PrimaryButton } from '../../../components/ui/PrimaryButton'
import { useToast } from '../../../lib/toast'
import templatesData from '../../../data/scheduledTaskTemplates.json'

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
  validFrom: number | null
  validUntil: number | null
}

/** 后端 /api/scheduled-tasks 返回的任务结构（snake_case） */
type ApiScheduledJob = {
  id: string
  name: string
  instruction: string
  schedule: ScheduleSpec
  enabled: boolean
  last_status?: string
  workspace_id?: string
  created_at?: number
  valid_from?: number | null
  valid_until?: number | null
}

/** WS 事件 prismclaw:scheduler-event 的 data 字段 */
type SchedulerEventPayload = {
  task_id?: string
  run_id?: string
  status?: string
  error?: unknown
  reason?: unknown
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

// 周期选择器的候选项（时 / 分 / 日）
const HOUR_OPTIONS = Array.from({ length: 24 }, (_, i) => String(i).padStart(2, '0'))
const MINUTE_OPTIONS = Array.from({ length: 60 }, (_, i) => String(i).padStart(2, '0'))
const MONTH_DAY_OPTIONS = Array.from({ length: 31 }, (_, i) => String(i + 1))

/** 周期文案：每 30 分钟 / 每小时 00 分 / 每天 09:00 / 每周一 20:00 / 每月 1 日 09:00 */
function scheduleLabel(spec: ScheduleSpec): string {
  const pad2 = (v: string | number) => String(v).padStart(2, '0')
  if (spec.type === 'minute') return `每 ${spec.everyMinutes} 分钟`
  if (spec.type === 'hour') return `每小时 ${pad2(spec.minute)} 分`
  if (spec.type === 'day') return `每天 ${pad2(spec.hour)}:${pad2(spec.minute)}`
  if (spec.type === 'week') return `每周${WEEKDAY_LABEL[spec.weekday]} ${pad2(spec.hour)}:${pad2(spec.minute)}`
  return `每月 ${spec.day} 日 ${pad2(spec.hour)}:${pad2(spec.minute)}`
}

type TaskTemplate = {
  key: string
  name: string
  description: string
  schedule: ScheduleSpec
  scheduleLabel: string
  instruction: string
}

// 推荐任务模板：文案与指令维护在 src/data/scheduledTaskTemplates.json
const TEMPLATES = (templatesData as unknown as { templates: TaskTemplate[] }).templates

/** JS getDay() 的星期索引：0=周日 */
const WEEKDAY_INDEX: Record<ScheduleWeekday, number> = {
  mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6, sun: 0,
}

/** 按周期规则推算下一次执行时间，返回 MM-DD HH:mm（本地时区）；超出有效期返回 null。 */
function nextRunLabel(
  spec: ScheduleSpec,
  now = new Date(),
  validFrom: number | null = null,
  validUntil: number | null = null,
): string | null {
  // 有效期开始前不执行：把推算锚点挪到开始日前 1 秒
  const start = validFrom != null && validFrom * 1000 > now.getTime()
    ? new Date(validFrom * 1000 - 1000)
    : now
  const pad = (n: number) => String(n).padStart(2, '0')
  let t: Date
  if (spec.type === 'minute') {
    t = new Date(start.getTime() + spec.everyMinutes * 60_000)
  } else if (spec.type === 'hour') {
    t = new Date(start)
    t.setMinutes(Number(spec.minute), 0, 0)
    if (t.getTime() <= start.getTime()) t.setHours(t.getHours() + 1)
  } else if (spec.type === 'day') {
    t = new Date(start)
    t.setHours(Number(spec.hour), Number(spec.minute), 0, 0)
    if (t.getTime() <= start.getTime()) t.setDate(t.getDate() + 1)
  } else if (spec.type === 'week') {
    t = new Date(start)
    t.setHours(Number(spec.hour), Number(spec.minute), 0, 0)
    let diff = (WEEKDAY_INDEX[spec.weekday] - t.getDay() + 7) % 7
    if (diff === 0 && t.getTime() <= start.getTime()) diff = 7
    t.setDate(t.getDate() + diff)
  } else {
    const at = (monthOffset: number) =>
      new Date(start.getFullYear(), start.getMonth() + monthOffset, Number(spec.day), Number(spec.hour), Number(spec.minute), 0, 0)
    t = at(0)
    if (t.getTime() <= start.getTime()) t = at(1)
  }
  if (validUntil != null && t.getTime() / 1000 > validUntil) return null
  return `${pad(t.getMonth() + 1)}-${pad(t.getDate())} ${pad(t.getHours())}:${pad(t.getMinutes())}`
}

/** 本地日期键（YYYY-MM-DD）↔ epoch 秒：开始日 00:00:00 / 结束日 23:59:59 */
function dateKeyFromTs(ts: number): string {
  const d = new Date(ts * 1000)
  const pad = (v: number) => String(v).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function startOfDaySec(key: string): number {
  const [y, m, d] = key.split('-').map(Number)
  return new Date(y, m - 1, d, 0, 0, 0, 0).getTime() / 1000
}

function endOfDaySec(key: string): number {
  const [y, m, d] = key.split('-').map(Number)
  return new Date(y, m - 1, d, 23, 59, 59, 0).getTime() / 1000
}

function todayKey(): string {
  return dateKeyFromTs(Date.now() / 1000)
}

/** 卡片有效期文案（MM-DD 紧凑展示） */
function validityLabel(from: number | null, until: number | null): string {
  const short = (ts: number) => dateKeyFromTs(ts).slice(5)
  if (from != null && until != null) return `${short(from)} ~ ${short(until)}`
  if (from != null) return `${short(from)} 起`
  return `至 ${short(until as number)}`
}

/** 有效期已结束（用于停用横幅区分「到期」与「连续失败」） */
function isValidityExpired(validUntil: number | null): boolean {
  return validUntil != null && Date.now() / 1000 > validUntil
}

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
  // 页面级视图：定时任务 / 执行记录
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
      const data: ApiScheduledJob[] = await res.json()
      const jobs: ScheduledJob[] = data.map((t) => ({
        id: t.id,
        name: t.name,
        instruction: t.instruction,
        schedule: t.schedule,
        enabled: t.enabled,
        lastStatus: t.last_status ?? 'idle',
        workspaceId: t.workspace_id ?? 'default',
        createdAt: typeof t.created_at === 'number' ? t.created_at : 0,
        validFrom: typeof t.valid_from === 'number' ? t.valid_from : null,
        validUntil: typeof t.valid_until === 'number' ? t.valid_until : null,
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

  const deleteJob = useCallback(
    async (job: ScheduledJob) => {
      if (!token) return
      if (!confirm(`确认删除任务「${job.name}」？此操作不可撤销。`)) return
      try {
        const res = await fetch(apiUrl(`/api/scheduled-tasks/${encodeURIComponent(job.id)}`), {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${token}` },
        })
        if (res.status === 401) { onLogout(); return }
        await fetchTasks()
      } catch {}
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
      const detail = (e as CustomEvent).detail as { type?: string; data?: SchedulerEventPayload } | undefined
      const type = detail?.type
      const data = detail?.data
      const taskId = typeof data?.task_id === 'string' ? data.task_id : null
      const name = scheduledJobsRef.current.find((j) => j.id === taskId)?.name ?? '定时任务'

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
          `「${name}」因连续执行失败已被自动停用${data?.reason ? `（${data.reason}）` : ''}，可在定时任务页重新启用。`,
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
  // 有效期：'' = 未设置（长期有效）。区间为本地自然日，提交时转 epoch 秒。
  const [scheduleDraftValidFrom, setScheduleDraftValidFrom] = useState('')
  const [scheduleDraftValidUntil, setScheduleDraftValidUntil] = useState('')
  const [scheduledMenuJobId, setScheduledMenuJobId] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [newTaskMenuOpen, setNewTaskMenuOpen] = useState(false)
  const [statusFilter, setStatusFilter] = useState<'all' | 'enabled' | 'paused'>('all')

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

  useEffect(() => {
    if (!newTaskMenuOpen) return
    function onDoc(event: MouseEvent) {
      const target = event.target
      if (!(target instanceof Element)) return
      if (target.closest('[data-new-task-menu-root]')) return
      setNewTaskMenuOpen(false)
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') setNewTaskMenuOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [newTaskMenuOpen])

  const displayedJobs = useMemo(() => {
    // 按创建时间展示（最新创建的在前），不再按 enabled/name 排序
    return [...scheduledJobs].sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0))
  }, [scheduledJobs])

  const enabledCount = useMemo(() => displayedJobs.filter((j) => j.enabled).length, [displayedJobs])

  // 搜索 + 状态筛选：任务名 / 指令文本忽略大小写
  const filteredJobs = useMemo(() => {
    const keyword = query.trim().toLowerCase()
    return displayedJobs.filter((job) => {
      if (statusFilter === 'enabled' && !job.enabled) return false
      if (statusFilter === 'paused' && job.enabled) return false
      if (!keyword) return true
      return (
        job.name.toLowerCase().includes(keyword) ||
        job.instruction.toLowerCase().includes(keyword)
      )
    })
  }, [displayedJobs, query, statusFilter])

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
    async (title: string): Promise<string> => {
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

      const safeTitle = title.trim() || '新任务'
      // 与 useConversationController.sendMessage 保持一致：必须带 workspaceId，
      // 否则 groupedHistoryConversations 会把它错放到「默认」分组。
      prependConversation({
        id: convId,
        title: safeTitle,
        updatedAt: Date.now(),
        active: false,
        type: 'normal',
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
          if (res.status === 401) onLogout()
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

  /** 按 ScheduleSpec 回填周期相关的 draft；名称 / 指令由调用方自行设置。 */
  const resetDraftFromSpec = useCallback((spec: ScheduleSpec | null) => {
    if (spec === null) {
      // 新建：默认每 30 分钟
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
    setScheduleDraftValidFrom('')
    setScheduleDraftValidUntil('')
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
    setScheduleDraftValidFrom(job.validFrom != null ? dateKeyFromTs(job.validFrom) : '')
    setScheduleDraftValidUntil(job.validUntil != null ? dateKeyFromTs(job.validUntil) : '')
    resetDraftFromSpec(job.schedule)
    setScheduleModalOpen(true)
  }, [resetDraftFromSpec, activeWorkspaceId])

  /** 从推荐模板进入创建弹窗：名称 / 指令 / 周期预填，用户确认后才真正创建。 */
  const openCreateFromTemplate = useCallback((tpl: TaskTemplate) => {
    setScheduleEditingJobId(null)
    setScheduleDraftName(tpl.name)
    setScheduleDraftInstruction(tpl.instruction)
    setScheduleDraftWorkspaceId(activeWorkspaceId || 'default')
    setScheduleDraftValidFrom('')
    setScheduleDraftValidUntil('')
    resetDraftFromSpec(tpl.schedule)
    setScheduleModalOpen(true)
  }, [resetDraftFromSpec, activeWorkspaceId])

  /** 通过对话创建：新开会话并预填创建任务的指令。 */
  const createViaConversation = useCallback(async () => {
    setNewTaskMenuOpen(false)
    const convId = await createTitledSession('通过对话创建自动化')
    setPendingComposerPrefill({
      text: '我想创建一个自动化任务「每日AI新闻整理」，需要「搜索过去24小时内的AI相关新闻，完成分类与重要性评级后，在界面以结构化方式输出结果」，「每天上午10点」执行，创建后立即启用。',
      nonce: Date.now(),
    })
    setActiveSection('chat')
    await onSelectSession(convId)
  }, [createTitledSession, onSelectSession, setActiveSection, setPendingComposerPrefill])

  const closeScheduleModal = useCallback(() => {
    setScheduleModalOpen(false)
    setScheduleEditingJobId(null)
  }, [])

  /** 预览执行：不落库，直接把当前执行内容带到新会话里试跑。 */
  const previewModalTask = useCallback(async () => {
    const instruction = scheduleDraftInstruction.trim()
    if (!instruction) return
    const name = scheduleDraftName.trim()
    const convId = await createTitledSession(name ? `预览：${name}` : '任务预览')
    setPendingComposerPrefill({ text: instruction, nonce: Date.now() })
    closeScheduleModal()
    setActiveSection('chat')
    toast('已在新会话中预填执行内容，确认后发送即可预览', 'info')
    await onSelectSession(convId)
  }, [
    closeScheduleModal,
    createTitledSession,
    onSelectSession,
    scheduleDraftInstruction,
    scheduleDraftName,
    setActiveSection,
    setPendingComposerPrefill,
    toast,
  ])

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
    // 有效期：只选开始日期时视为单日区间；两者皆空 = 长期有效。
    // 同时传 max_age_days=0，避免后端 30 天默认值把长期任务静默过期。
    const validFromTs = scheduleDraftValidFrom ? startOfDaySec(scheduleDraftValidFrom) : null
    const validUntilTs = scheduleDraftValidUntil
      ? endOfDaySec(scheduleDraftValidUntil)
      : scheduleDraftValidFrom
        ? endOfDaySec(scheduleDraftValidFrom)
        : null

    try {
      const url = isEdit
        ? apiUrl(`/api/scheduled-tasks/${encodeURIComponent(scheduleEditingJobId!)}`)
        : apiUrl('/api/scheduled-tasks')
      const res = await fetch(url, {
        method: isEdit ? 'PATCH' : 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          instruction,
          schedule,
          workspace_id: scheduleDraftWorkspaceId,
          valid_from: validFromTs,
          valid_until: validUntilTs,
          max_age_days: 0,
        }),
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
    scheduleDraftValidFrom,
    scheduleDraftValidUntil,
    scheduleDraftWorkspaceId,
    scheduleEditingJobId,
    selectedScheduleSpec,
    token,
  ])

  return (
    <div className="flex-1 min-w-0 min-h-0 flex flex-col relative app-panel overflow-hidden">
      {/* 页头：定时任务 / 执行记录 切换 + 搜索 + 新建任务（对话创建 / 自定义创建） */}
      <div className="px-6 md:px-[28px] pt-[56px] pb-[20px] shrink-0">
        <div className="max-w-[1200px] mx-auto w-full flex items-start justify-between gap-6">
          <div className="flex items-center gap-[26px] min-w-0 pt-[2px]">
            {([
              ['tasks', '定时任务'],
              ['history', '执行记录'],
            ] as const).map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => setView(key)}
                className={`text-[19px] leading-[26px] transition-colors cursor-pointer ${
                  view === key
                    ? 'font-semibold text-[var(--c-text)]'
                    : 'font-normal text-[color-mix(in_srgb,var(--c-text-secondary)_55%,var(--c-text-tertiary))] hover:text-[var(--c-text)]'
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          <div className="shrink-0 flex items-center gap-[10px] mt-[2px]">
            {view === 'tasks' ? (
              <div className="relative">
                <Search
                  size={14}
                  className="pointer-events-none absolute left-[12px] top-1/2 -translate-y-1/2 text-[var(--c-text-tertiary)]"
                />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="搜索定时任务"
                  className="h-[32px] w-[180px] pl-[32px] pr-[12px] rounded-[var(--radius-control)] border border-[var(--c-border)] bg-[var(--c-surface)]
                   text-[14px] text-[var(--c-text)] placeholder:text-[var(--c-text-tertiary)]
                   hover:border-[var(--c-border-strong)] focus:outline-none focus:border-[var(--c-accent)]
                   focus:ring-1 focus:ring-[var(--c-accent)]/20 transition-colors"
                />
              </div>
            ) : null}

            <div className="relative" data-new-task-menu-root>
              <PrimaryButton
                type="button"
                size="xs"
                className="!px-[16px] !text-[14px]"
                onClick={() => setNewTaskMenuOpen((v) => !v)}
              >
                <span className="inline-flex items-center gap-[6px]">
                  <Plus size={14} strokeWidth={2.2} className="shrink-0" />
                  新建任务
                  <ChevronDown
                    size={14}
                    className={`transition-transform duration-200 ${newTaskMenuOpen ? 'rotate-180' : ''}`}
                  />
                </span>
              </PrimaryButton>

              {newTaskMenuOpen ? (
                <div className="absolute right-0 top-full mt-1.5 z-50">
                  <div className="overlay-panel w-[184px] p-1.5 rounded-[var(--radius-card)] animate-in">
                    <button
                      type="button"
                      onClick={() => void createViaConversation()}
                      className="w-full h-9 flex items-center gap-2.5 px-2.5 rounded-[var(--radius-md)] text-left
                       text-[length:var(--fs-sm)] font-medium text-[var(--c-text)]
                       hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer whitespace-nowrap"
                    >
                      <MessageSquare size={15} className="shrink-0 text-[var(--c-text-secondary)]" />
                      通过对话创建
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setNewTaskMenuOpen(false)
                        openCreateModal()
                      }}
                      className="w-full h-9 flex items-center gap-2.5 px-2.5 rounded-[var(--radius-md)] text-left
                       text-[length:var(--fs-sm)] font-medium text-[var(--c-text)]
                       hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer whitespace-nowrap"
                    >
                      <SquarePen size={15} className="shrink-0 text-[var(--c-text-secondary)]" />
                      自定义创建
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 md:px-[28px] pb-10">
        <div className="max-w-[1200px] mx-auto w-full">
          {view === 'tasks' ? (
            <>
              {displayedJobs.length === 0 ? (
                <EmptyScheduledTasks />
              ) : (
                <>
                  {/* 状态筛选 */}
                  <div className="mb-[16px] flex items-center gap-[10px]">
                    {([
                      ['all', '全部', displayedJobs.length],
                      ['enabled', '已启用', enabledCount],
                      ['paused', '已暂停', displayedJobs.length - enabledCount],
                    ] as const).map(([key, label, count]) => {
                      const active = statusFilter === key
                      return (
                        <button
                          key={key}
                          type="button"
                          onClick={() => setStatusFilter(key)}
                          className={`h-[28px] px-[10px] rounded-[6px] text-[14px] transition-colors cursor-pointer ${
                            active
                              ? 'bg-[var(--c-accent-soft)] text-[var(--c-accent)] font-semibold'
                              : 'text-[var(--c-text-secondary)] font-medium hover:bg-[var(--c-surface-2)] hover:text-[var(--c-text)]'
                          }`}
                        >
                          {label} ({count})
                        </button>
                      )
                    })}
                  </div>

                  {filteredJobs.length === 0 ? (
                    <div className="py-16 text-center text-[length:var(--fs-md)] text-[var(--c-text-secondary)]">
                      当前筛选下暂无任务
                    </div>
                  ) : (
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-[16px]">
                      {filteredJobs.map((job) => (
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
                          className="app-card group flex flex-col h-full px-[16px] pt-[16px] pb-[18px] cursor-pointer transition-colors hover:border-[var(--c-border-strong)] focus:outline-none focus-visible:ring-1 focus-visible:ring-[var(--c-accent)]/30"
                        >
                          <div className="flex items-start justify-between gap-3">
                            <h3 className="min-w-0 text-[14px] leading-[21px] font-semibold text-[var(--c-text)] truncate" title={job.name}>
                              {job.name}
                            </h3>
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation()
                                void setEnabled(job, !job.enabled)
                              }}
                              className={`h-[20px] w-[36px] p-[2px] rounded-full transition-colors cursor-pointer flex items-center shrink-0 ${
                                job.enabled
                                  ? 'bg-[var(--c-accent)]'
                                  : 'bg-[var(--c-border)]'
                              }`}
                              title={job.enabled ? '已开启' : '已关闭'}
                              aria-label={job.enabled ? '已开启' : '已关闭'}
                            >
                              <span
                                className={`h-[16px] w-[16px] rounded-full bg-white shadow-[0_1px_2px_rgba(15,20,32,0.18)] transition-transform ${
                                  job.enabled ? 'translate-x-[16px]' : 'translate-x-0'
                                }`}
                              />
                            </button>
                          </div>

                          {!job.enabled && job.lastStatus === 'disabled' ? (
                            isValidityExpired(job.validUntil) ? (
                              <div className="mt-3 flex items-center gap-2 rounded-[var(--radius-md)] bg-[var(--c-warning-soft)] px-3 py-2 text-[length:var(--fs-sm)] text-[var(--c-warning)]">
                                <Calendar size={14} className="shrink-0" />
                                <span className="flex-1 leading-snug">有效期已结束，任务已自动停用</span>
                                <button
                                  type="button"
                                  onClick={(e) => { e.stopPropagation(); openEditModal(job) }}
                                  className="shrink-0 font-medium underline underline-offset-2 hover:opacity-80 cursor-pointer"
                                >
                                  编辑有效期
                                </button>
                              </div>
                            ) : (
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
                            )
                          ) : null}

                          <p className="mt-[10px] text-[14px] text-[color-mix(in_srgb,var(--c-text-secondary)_60%,var(--c-text-tertiary))] leading-[21px] line-clamp-2">
                            {job.instruction}
                          </p>

                          <div className="relative mt-auto pt-[18px]">
                            <div className="flex items-center gap-[8px] text-[14px] text-[color-mix(in_srgb,var(--c-text-secondary)_60%,var(--c-text-tertiary))]">
                              <Clock size={14} className="shrink-0" />
                              <span className="tabular-nums">{scheduleLabel(job.schedule)}</span>
                              <RunStatusBadge run={liveRuns[job.id]} latest={runsByJob[job.id]?.[0]} />
                            </div>
                            <div className="mt-[10px] flex items-center gap-[8px] pr-[76px] text-[14px] text-[color-mix(in_srgb,var(--c-text-secondary)_60%,var(--c-text-tertiary))]">
                              <RefreshIcon size={14} className="shrink-0" />
                              {job.enabled ? (
                                <>
                                  <span>下次执行</span>
                                  <span className="tabular-nums">{nextRunLabel(job.schedule, new Date(), job.validFrom, job.validUntil) ?? '-'}</span>
                                </>
                              ) : (
                                <span>-</span>
                              )}
                            </div>
                            {job.validFrom != null || job.validUntil != null ? (
                              <div className="mt-[10px] flex items-center gap-[8px] pr-[76px] text-[14px] text-[color-mix(in_srgb,var(--c-text-secondary)_60%,var(--c-text-tertiary))]">
                                <Calendar size={14} className="shrink-0" />
                                <span className="tabular-nums whitespace-nowrap">有效期 {validityLabel(job.validFrom, job.validUntil)}</span>
                              </div>
                            ) : null}

                            {/* 悬停操作：立即执行 / 编辑 / 更多 */}
                            <div
                              className="absolute right-0 bottom-0 flex items-center gap-[2px] pointer-events-none opacity-0 transition-opacity
                               group-hover:pointer-events-auto group-hover:opacity-100
                               group-focus-within:pointer-events-auto group-focus-within:opacity-100"
                              onClick={(e) => e.stopPropagation()}
                            >
                              <button
                                type="button"
                                disabled={Boolean(liveRuns[job.id])}
                                onClick={() => void triggerScheduledRun(job.id)}
                                className="h-[24px] w-[24px] grid place-items-center rounded-[var(--radius-control)] text-[var(--c-text-secondary)] hover:bg-[var(--c-surface-3)] hover:text-[var(--c-text)] transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                                title={liveRuns[job.id] ? '执行中…' : '立即执行'}
                                aria-label="立即执行"
                              >
                                {liveRuns[job.id] ? (
                                  <span className="h-3.5 w-3.5 rounded-full border-2 border-[var(--c-text-secondary)] border-t-transparent animate-spin" />
                                ) : (
                                  <Play size={15} />
                                )}
                              </button>
                              <button
                                type="button"
                                onClick={() => openEditModal(job)}
                                className="h-[24px] w-[24px] grid place-items-center rounded-[var(--radius-control)] text-[var(--c-text-secondary)] hover:bg-[var(--c-surface-3)] hover:text-[var(--c-text)] transition-colors cursor-pointer"
                                title="编辑"
                                aria-label="编辑"
                              >
                                <Pencil size={14} />
                              </button>

                              <div className="relative" data-scheduled-menu-root>
                                <button
                                  type="button"
                                  onClick={() => setScheduledMenuJobId((v) => (v === job.id ? null : job.id))}
                                  data-scheduled-menu-button
                                  className="h-[24px] w-[24px] grid place-items-center rounded-[var(--radius-control)] text-[var(--c-text-secondary)] hover:bg-[var(--c-surface-3)] hover:text-[var(--c-text)] transition-colors cursor-pointer"
                                  aria-label="更多"
                                  title="更多"
                                >
                                  <MoreHorizontal size={16} />
                                </button>

                                {scheduledMenuJobId === job.id ? (
                                  <div className="absolute right-0 bottom-full mb-1 z-50">
                                    <div className="overlay-panel min-w-[148px] py-1 rounded-[var(--radius-card)] overflow-hidden animate-in">
                                      <button
                                        type="button"
                                        onClick={() => {
                                          setScheduledMenuJobId(null)
                                          openEditModal(job)
                                        }}
                                        className="w-full flex items-center gap-2 px-3 py-1.5 rounded-[var(--radius-md)] text-left text-[length:var(--fs-sm)] font-medium text-[var(--c-text)] hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer whitespace-nowrap"
                                      >
                                        <Pencil size={14} className="shrink-0" />
                                        编辑任务
                                      </button>
                                      <button
                                        type="button"
                                        onClick={() => {
                                          setScheduledMenuJobId(null)
                                          void deleteJob(job)
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
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}

              {/* 推荐任务：未搜索时始终展示（空状态也显示），点击卡片打开创建弹窗并预填模板 */}
              {!query.trim() ? (
                <RecommendedTasks onPick={openCreateFromTemplate} />
              ) : null}
            </>
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
            className="overlay-panel w-full max-w-[704px] rounded-[var(--radius-card)] overflow-hidden flex flex-col max-h-[min(720px,calc(100vh-64px))]"
            style={{
              background: 'var(--c-surface)',
              border: '1px solid var(--c-border)',
              backdropFilter: 'none',
              WebkitBackdropFilter: 'none',
            }}
          >
            <div className="px-[20px] pt-[20px] flex items-start justify-between">
              <div className="text-[16px] leading-[24px] font-semibold text-[var(--c-text)]">
                {isEditingJob ? '编辑定时任务' : '新建定时任务'}
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

            <div className="flex-1 overflow-y-auto px-[20px] pt-[16px] pb-[8px] space-y-[18px]">
              <div>
                <div className="mb-[6px] text-[14px] leading-[21px] text-[var(--c-text-secondary)]">
                  任务名称
                </div>
                <input
                  value={scheduleDraftName}
                  onChange={(e) => setScheduleDraftName(e.target.value)}
                  placeholder="请输入"
                  maxLength={50}
                  className="w-full h-[38px] px-[12px] rounded-[6px] bg-[var(--c-surface)] border border-[var(--c-border)]
                             text-[14px] text-[var(--c-text)] placeholder:text-[var(--c-text-tertiary)]
                             hover:border-[var(--c-border-strong)] focus:outline-none focus:border-[var(--c-accent)] transition-colors"
                />
              </div>

              <div>
                <div className="mb-[6px] text-[14px] leading-[21px] text-[var(--c-text-secondary)]">
                  执行内容
                </div>

                {/* 执行内容输入器：正文 + 底部工具行 + 内嵌工作区选择（对齐设计稿） */}
                <div className="rounded-[14px] border border-[var(--c-border)] bg-[var(--c-surface)] overflow-hidden shadow-[0_1px_2px_rgba(31,35,43,0.04)]">
                  <textarea
                    value={scheduleDraftInstruction}
                    onChange={(e) => setScheduleDraftInstruction(e.target.value)}
                    placeholder="请输入"
                    maxLength={3000}
                    rows={5}
                    className="block w-full px-[20px] pt-[16px] pb-[6px] bg-transparent resize-none border-0
                               text-[14px] leading-[24px] text-[var(--c-text)] placeholder:text-[var(--c-text-tertiary)]
                               focus:outline-none"
                  />
                  <div className="flex items-center justify-between px-[16px] pb-[12px]">
                    <span className="h-[28px] w-[28px] grid place-items-center rounded-full border border-[var(--c-border)] text-[var(--c-text-secondary)]">
                      <Plus size={15} />
                    </span>
                    <Mic size={17} className="text-[var(--c-text-secondary)]" />
                  </div>
                  <div className="relative bg-[var(--c-surface-2)] border-t border-[var(--c-border)]/60">
                    <FolderIcon
                      size={16}
                      className="pointer-events-none absolute left-[20px] top-1/2 -translate-y-1/2 text-[var(--c-text)]"
                    />
                    <select
                      value={scheduleDraftWorkspaceId}
                      onChange={(e) => setScheduleDraftWorkspaceId(e.target.value)}
                      className="appearance-none w-full h-[40px] pl-[46px] pr-[36px] bg-transparent cursor-pointer
                                 text-[14px] text-[var(--c-text)] focus:outline-none"
                    >
                      {workspaces.map((ws) => (
                        <option key={ws.id} value={ws.id}>{ws.name}</option>
                      ))}
                    </select>
                    <ChevronDown
                      size={14}
                      className="pointer-events-none absolute right-[16px] top-1/2 -translate-y-1/2 text-[var(--c-text-secondary)]"
                    />
                  </div>
                </div>
              </div>

              <div>
                <div className="mb-[6px] text-[14px] leading-[21px] text-[var(--c-text-secondary)]">
                  计划时间 <span className="text-[var(--c-error)]">*</span>
                </div>
                <div className="flex flex-wrap items-center gap-[10px]">
                  <PillSelect
                    value={scheduleDraftType}
                    onChange={(v) => setScheduleDraftType(v as ScheduleType)}
                    className="w-[88px]"
                  >
                    <option value="minute">每分钟</option>
                    <option value="hour">每小时</option>
                    <option value="day">每天</option>
                    <option value="week">每周</option>
                    <option value="month">每月</option>
                  </PillSelect>

                  {scheduleDraftType === 'month' ? (
                    <PillSelect
                      value={scheduleDraftMonthDay}
                      onChange={(v) => setScheduleDraftMonthDay(v)}
                      icon={<Calendar size={14} />}
                      className="w-[86px]"
                    >
                      {MONTH_DAY_OPTIONS.map((day) => (
                        <option key={day} value={day}>{day} 日</option>
                      ))}
                    </PillSelect>
                  ) : null}

                  {scheduleDraftType === 'minute' ? (
                    <div className="inline-flex h-[34px] items-center gap-[6px] rounded-[6px] border border-[var(--c-border)] bg-[var(--c-surface)] pl-[12px] pr-[12px] text-[14px] text-[var(--c-text)]">
                      <span>每</span>
                      <input
                        value={scheduleDraftEveryMinutes}
                        onChange={(e) =>
                          setScheduleDraftEveryMinutes(e.target.value.replace(/[^\d]/g, '').slice(0, 4))
                        }
                        inputMode="numeric"
                        className="w-[30px] bg-transparent text-center tabular-nums focus:outline-none"
                      />
                      <span>分钟</span>
                    </div>
                  ) : scheduleDraftType === 'hour' ? (
                    <PillSelect
                      value={scheduleDraftHourMinute}
                      onChange={(v) => setScheduleDraftHourMinute(v)}
                      icon={<Clock size={14} />}
                      className="w-[120px]"
                    >
                      {MINUTE_OPTIONS.map((v) => (
                        <option key={v} value={v}>第 {v} 分</option>
                      ))}
                    </PillSelect>
                  ) : (
                    <TimePill
                      hour={scheduleDraftDayHour}
                      minute={scheduleDraftDayMinute}
                      onHour={(v) => setScheduleDraftDayHour(v)}
                      onMinute={(v) => setScheduleDraftDayMinute(v)}
                    />
                  )}

                  <ValidityPicker
                    from={scheduleDraftValidFrom}
                    until={scheduleDraftValidUntil}
                    onChange={(from, until) => {
                      setScheduleDraftValidFrom(from)
                      setScheduleDraftValidUntil(until)
                    }}
                    className="flex-1 min-w-[220px]"
                  />
                </div>

                {scheduleDraftType === 'week' ? (
                  <div className="mt-[8px] flex flex-wrap items-center gap-[6px]">
                    {(Object.entries(WEEKDAY_LABEL) as [ScheduleWeekday, string][]).map(([value, label]) => {
                      const selected = scheduleDraftWeekday === value
                      return (
                        <button
                          key={value}
                          type="button"
                          onClick={() => setScheduleDraftWeekday(value)}
                          className={`h-[34px] w-[54px] rounded-[6px] border text-[14px] transition-colors cursor-pointer ${
                            selected
                              ? 'border-[var(--c-accent)] bg-[var(--c-accent-soft)] text-[var(--c-accent)] font-medium'
                              : 'border-[var(--c-border)] bg-[var(--c-surface)] text-[var(--c-text)] hover:border-[var(--c-border-strong)]'
                          }`}
                        >
                          {label}
                        </button>
                      )
                    })}
                  </div>
                ) : null}
              </div>
            </div>
            <div className="px-[20px] pt-[24px] pb-[20px] flex items-center justify-end gap-[9px]">
              <button
                type="button"
                onClick={() => void previewModalTask()}
                disabled={!scheduleDraftInstruction.trim()}
                className="btn btn-secondary h-[32px] px-[16px] text-[14px]"
              >
                预览执行
              </button>
              <button
                type="button"
                onClick={closeScheduleModal}
                className="btn btn-secondary h-[32px] px-[16px] text-[14px]"
              >
                取消
              </button>
              <PrimaryButton
                type="button"
                onClick={submitModal}
                disabled={!canConfirm}
                className="!h-[32px] !px-[16px] !text-[14px]"
              >
                保存
              </PrimaryButton>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function formatRunTime(ts: number): string {
  const d = new Date(ts * 1000)
  const pad = (v: number) => String(v).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function EmptyScheduledTasks() {
  return (
    <div className="flex flex-col items-center justify-center text-center pt-[88px] pb-[72px]">
      <Clock size={30} strokeWidth={1.5} className="text-[var(--c-text-tertiary)]" />
      <div className="mt-[26px] text-[length:var(--fs-md)] font-semibold text-[var(--c-text)]">还没有定时任务</div>
      <div className="mt-2 text-[length:var(--fs-md)] text-[var(--c-text-tertiary)]">
        新建一个任务，或从模板开始。
      </div>
    </div>
  )
}

function RecommendedTasks({ onPick }: { onPick: (tpl: TaskTemplate) => void }) {
  return (
    <section className="mt-[32px]">
      <div className="mb-[18px] text-[14px] leading-[21px] font-semibold text-[var(--c-text)]">推荐任务</div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-[16px]">
        {TEMPLATES.map((tpl) => (
          <button
            key={tpl.key}
            type="button"
            onClick={() => onPick(tpl)}
            className="app-card app-card--passive flex flex-col h-full px-[16px] pt-[16px] pb-[18px] text-left cursor-pointer
             hover:border-[var(--c-border-strong)] transition-colors
             focus:outline-none focus-visible:ring-1 focus-visible:ring-[var(--c-accent)]/30"
          >
            <h3 className="text-[14px] leading-[21px] font-semibold text-[var(--c-text)] truncate">
              {tpl.name}
            </h3>
            <p className="mt-[12px] text-[14px] leading-[21px] text-[color-mix(in_srgb,var(--c-text-secondary)_60%,var(--c-text-tertiary))] line-clamp-2">
              {tpl.description}
            </p>
            <div className="mt-auto pt-[18px] flex items-center gap-[8px] text-[14px] text-[color-mix(in_srgb,var(--c-text-secondary)_60%,var(--c-text-tertiary))]">
              <Clock size={14} className="shrink-0" />
              <span className="tabular-nums">{tpl.scheduleLabel}</span>
            </div>
          </button>
        ))}
      </div>
    </section>
  )
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

// 原生 <select> 自带的箭头无法跟随容器圆角，统一 appearance-none 隐藏后自绘 ChevronDown。
type PillSelectProps = {
  value: string
  onChange: (next: string) => void
  icon?: React.ReactNode
  className?: string
  children: React.ReactNode
}

function PillSelect({ value, onChange, icon, className, children }: PillSelectProps) {
  return (
    <div
      className={`relative inline-flex h-[34px] items-center rounded-[6px] border border-[var(--c-border)] bg-[var(--c-surface)]
       text-[14px] text-[var(--c-text)] hover:border-[var(--c-border-strong)] focus-within:border-[var(--c-accent)] transition-colors
       ${className ?? ''}`}
    >
      {icon ? (
        <span className="pointer-events-none absolute left-[12px] flex items-center text-[var(--c-text-secondary)]">
          {icon}
        </span>
      ) : null}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`appearance-none w-full h-full bg-transparent rounded-[6px] cursor-pointer focus:outline-none ${
          icon ? 'pl-[34px]' : 'pl-[14px]'
        } pr-[30px]`}
      >
        {children}
      </select>
      <ChevronDown
        size={14}
        className="pointer-events-none absolute right-[10px] text-[var(--c-text-secondary)]"
      />
    </div>
  )
}

type TimePillProps = {
  hour: string
  minute: string
  onHour: (next: string) => void
  onMinute: (next: string) => void
}

/** 时间pill：一个边框内并排小时/分钟两个原生下拉，外观与设计稿的「20:00」一致。 */
function TimePill({ hour, minute, onHour, onMinute }: TimePillProps) {
  return (
    <div className="relative inline-flex h-[34px] w-[113px] items-center rounded-[6px] border border-[var(--c-border)] bg-[var(--c-surface)]
     pl-[12px] pr-[28px] text-[14px] text-[var(--c-text)] hover:border-[var(--c-border-strong)] focus-within:border-[var(--c-accent)] transition-colors">
      <Clock size={14} className="shrink-0 text-[var(--c-text-secondary)]" />
      <select
        value={hour}
        onChange={(e) => onHour(e.target.value)}
        className="ml-[6px] w-[22px] appearance-none bg-transparent text-center tabular-nums cursor-pointer focus:outline-none"
      >
        {HOUR_OPTIONS.map((v) => (
          <option key={v} value={v}>{v}</option>
        ))}
      </select>
      <span className="text-[var(--c-text-secondary)]">:</span>
      <select
        value={minute}
        onChange={(e) => onMinute(e.target.value)}
        className="w-[22px] appearance-none bg-transparent text-center tabular-nums cursor-pointer focus:outline-none"
      >
        {MINUTE_OPTIONS.map((v) => (
          <option key={v} value={v}>{v}</option>
        ))}
      </select>
      <ChevronDown
        size={14}
        className="pointer-events-none absolute right-[10px] text-[var(--c-text-secondary)]"
      />
    </div>
  )
}

// ── 有效期区间选择 ────────────────────────────────────────────────────

/** 某月按周一起始排布的日期键（YYYY-MM-DD），前置空格用 null 占位。 */
function monthCellKeys(year: number, month: number): (string | null)[] {
  const offset = (new Date(year, month, 1).getDay() + 6) % 7
  const total = new Date(year, month + 1, 0).getDate()
  const cells: (string | null)[] = Array<null>(offset).fill(null)
  for (let day = 1; day <= total; day += 1) {
    cells.push(`${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`)
  }
  return cells
}

const CALENDAR_WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日']

type MonthCalendarProps = {
  year: number
  month: number
  from: string
  until: string
  side: 'left' | 'right'
  onShift: (months: number) => void
  onPick: (key: string) => void
}

/** 单月日历：左月显示 « ‹，右月显示 › »，标题居中。 */
function MonthCalendar({ year, month, from, until, side, onShift, onPick }: MonthCalendarProps) {
  const navButton = (label: string, months: number, children: React.ReactNode) => (
    <button
      type="button"
      onClick={() => onShift(months)}
      aria-label={label}
      title={label}
      className="h-[24px] w-[24px] grid place-items-center rounded-[4px] text-[var(--c-text-secondary)] hover:bg-[var(--c-surface-3)] hover:text-[var(--c-text)] transition-colors cursor-pointer"
    >
      {children}
    </button>
  )

  return (
    <div>
      <div className="grid grid-cols-[56px_1fr_56px] items-center px-[12px] pt-[14px] pb-[6px]">
        <div className="flex items-center gap-[2px]">
          {side === 'left' ? (
            <>
              {navButton('上一年', -12, <ChevronsLeft size={16} />)}
              {navButton('上个月', -1, <ChevronLeft size={16} />)}
            </>
          ) : null}
        </div>
        <div className="text-center text-[15px] leading-[22px] font-semibold text-[var(--c-text)] tabular-nums">
          {year} 年 {month + 1} 月
        </div>
        <div className="flex items-center justify-end gap-[2px]">
          {side === 'right' ? (
            <>
              {navButton('下个月', 1, <ChevronRight size={16} />)}
              {navButton('下一年', 12, <ChevronsRight size={16} />)}
            </>
          ) : null}
        </div>
      </div>

      <div className="grid grid-cols-7 px-[10px] text-center text-[13px] text-[var(--c-text-tertiary)]">
        {CALENDAR_WEEKDAYS.map((w) => (
          <div key={w} className="h-[26px] leading-[26px]">
            {w}
          </div>
        ))}
      </div>

      <div className="grid grid-cols-7 gap-y-[2px] px-[10px] pb-[12px]">
        {monthCellKeys(year, month).map((key, index) => {
          if (key === null) return <div key={`blank-${index}`} />
          const selected = key === from || key === until
          const inRange = Boolean(from && until && key > from && key < until)
          return (
            <button
              key={key}
              type="button"
              onClick={() => onPick(key)}
              className={`mx-auto h-[34px] w-full max-w-[36px] rounded-[6px] text-[14px] tabular-nums transition-colors cursor-pointer ${
                selected
                  ? 'bg-[var(--c-accent-soft)] font-semibold text-[var(--c-accent)]'
                  : inRange
                    ? 'bg-[color-mix(in_srgb,var(--c-accent)_6%,transparent)] text-[var(--c-text)]'
                    : key === todayKey()
                      ? 'font-medium text-[var(--c-accent)] hover:bg-[var(--c-surface-3)]'
                      : 'text-[var(--c-text-secondary)] hover:bg-[var(--c-surface-3)] hover:text-[var(--c-text)]'
              }`}
            >
              {Number(key.slice(-2))}
            </button>
          )
        })}
      </div>
    </div>
  )
}

type ValidityPickerProps = {
  from: string
  until: string
  onChange: (from: string, until: string) => void
  className?: string
}

/**
 * 有效期区间选择器：触发器内联在计划时间行，日历浮层走 portal + fixed 定位，
 * 避免被弹窗的 overflow-y-auto 容器裁剪。两值皆空即「长期有效」。
 */
function ValidityPicker({ from, until, onChange, className }: ValidityPickerProps) {
  const [open, setOpen] = useState(false)
  const [viewMonth, setViewMonth] = useState(() => {
    const now = new Date()
    return new Date(now.getFullYear(), now.getMonth(), 1)
  })
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  const updatePosition = useCallback(() => {
    const trigger = triggerRef.current
    if (!trigger) return
    const rect = trigger.getBoundingClientRect()
    const width = Math.min(640, window.innerWidth - 24)
    const height = panelRef.current?.offsetHeight ?? 400
    const left = Math.min(Math.max(12, rect.left), Math.max(12, window.innerWidth - width - 12))
    let top = rect.bottom + 8
    if (top + height > window.innerHeight - 12) top = Math.max(12, rect.top - height - 8)
    setPosition({ left, top })
  }, [])

  useEffect(() => {
    if (!open) {
      setPosition(null)
      return
    }
    updatePosition()
    window.addEventListener('resize', updatePosition)
    // capture：弹窗体滚动也要跟随触发器
    window.addEventListener('scroll', updatePosition, true)
    return () => {
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
    }
  }, [open, updatePosition])

  useEffect(() => {
    if (!open) return
    function onDoc(event: MouseEvent) {
      const target = event.target
      if (!(target instanceof Node)) return
      if (triggerRef.current?.contains(target) || panelRef.current?.contains(target)) return
      setOpen(false)
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const openCalendar = () => {
    const base = from || until
    if (base) {
      const [y, m] = base.split('-').map(Number)
      setViewMonth(new Date(y, m - 1, 1))
    } else {
      const now = new Date()
      setViewMonth(new Date(now.getFullYear(), now.getMonth(), 1))
    }
    setOpen(true)
  }

  const shift = (months: number) =>
    setViewMonth((cur) => new Date(cur.getFullYear(), cur.getMonth() + months, 1))

  const pick = (key: string) => {
    // 未选 / 已完成一段区间 → 重新开始；只选了开始日 → 补齐结束日并自动纠正顺序
    if (!from || (from && until)) {
      onChange(key, '')
      return
    }
    if (key < from) onChange(key, from)
    else onChange(from, key)
  }

  const label = from
    ? until
      ? `${from} ~ ${until}`
      : from
    : '有效期：长期有效'
  const hint = !from ? '先选开始日期' : !until ? '请选择结束日期' : `已选择 ${from} ~ ${until}`
  const rightMonth = new Date(viewMonth.getFullYear(), viewMonth.getMonth() + 1, 1)

  return (
    <div className={`relative ${className ?? ''}`}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => (open ? setOpen(false) : openCalendar())}
        aria-expanded={open}
        className={`flex h-[34px] w-full items-center gap-[8px] rounded-[6px] border px-[12px] text-[14px] transition-colors cursor-pointer ${
          open
            ? 'border-[var(--c-accent)] bg-[var(--c-accent-soft)] text-[var(--c-text)]'
            : 'border-[color-mix(in_srgb,var(--c-accent)_18%,var(--c-border))] bg-[var(--c-accent-soft)] text-[var(--c-text)] hover:border-[color-mix(in_srgb,var(--c-accent)_38%,var(--c-border))]'
        }`}
      >
        <Calendar size={14} className="shrink-0 text-[var(--c-text-secondary)]" />
        <span className="min-w-0 flex-1 truncate text-left tabular-nums">{label}</span>
        <ChevronDown
          size={14}
          className={`shrink-0 text-[var(--c-text-tertiary)] transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open
        ? createPortal(
            <div
              ref={panelRef}
              className="fixed z-[70] overflow-hidden rounded-[14px] border border-[var(--c-border)] bg-[var(--c-surface)] shadow-[var(--shadow-lg)] animate-in"
              style={{
                left: position?.left ?? 0,
                top: position?.top ?? 0,
                width: Math.min(640, window.innerWidth - 24),
                visibility: position ? 'visible' : 'hidden',
              }}
            >
              <div className="grid grid-cols-2">
                <MonthCalendar
                  year={viewMonth.getFullYear()}
                  month={viewMonth.getMonth()}
                  from={from}
                  until={until}
                  side="left"
                  onShift={shift}
                  onPick={pick}
                />
                <div className="border-l border-[var(--c-border)]">
                  <MonthCalendar
                    year={rightMonth.getFullYear()}
                    month={rightMonth.getMonth()}
                    from={from}
                    until={until}
                    side="right"
                    onShift={shift}
                    onPick={pick}
                  />
                </div>
              </div>
              <div className="flex items-center justify-between gap-3 border-t border-[var(--c-border)] px-[16px] py-[10px]">
                <span className="text-[13px] leading-[20px] text-[var(--c-text-tertiary)]">{hint}</span>
                {from || until ? (
                  <button
                    type="button"
                    onClick={() => onChange('', '')}
                    className="shrink-0 text-[13px] font-medium text-[var(--c-accent)] hover:opacity-80 cursor-pointer"
                  >
                    设为长期有效
                  </button>
                ) : null}
              </div>
            </div>,
            document.body,
          )
        : null}
    </div>
  )
}
