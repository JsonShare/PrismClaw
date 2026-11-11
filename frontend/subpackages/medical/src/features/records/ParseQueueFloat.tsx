/**
 * 全局解析队列浮层（上传队列）
 *
 * 右下角悬浮卡片，两形态（同一浮层）：
 *   迷你条（收起态）→ 面板（仅上传/解析队列）→ 无事可做自动淡出
 *
 * 职责单一：只展示「上传 / 解析」进度与汇总。待归档（待指派）能力
 * 已迁移到病历页（患者列表下方的「待指派」常驻区块），本浮层不再承载。
 *
 * 顶栏按钮语义：— 收起为迷你条；× 本批静默（下次上传才再出现）。
 * 自动隐藏唯一规则：无解析中、无历史任务 → 6s 后淡出。
 *
 * 挂载点：MedicalWorkbenchApp（所有医疗子页面的公共外壳，切页不卸载）。
 * 渲染方式：createPortal → document.body，脱离任何 overflow 容器。
 *
 * 事件协议：
 *   监听 `medical:upload-complete` → 本批解除静默，自动展开解析面板
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { CheckCircle2, X, Minus, ChevronUp } from 'lucide-react'
import { getRecordsQueue } from '../../api/client'
import type { QueueTask, QueueSummary } from '../../types/view'
import FileIcon from '@/components/file/FileIcon'

// ── 常量 ──────────────────────────────────────────────────

const POLL_ACTIVE_MS = 3_000
const POLL_IDLE_MS = 12_000
const FADE_MS = 6_000
const MAX_VISIBLE_TASKS = 20

// ── 阶段映射 ──────────────────────────────────────────────

const STAGE_LABEL: Record<string, string> = {
  ocr: 'OCR 识别',
  classify: '自动分类',
  summarize: '病情总结',
  done: '已完成',
  failed: '解析失败',
  pending: '等待中',
}

// ── 组件 ──────────────────────────────────────────────────

export function ParseQueueFloat() {
  const [tasks, setTasks] = useState<QueueTask[]>([])
  const [summary, setSummary] = useState<QueueSummary>({ doneToday: 0, failed: 0, retried: 0, unassigned: 0 })
  const [visible, setVisible] = useState(false)
  const [collapsed, setCollapsed] = useState(false)

  const mountedRef = useRef(true)
  const pollTimerRef = useRef<number>(0)
  const fadeTimerRef = useRef<number>(0)
  const hasActiveRef = useRef(false)
  const silencedRef = useRef(false)

  // ── 轮询（状态机核心）─────────────────────────────────

  const poll = useCallback(async () => {
    try {
      const res = await getRecordsQueue()
      if (!mountedRef.current) return

      const active = res.tasks.some(t => t.stage !== 'done' && t.stage !== 'failed')

      setTasks(res.tasks)
      setSummary(res.summary)

      clearTimeout(fadeTimerRef.current)
      fadeTimerRef.current = 0

      if (active) {
        // 解析中：常驻（除非本批已静默）
        if (!silencedRef.current) {
          setVisible(true)
        }
      } else if (res.tasks.length > 0) {
        // 全部完成（无论是否还有待归档）→ 6s 后淡出；
        // 待归档由病历页「待指派」常驻区块负责，本浮层只管上传队列。
        fadeTimerRef.current = window.setTimeout(() => {
          if (mountedRef.current) setVisible(false)
        }, FADE_MS)
      } else {
        setVisible(false)
      }
    } catch {
      // 静默
    }
  }, [])

  useEffect(() => { hasActiveRef.current = tasks.some(t => t.stage !== 'done' && t.stage !== 'failed') }, [tasks])

  useEffect(() => {
    mountedRef.current = true
    void poll()
    const scheduleNext = () => {
      const interval = hasActiveRef.current ? POLL_ACTIVE_MS : POLL_IDLE_MS
      pollTimerRef.current = window.setTimeout(() => { void poll().then(scheduleNext) }, interval)
    }
    scheduleNext()
    return () => {
      mountedRef.current = false
      clearTimeout(pollTimerRef.current)
      clearTimeout(fadeTimerRef.current)
    }
  }, [poll])

  useEffect(() => {
    const onUpload = () => {
      silencedRef.current = false
      setVisible(true)
      setCollapsed(false)
      void poll()
    }
    window.addEventListener('medical:upload-complete', onUpload)
    return () => window.removeEventListener('medical:upload-complete', onUpload)
  }, [poll])

  // ── 不渲染 ──────────────────────────────────────────────

  if (!visible) return null

  // ── 派生 ──────────────────────────────────────────────

  const active = tasks.some(t => t.stage !== 'done' && t.stage !== 'failed')
  const doneCount = tasks.filter(t => t.stage === 'done').length
  const failedCount = tasks.filter(t => t.stage === 'failed').length
  const unassignedCount = summary.unassigned ?? 0

  const silence = () => {
    silencedRef.current = true
    setVisible(false)
    setCollapsed(false)
    clearTimeout(fadeTimerRef.current)
  }

  // ── 迷你条（收起态）───────────────────────────────────

  if (collapsed) {
    const miniText = active
      ? `解析中 ${doneCount}/${tasks.length}`
      : `解析完成 ${summary.doneToday} 份`
    return createPortal(
      <div className="mw-pqf-mini" role="button" aria-label="展开解析队列"
        onClick={() => setCollapsed(false)}>
        {active ? (
          <span className="mw-pqf-spinner" />
        ) : (
          <CheckCircle2 className="h-3.5 w-3.5 text-[var(--c-success)] shrink-0" />
        )}
        <span className="mw-pqf-mini-text">{miniText}</span>
        {failedCount > 0 && <span className="mw-pqf-badge-danger">{failedCount}</span>}
        <ChevronUp className="h-3.5 w-3.5 text-[var(--c-text-secondary)] shrink-0" />
      </div>,
      document.body,
    )
  }

  // ── 面板（仅上传/解析队列）──────────────────────────

  const title = active
    ? `正在解析 (${doneCount}/${tasks.length})`
    : `整理完成 ${summary.doneToday} 份`

  return createPortal(
    <div className="mw-pqf" role="status" aria-live="polite">
      {/* 顶栏：标题 + 收起/静默两按钮 */}
      <div className="mw-pqf-header">
        <div className="mw-pqf-header-left">
          {active ? (
            <span className="mw-pqf-spinner" />
          ) : (
            <CheckCircle2 className="h-4 w-4 text-[var(--c-success)] shrink-0" />
          )}
          <span className="mw-pqf-title">{title}</span>
          {failedCount > 0 && (
            <span className="mw-pqf-badge-danger">{failedCount} 失败</span>
          )}
        </div>
        <div className="mw-pqf-header-right">
          <button type="button" className="mw-pqf-icon-btn" onClick={() => setCollapsed(true)} aria-label="收起为迷你条">
            <Minus className="h-3.5 w-3.5" />
          </button>
          <button type="button" className="mw-pqf-icon-btn" onClick={silence} aria-label="本批不再提示">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* 解析队列：三列表格 */}
      <div className="mw-pqf-body">
        <div className="mw-pqf-cols">
          <span />
          <span>文件名</span>
          <span>进度</span>
          <span>状态</span>
        </div>
        <div className="mw-pqf-list">
          {tasks.slice(0, MAX_VISIBLE_TASKS).map(task => (
            <TaskRow key={task.id} task={task} />
          ))}
          {tasks.length > MAX_VISIBLE_TASKS && (
            <div className="mw-pqf-more">还有 {tasks.length - MAX_VISIBLE_TASKS} 份...</div>
          )}
        </div>
        {summary.doneToday > 0 && (
          <div className="mw-pqf-footer">
            今日已完成 {summary.doneToday} 份
            {summary.retried > 0 && ` · ${summary.retried} 份重试`}
            {unassignedCount > 0 && ` · ${unassignedCount} 份待指派`}
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}

// ── 任务行（三列：图标+文件名 | 进度条+百分比 | 状态） ──

function TaskRow({ task }: { task: QueueTask }) {
  const isDone = task.stage === 'done'
  const isFailed = task.stage === 'failed'
  const isWaiting = task.stage === 'pending'
  const label = STAGE_LABEL[task.stage] ?? task.stage
  const statusClass = isDone ? 'done' : isFailed ? 'failed' : isWaiting ? 'wait' : 'active'

  return (
    <div className={`mw-pqf-task${isFailed ? ' mw-pqf-task-failed' : ''}`}>
      <FileIcon filename={task.file} size={16} className="mw-pqf-task-icon" />
      <span className="mw-pqf-task-name" title={task.file}>{task.file}</span>
      {isDone || isFailed ? (
        <span className="mw-pqf-task-progress" />
      ) : (
        <span className="mw-pqf-task-progress">
          <span className="mw-pqf-bar-track">
            <span className="mw-pqf-bar-fill"
              style={{ width: `${Math.min(100, Math.max(0, task.progress))}%` }} />
          </span>
          <span className="mw-pqf-task-percent">{Math.round(task.progress)}%</span>
        </span>
      )}
      <span className={`mw-pqf-task-status st-${statusClass}`}>{label}</span>
    </div>
  )
}
