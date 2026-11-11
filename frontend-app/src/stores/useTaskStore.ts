import { create } from 'zustand'

import {
  createScheduledTask,
  deleteScheduledTask,
  listScheduledRuns,
  listScheduledTasks,
  triggerScheduledTask,
  updateScheduledTask,
} from '../services/scheduled'
import type { CreateTaskInput, ScheduledRun, ScheduledTask } from '../services/scheduled'

interface TaskStore {
  tasks: ScheduledTask[]
  loading: boolean
  runs: Record<string, ScheduledRun[]>
  runsLoading: Record<string, boolean>

  load: (workspaceId: string) => Promise<void>
  create: (input: CreateTaskInput) => Promise<ScheduledTask>
  update: (
    taskId: string,
    patch: Partial<Pick<ScheduledTask, 'name' | 'instruction' | 'schedule' | 'enabled' | 'workspace_id'>>,
  ) => Promise<void>
  remove: (taskId: string) => Promise<void>
  toggle: (taskId: string, enabled: boolean) => Promise<void>
  trigger: (taskId: string) => Promise<void>
  loadRuns: (taskId: string) => Promise<void>
}

let loadSeq = 0

export const useTaskStore = create<TaskStore>((set, get) => ({
  tasks: [],
  loading: false,
  runs: {},
  runsLoading: {},

  load: async (workspaceId) => {
    const requestId = ++loadSeq
    set({ loading: true })
    try {
      const tasks = await listScheduledTasks(workspaceId)
      if (requestId === loadSeq) set({ tasks })
    } finally {
      if (requestId === loadSeq) set({ loading: false })
    }
  },

  create: async (input) => {
    const task = await createScheduledTask(input)
    set({ tasks: [task, ...get().tasks] })
    return task
  },

  update: async (taskId, patch) => {
    const updated = await updateScheduledTask(taskId, patch)
    set({ tasks: get().tasks.map((task) => (task.id === taskId ? updated : task)) })
  },

  remove: async (taskId) => {
    await deleteScheduledTask(taskId)
    const runs = { ...get().runs }
    delete runs[taskId]
    set({ tasks: get().tasks.filter((task) => task.id !== taskId), runs })
  },

  toggle: async (taskId, enabled) => {
    set({ tasks: get().tasks.map((task) => (task.id === taskId ? { ...task, enabled } : task)) })
    try {
      const updated = await updateScheduledTask(taskId, { enabled })
      set({ tasks: get().tasks.map((task) => (task.id === taskId ? updated : task)) })
    } catch (error) {
      set({ tasks: get().tasks.map((task) => (task.id === taskId ? { ...task, enabled: !enabled } : task)) })
      throw error
    }
  },

  trigger: async (taskId) => {
    await triggerScheduledTask(taskId)
    void get().loadRuns(taskId)
  },

  loadRuns: async (taskId) => {
    if (get().runsLoading[taskId]) return
    set({ runsLoading: { ...get().runsLoading, [taskId]: true } })
    try {
      const runs = await listScheduledRuns(taskId)
      set({ runs: { ...get().runs, [taskId]: runs } })
    } finally {
      set({ runsLoading: { ...get().runsLoading, [taskId]: false } })
    }
  },
}))
