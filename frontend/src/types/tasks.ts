/**
 * 域任务（业务域长任务框架）共享类型。
 *
 * 帧协议见后端 prismclaw/schema/services/domain_task_events.py（adapter）
 * + app/web/websocket/domain_task_emitter.py（实现）；
 * 快照协议见各域自行暴露的 REST 端点。
 * 设计口径：帧只承载状态语义，全量详情由视图在终态转换 / 聚焦时经
 * GET /{id} 拉取并缓存进 store —— 避免帧内塞详情形成双真相源。
 */

export type DomainTaskStatus = 'queued' | 'running' | 'done' | 'failed'

/** /active 快照列表项 */
export interface DomainTaskSnapshot {
  thread_id: number
  title?: string | null
  status: string
  stage?: string | null
  progress?: number | null
  updated_at?: string | null
}

/** GET /active 响应 */
export interface ActiveMiningSnapshot {
  active: DomainTaskSnapshot[]
  recent_terminal: DomainTaskSnapshot[]
}

/** domain_task.updated 帧的 data */
export interface DomainTaskEventPayload {
  task_id: number
  kind: string  // 域命名空间：bundle.name（legal / medical / 自建 agent）
  workspace_id: string
  status: string
  stage: string | null
  progress: number | null
  updated_at: string | null
}

/** 域任务 GET /{id} 全量详情缓存（消费方视图自行窄化） */
export interface MiningTaskDetail {
  thread_id: number
  status?: string
  stage?: string
  progress?: number
  stages?: Record<string, unknown>
  error?: string | null
  thread?: Record<string, unknown>
  candidates?: unknown[]
  count?: number
  [key: string]: unknown
}
