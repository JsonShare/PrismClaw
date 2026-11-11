import { useMemo } from 'react'
import { ModalShell } from '../modals/ModalShell'

export type UploadTaskStatus = 'pending' | 'uploading' | 'done' | 'error'

export type UploadTask = {
  id: string
  name: string
  relativePath?: string
  size: number
  status: UploadTaskStatus
  error?: string
}

function calcProgress(tasks: UploadTask[]): number {
  if (tasks.length === 0) return 0
  const done = tasks.filter((t) => t.status === 'done').length
  const uploading = tasks.filter((t) => t.status === 'uploading').length
  const errored = tasks.filter((t) => t.status === 'error').length
  // done/error 视为「已处理」各计 1 份，uploading 计 0.5 份给视觉推进；
  // 关键：error 必须计入，否则只要有 1 个文件失败，进度会卡在 (total-1)/total
  // 永远到不了 100%——例如 20 个文件 1 个失败 = 19/20 = 95% 卡死。
  const counted = done + errored + uploading * 0.5
  return Math.min(100, Math.max(0, Math.round((counted / tasks.length) * 100)))
}

/**
 * 横向进度条：标记位置只显示整体上传进度（0-100%），不显示具体文件信息。
 * 点击可展开弹窗查看总进度与完成统计。
 */
export function UploadTicker({ tasks, onClick }: { tasks: UploadTask[]; onClick: () => void }) {
  const pct = useMemo(() => calcProgress(tasks), [tasks])

  if (tasks.length === 0) return null

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onClick()
        }
      }}
      title="查看上传队列"
      className="w-[110px] shrink-0 cursor-pointer flex items-center gap-2"
    >
      <div className="flex-1 h-2 rounded-full bg-[var(--c-surface-2)] overflow-hidden">
        <div
          className="h-full rounded-full bg-[var(--c-accent)] transition-all duration-300 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)] tabular-nums w-8 text-right">
        {pct}%
      </span>
    </div>
  )
}

export function UploadQueueModal({
  tasks,
  open,
  onClose,
}: {
  tasks: UploadTask[]
  open: boolean
  onClose: () => void
}) {
  const pct = useMemo(() => calcProgress(tasks), [tasks])
  const done = tasks.filter((t) => t.status === 'done').length
  const failed = tasks.filter((t) => t.status === 'error').length
  const total = tasks.length

  return (
    <ModalShell open={open} onClose={onClose} title="上传队列" maxWidth="max-w-sm">
      <div className="px-5 pb-5">
        {total === 0 ? (
          <p className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)] text-center py-8">
            暂无上传任务
          </p>
        ) : (
          <div className="space-y-4">
            <div className="h-2 rounded-full bg-[var(--c-surface-2)] overflow-hidden">
              <div
                className="h-full rounded-full bg-[var(--c-accent)] transition-all duration-300 ease-out"
                style={{ width: `${pct}%` }}
              />
            </div>
            <div className="flex items-center justify-between text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">
              <span>总进度</span>
              <span className="text-[var(--c-text)] font-medium">
                {done}/{total}
              </span>
            </div>
            {failed > 0 && (
              <p className="text-[length:var(--fs-sm)] text-[var(--c-error)]">
                {failed} 个文件上传失败
              </p>
            )}
          </div>
        )}
      </div>
    </ModalShell>
  )
}
