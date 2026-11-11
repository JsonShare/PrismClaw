/**
 * 引文气泡 — 鼠标悬停/点击引文标记时展示来源详情
 *
 * 显示：来源类型图标 + sourceId + 引用原文摘录 + 置信度标签
 */

import { useState, useRef, useEffect, useCallback } from 'react'
import { BookOpen, FileText, Pill, FlaskConical, Sparkles } from 'lucide-react'
import type { CitationItem } from '../../types/view'

interface CitationPopoverProps {
  citation: CitationItem
  /** 触发方式：hover 或 click */
  trigger?: 'hover' | 'click'
  children: React.ReactNode
}

const SOURCE_ICONS: Record<string, React.ReactNode> = {
  report_section: <FileText className="h-3.5 w-3.5" />,
  kb_literature: <BookOpen className="h-3.5 w-3.5" />,
  kb_guideline: <BookOpen className="h-3.5 w-3.5" />,
  kb_drug: <Pill className="h-3.5 w-3.5" />,
  speculative: <Sparkles className="h-3.5 w-3.5" />,
}

const SOURCE_LABELS: Record<string, string> = {
  report_section: '病历',
  kb_literature: '文献',
  kb_guideline: '指南',
  kb_drug: '药品',
  speculative: '探索性',
}

const CONFIDENCE_STYLES: Record<string, string> = {
  high: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300',
  medium: 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
  low: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300',
}

const CONFIDENCE_LABELS: Record<string, string> = {
  high: '高',
  medium: '中',
  low: '低',
}

export function CitationPopover({ citation, trigger = 'hover', children }: CitationPopoverProps) {
  const [open, setOpen] = useState(false)
  const [placement, setPlacement] = useState<'top' | 'bottom'>('top')
  const ref = useRef<HTMLDivElement>(null)

  const show = useCallback(() => setOpen(true), [])
  const hide = useCallback(() => setOpen(false), [])

  // 视口感知定位：打开时检查是否靠近顶部
  useEffect(() => {
    if (!open || !ref.current) return
    const rect = ref.current.getBoundingClientRect()
    // 如果触发元素顶部离视口顶部 < 200px，弹窗向下展开
    setPlacement(rect.top < 200 ? 'bottom' : 'top')
  }, [open])

  useEffect(() => {
    if (!open) return
    const handleClickOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [open])

  const triggerProps = trigger === 'hover'
    ? { onMouseEnter: show, onMouseLeave: hide }
    : { onClick: (e: React.MouseEvent) => { e.stopPropagation(); setOpen(!open) } }

  const popperClass = placement === 'top'
    ? 'bottom-full left-1/2 -translate-x-1/2 mb-2'
    : 'top-full left-1/2 -translate-x-1/2 mt-2'

  return (
    <span className="relative inline" ref={ref} {...triggerProps}>
      {children}
      {open && (
        <div
          className={`absolute z-50 ${popperClass} w-72 rounded-lg border border-[var(--c-border)] bg-[var(--c-surface)] shadow-lg p-3 text-[length:var(--fs-sm)]`}
          onClick={(e) => e.stopPropagation()}
        >
          {/* 来源类型行 */}
          <div className="flex items-center gap-1.5 mb-2">
            <span className="text-[var(--c-text-secondary)]">
              {SOURCE_ICONS[citation.sourceType] ?? <FlaskConical className="h-3.5 w-3.5" />}
            </span>
            <span className="font-medium text-[var(--c-text)]">
              {SOURCE_LABELS[citation.sourceType] ?? citation.sourceType}
            </span>
            <span className="text-[var(--c-text-secondary)] truncate text-[length:var(--fs-xs)]">
              {citation.sourceId}
            </span>
            <span className={`ml-auto px-1.5 py-0.5 rounded text-[length:var(--fs-xs)] font-medium ${CONFIDENCE_STYLES[citation.confidence]}`}>
              {CONFIDENCE_LABELS[citation.confidence]}
            </span>
          </div>
          {/* 引用原文 */}
          {citation.quotedText && (
            <blockquote className="border-l-2 border-[var(--c-accent)] pl-2.5 text-[var(--c-text-secondary)] leading-relaxed">
              {citation.quotedText}
            </blockquote>
          )}
        </div>
      )}
    </span>
  )
}
