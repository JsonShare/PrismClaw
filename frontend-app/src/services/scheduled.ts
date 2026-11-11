import { http } from './request'

export type ScheduleType = 'minute' | 'hour' | 'day' | 'week' | 'month'

export type ScheduleSpec =
  | { type: 'minute'; everyMinutes: number | string }
  | { type: 'hour'; minute: number | string }
  | { type: 'day'; hour: number | string; minute: number | string }
  | { type: 'week'; weekday: string; hour: number | string; minute: number | string }
  | { type: 'month'; day: number | string; hour: number | string; minute: number | string }
  /** 后端 cron_to_spec 无法识别的历史计划，只保留原始 cron 展示 */
  | { type: 'raw'; cron: string }

export interface ScheduledTask {
  id: string
  name: string
  instruction: string
  schedule: ScheduleSpec
  cron_expr: string
  workspace_id: string
  enabled: boolean
  last_fired_at: number | null
  last_status: string
  consecutive_failures: number
  max_consecutive_failures: number
  conversation_id: string | null
  created_at: number
  max_age_days: number
  missed_policy: string
}

export interface ScheduledRun {
  id: string
  task_id: string
  fired_at: number
  started_at: number | null
  finished_at: number | null
  status: string
  session_id: string | null
  error: string | null
  duration_s: number | null
}

export interface CreateTaskInput {
  name: string
  instruction: string
  schedule: ScheduleSpec
  workspace_id?: string
}

export async function listScheduledTasks(workspaceId?: string): Promise<ScheduledTask[]> {
  const res = await http.get<ScheduledTask[]>('/api/scheduled-tasks', {
    workspace_id: workspaceId && workspaceId !== 'all' ? workspaceId : undefined,
  })
  return Array.isArray(res) ? res : []
}

export async function createScheduledTask(input: CreateTaskInput): Promise<ScheduledTask> {
  return http.post<ScheduledTask>('/api/scheduled-tasks', input)
}

export async function updateScheduledTask(
  taskId: string,
  patch: Partial<Pick<ScheduledTask, 'name' | 'instruction' | 'schedule' | 'enabled' | 'workspace_id'>>,
): Promise<ScheduledTask> {
  return http.patch<ScheduledTask>(`/api/scheduled-tasks/${encodeURIComponent(taskId)}`, patch)
}

export async function deleteScheduledTask(taskId: string): Promise<void> {
  await http.delete(`/api/scheduled-tasks/${encodeURIComponent(taskId)}`)
}

export async function triggerScheduledTask(taskId: string): Promise<{ run_id: string; status: string }> {
  return http.post<{ run_id: string; status: string }>(
    `/api/scheduled-tasks/${encodeURIComponent(taskId)}/trigger`,
  )
}

export async function listScheduledRuns(taskId: string, limit = 20, offset = 0): Promise<ScheduledRun[]> {
  const res = await http.get<ScheduledRun[]>(`/api/scheduled-tasks/${encodeURIComponent(taskId)}/runs`, {
    limit,
    offset,
  })
  return Array.isArray(res) ? res : []
}
