import { create } from 'zustand'
import type {
  ActiveMiningSnapshot,
  DomainTaskEventPayload,
  DomainTaskStatus,
  MiningTaskDetail,
} from '../types/tasks'

/** 合法状态四值：帧/快照携带其他值时整条忽略，防脏数据把 store 写花。 */
const VALID_STATUSES: ReadonlySet<string> = new Set(['queued', 'running', 'done', 'failed'])
/** 终态集合：终态条目受单调合并保护（不降级、不回退 updatedAt），且参与 LRU 淘汰。 */
const TERMINAL_STATUSES: ReadonlySet<string> = new Set(['done', 'failed'])
/** 终态 LRU 上限：超过时按 updatedAt 淘汰最旧终态（active/非终态永不淘汰）。 */
const MAX_TERMINAL_TASKS = 20

/** 与 AuthContext / 域 api client 同键：JSON 的 .token 字段。 */
const AUTH_STORAGE_KEY = 'prismclaw.auth.v1'

/**
 * 域快照端点映射：kind（bundle.name）→ REST 快照路径。
 * 各域自行暴露快照端点时在此注册，hydrateFromApi 自动路由。
 * 未注册 kind 的快照由 hydrate 直接注入。
 */
const SNAPSHOT_URLS: Readonly<Record<string, string>> = {}

/** task 命名空间 key：不同域同 id 不冲突。 */
function taskKey(kind: string, id: number): string {
  return `${kind}:${id}`
}

function isDomainTaskStatus(status: string): status is DomainTaskStatus {
  return VALID_STATUSES.has(status)
}

/** 单个域任务（业务域长任务，legal / medical / 自建 agent 等）的 App 级状态。 */
export interface DomainTaskState {
  threadId: number
  kind: string  // 域命名空间：bundle.name（legal / medical / my_agent）
  status: DomainTaskStatus
  title: string | null
  stage: string | null
  progress: number | null
  updatedAt: number
  detail: MiningTaskDetail | null
  detailError: string | null
}

/**
 * 域任务 store（zustand）——业务域长任务的全局状态源。
 *
 * 设计口径：
 *  - 多任务原生：tasks 是以 threadId 为键的 Record，3 个并发任务各占一槽、
 *    互不覆盖，各自独立做单调合并（旧 MiningPage 的组件内单任务状态无法表达并发）。
 *  - App 级存活：store 挂在 App 层，视图可随路由卸载重建而任务
 *    状态不丢——同 chat.ts convStates「视图是投影」的先例。
 *  - 零轮询：状态由 domain_task.updated WS 帧实时驱动；断线期间错过的帧由
 *    连接就绪时的一次 REST 快照（hydrateFromApi）补齐，组件不再自建 setInterval。
 *
 * 单调合并：帧与快照的到达顺序无保证（重连补快照常晚于迟到帧），因此所有写入
 * 走同一条 mergeTaskUpdate 纯函数——已终态条目不降级、终态不回退 updatedAt。
 * 帧只承载状态语义（status/stage/progress），全量详情由视图在终态/聚焦时经
 * GET /{id} 拉取写入 detail（setDetail）；帧内不塞详情，避免双真相源。
 */

/** mergeTaskUpdate 的输入：快照与帧归一化后的最小更新集。 */
interface MergedUpdate {
  kind: string
  threadId: number
  status: DomainTaskStatus
  stage: string | null
  progress: number | null
  updatedAt: number
  /**
   * title 仅快照携带（帧不带）：undefined 表示本更新不触碰 title；
   * 非 undefined 时仅在既有 title 缺失时填充——快照是 title 的唯一来源，
   * 先到者为准，避免后到的旧快照覆盖已展示的标题。
   */
  title?: string | null
}

/**
 * 单条条目的单调合并（快照 hydrate 与帧 applyEventFrame 共用，保证两路写入
 * 同一规则、乱序安全）：
 *  - 已存在条目为终态且新更新非终态 → 忽略（乱序旧帧不得把终态降级）；
 *  - 已存在条目为终态且新更新也终态 → 仅当新 updatedAt >= 既有才应用；
 *  - 其余（无条目 / 非终态）→ 应用；
 *  - 应用时更新 status/stage/progress/updatedAt，保留 kind/detail/detailError，
 *    title 按 MergedUpdate.title 的规则处理。
 * 返回 null 表示忽略该更新。
 */
function mergeTaskUpdate(
  existing: DomainTaskState | undefined,
  update: MergedUpdate,
): DomainTaskState | null {
  if (existing) {
    const existingTerminal = TERMINAL_STATUSES.has(existing.status)
    const incomingTerminal = TERMINAL_STATUSES.has(update.status)
    if (existingTerminal && !incomingTerminal) return null
    if (existingTerminal && incomingTerminal && update.updatedAt < existing.updatedAt) {
      return null
    }
  }

  const base: DomainTaskState = existing ?? {
    threadId: update.threadId,
    kind: update.kind,
    status: update.status,
    title: null,
    stage: null,
    progress: null,
    updatedAt: update.updatedAt,
    detail: null,
    detailError: null,
  }
  return {
    ...base,
    status: update.status,
    stage: update.stage,
    progress: update.progress,
    updatedAt: update.updatedAt,
    title: base.title ?? (update.title ?? null),
  }
}

/** updatedAt 解析：iso 有效 → Date.parse(ms)；null/NaN → Date.now()（接收时刻兜底）。 */
function parseUpdatedAt(iso: string | null | undefined): number {
  if (iso) {
    const ms = Date.parse(iso)
    if (!Number.isNaN(ms)) return ms
  }
  return Date.now()
}

/**
 * 终态 LRU（纯函数）：终态条目超过 MAX_TERMINAL_TASKS 时按 updatedAt 淘汰最旧的，
 * 活跃/非终态条目永不淘汰。未超限时原样返回引用（零成本，可在每次 tasks 变更后
 * 无条件调用）。
 */
export function pruneTerminal(
  tasks: Record<string, DomainTaskState>,
): Record<string, DomainTaskState> {
  const terminalEntries = Object.entries(tasks).filter(([, t]) => TERMINAL_STATUSES.has(t.status))
  if (terminalEntries.length <= MAX_TERMINAL_TASKS) return tasks
  terminalEntries.sort((a, b) => a[1].updatedAt - b[1].updatedAt)
  const next = { ...tasks }
  for (const [id] of terminalEntries.slice(0, terminalEntries.length - MAX_TERMINAL_TASKS)) {
    delete next[id]
  }
  return next
}

interface TaskState {
  tasks: Record<string, DomainTaskState>
}

interface TaskActions {
  /** 应用 GET /{kind}/tasks/active 快照：遍历 active + recent_terminal 逐条走单调合并（幂等）。 */
  hydrate: (kind: string, snapshot: ActiveMiningSnapshot) => void
  /** 拉取快照并对齐；kind 决定路由；token 缺省时从 localStorage 读取。 */
  hydrateFromApi: (kind: string, token?: string | null) => Promise<void>
  /** 应用一帧 domain_task.updated：payload.kind 决定域命名空间。 */
  applyEventFrame: (payload: DomainTaskEventPayload) => void
  /** 写入全量详情（GET /{id} 成功）并清除 detailError。 */
  setDetail: (kind: string, threadId: number, detail: MiningTaskDetail) => void
  /** 只写 detailError，不动 status——传输错误永不改变任务语义（G3）。 */
  setDetailError: (kind: string, threadId: number, message: string) => void
}

export type DomainTaskStore = TaskState & TaskActions

export const useTaskStore = create<DomainTaskStore>((set, get) => ({
  tasks: {},

  hydrate: (kind, snapshot) =>
    set((state) => {
      let tasks = state.tasks
      let changed = false
      for (const item of [...(snapshot.active ?? []), ...(snapshot.recent_terminal ?? [])]) {
        if (!isDomainTaskStatus(item.status)) continue
        const key = taskKey(kind, item.thread_id)
        const merged = mergeTaskUpdate(tasks[key], {
          kind,
          threadId: item.thread_id,
          status: item.status,
          stage: item.stage ?? null,
          progress: item.progress ?? null,
          updatedAt: parseUpdatedAt(item.updated_at),
          title: item.title ?? null,
        })
        if (merged === null) continue
        tasks = { ...tasks, [key]: merged }
        changed = true
      }
      if (!changed) return state
      return { tasks: pruneTerminal(tasks) }
    }),

  hydrateFromApi: async (kind, token) => {
    const url = SNAPSHOT_URLS[kind]
    if (!url) return  // 未注册快照端点的域，由 hydrate 直接注入

    let bearer = token ?? null
    if (!bearer) {
      try {
        const raw = localStorage.getItem(AUTH_STORAGE_KEY)
        if (raw) {
          const parsed = JSON.parse(raw)
          if (typeof parsed?.token === 'string') bearer = parsed.token
        }
      } catch { /* ignore */ }
    }
    if (!bearer) return
    try {
      const res = await fetch(url, {
        headers: { Authorization: `Bearer ${bearer}` },
      })
      if (!res.ok) return
      const data = (await res.json()) as ActiveMiningSnapshot
      get().hydrate(kind, data)
    } catch { /* ignore */ }
  },

  applyEventFrame: (payload) =>
    set((state) => {
      if (!isDomainTaskStatus(payload.status)) return state
      const key = taskKey(payload.kind, payload.task_id)
      const merged = mergeTaskUpdate(state.tasks[key], {
        kind: payload.kind,
        threadId: payload.task_id,
        status: payload.status,
        stage: payload.stage,
        progress: payload.progress,
        updatedAt: parseUpdatedAt(payload.updated_at),
      })
      if (merged === null) return state
      return { tasks: pruneTerminal({ ...state.tasks, [key]: merged }) }
    }),

  setDetail: (kind, threadId, detail) =>
    set((state) => {
      const key = taskKey(kind, threadId)
      const task = state.tasks[key]
      if (!task) return state
      return {
        tasks: {
          ...state.tasks,
          [key]: { ...task, detail, detailError: null },
        },
      }
    }),

  setDetailError: (kind, threadId, message) =>
    set((state) => {
      const key = taskKey(kind, threadId)
      const task = state.tasks[key]
      if (!task) return state
      return {
        tasks: {
          ...state.tasks,
          [key]: { ...task, detailError: message },
        },
      }
    }),
}))
