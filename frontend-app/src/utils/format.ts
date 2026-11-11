const MINUTE = 60 * 1000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

function toMs(value: number | string | null | undefined): number {
  if (!value) return 0
  if (typeof value === 'number') return value < 1e12 ? value * 1000 : value
  const parsed = new Date(value).getTime()
  return Number.isFinite(parsed) ? parsed : 0
}

const timeText = (ms: number) => {
  const date = new Date(ms)
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

/** 自然日序号（同一天为 0），避免用固定毫秒差计算「几天前」 */
function dayDiff(ms: number): number {
  const startOfDay = (value: number) => {
    const date = new Date(value)
    date.setHours(0, 0, 0, 0)
    return date.getTime()
  }
  return Math.round((startOfDay(Date.now()) - startOfDay(ms)) / DAY)
}

/** 列表里的相对时间：刚刚 / 5分钟前 / 今天 14:20 / 昨天 / 10-08 */
export function formatRelative(value: number | string | null | undefined): string {
  const ms = toMs(value)
  if (!ms) return ''
  const diff = Date.now() - ms
  if (diff < MINUTE && diff > -MINUTE) return '刚刚'
  if (diff > 0 && diff < HOUR) return `${Math.floor(diff / MINUTE)} 分钟前`
  const days = dayDiff(ms)
  if (days <= 0) return timeText(ms)
  if (days === 1) return '昨天'
  if (days < 7) return `${days} 天前`
  const date = new Date(ms)
  return `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

/** 后端部分时间字段是秒级时间戳，统一换算成毫秒用于排序/展示 */
export function toEpochMs(value: number | null | undefined): number {
  if (!value) return 0
  return value < 1e12 ? value * 1000 : value
}

export function formatSize(bytes: number): string {
  if (!bytes || bytes < 0) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return ''
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))}s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes}m${Math.round(seconds % 60)}s`
}

/** 执行模式 / 状态的中文文案 */
export function statusText(status: string | null | undefined): string {
  switch (status) {
    case 'success':
      return '成功'
    case 'failed':
    case 'error':
      return '失败'
    case 'running':
    case 'executing':
      return '执行中'
    case 'pending':
      return '排队中'
    case 'idle':
      return '待执行'
    case 'skipped':
      return '已跳过'
    case 'aborted':
      return '已取消'
    default:
      return status || '—'
  }
}
