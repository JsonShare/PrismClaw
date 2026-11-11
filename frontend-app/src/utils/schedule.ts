import type { ScheduleSpec } from '../services/scheduled'

export const WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const

export const WEEKDAY_LABEL: Record<string, string> = {
  mon: '一',
  tue: '二',
  wed: '三',
  thu: '四',
  fri: '五',
  sat: '六',
  sun: '日',
}

const pad2 = (value: number | string) => String(value).padStart(2, '0')

export function scheduleLabel(spec: ScheduleSpec): string {
  if (!spec) return '—'
  switch (spec.type) {
    case 'minute':
      return `每 ${spec.everyMinutes} 分钟`
    case 'hour':
      return `每小时第 ${pad2(spec.minute)} 分`
    case 'day':
      return `每天 ${pad2(spec.hour)}:${pad2(spec.minute)}`
    case 'week':
      return `每周${WEEKDAY_LABEL[spec.weekday] ?? ''} ${pad2(spec.hour)}:${pad2(spec.minute)}`
    case 'month':
      return `每月 ${spec.day} 日 ${pad2(spec.hour)}:${pad2(spec.minute)}`
    case 'raw':
      return `按 Cron：${spec.cron}`
    default:
      return '—'
  }
}
