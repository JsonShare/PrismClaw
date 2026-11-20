/**
 * 医学知识库 · 展示辅助
 *
 * 只放与设计稿展示相关的纯函数（相对时间 / 期刊缩写与品牌色 / 芯片去重），
 * 不引入任何数据请求逻辑。
 */

import { useEffect, useState } from 'react'

/** 期刊 → 顶部芯片使用的缩写 */
const JOURNAL_SHORT: Record<string, string> = {
  'N Engl J Med': 'NEJM',
  'JAMA Dermatology': 'JAMA',
  JAMA: 'JAMA',
  BMJ: 'BMJ',
  'The Lancet': 'Lancet',
  Gut: 'Gut',
  'Signal Transduction and Targeted Therapy': 'STTT',
}

/** 期刊 → 徽标品牌色 */
const JOURNAL_COLOR: Record<string, string> = {
  'N Engl J Med': '#B31B1B',
  'JAMA Dermatology': '#8A1538',
  JAMA: '#8A1538',
  BMJ: '#1F5FA9',
  'The Lancet': '#00539B',
  Gut: '#6A1B9A',
  'Signal Transduction and Targeted Therapy': '#1B6E3C',
}

/** 期刊缩写（未知期刊原样返回） */
export function journalShort(journal: string): string {
  return JOURNAL_SHORT[journal] ?? journal
}

/** 期刊徽标颜色（未知期刊用中性灰） */
export function journalColor(journal: string): string {
  return JOURNAL_COLOR[journal] ?? '#6B7280'
}

/** ISO 时间 → 「今天 / N天前 / N个月前 / N年前」 */
export function relativeDays(iso?: string): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const days = Math.floor((Date.now() - t) / 86_400_000)
  if (days <= 0) return '今天'
  if (days < 30) return `${days}天前`
  if (days < 365) return `${Math.floor(days / 30)}个月前`
  return `${Math.floor(days / 365)}年前`
}

/** 输入防抖（列表检索用） */
export function useDebouncedValue<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay)
    return () => window.clearTimeout(timer)
  }, [value, delay])
  return debounced
}

/** 去重并保序 */
export function unique<T>(list: T[]): T[] {
  return Array.from(new Set(list))
}

/** 筛选芯片 */
export interface FilterChip {
  key: string
  label: string
}
