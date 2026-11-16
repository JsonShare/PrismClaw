import { Text, View } from '@tarojs/components'
import { useMemo, useState } from 'react'

import { buildStepRows, buildTimelineSummary } from '../../lib/chat/stepRows'
import type { IconType, StepRow } from '../../lib/chat/stepRows'
import type { TimelineNode } from '../../types'
import ExecSheet from '../ExecSheet'
import Icon from '../Icon'

interface ExecutionTimelineProps {
  nodes: TimelineNode[]
  /** 点击「思考执行」：把详情交给页面级浮层渲染，避免被滚动容器裁切 */
  onOpenDetail: (rows: StepRow[], finished: boolean) => void
}

/** 工具类型 → 图标名（对齐 PC 端 StepDetailList.ToolIcon） */
const TOOL_ICON_NAMES: Record<IconType, string> = {
  file_read: 'file-search',
  file_edit: 'file-pen-line',
  web_search: 'globe',
  fetch: 'globe',
  search: 'search',
  command: 'square-terminal',
  execute: 'terminal',
  ask_user: 'help-circle',
  clock: 'clock',
  check: 'check-circle',
  default: 'file-text',
}

/** 行标题前的工具图标：运行中转圈、失败红色叉、其余按工具类型取灰图标 */
function ToolIcon({ iconType, status }: { iconType: IconType; status: string }) {
  if (status === 'running') {
    return (
      <Icon name='loader' size={16} color='#2C59BC' strokeWidth={2} className='exec-icon-spin' />
    )
  }
  if (status === 'error') {
    return <Icon name='circle-x' size={16} color='#D12224' strokeWidth={2} />
  }
  return (
    <Icon name={TOOL_ICON_NAMES[iconType] ?? 'file-text'} size={16} color='#4B5563' strokeWidth={2} />
  )
}

function rowTitle(row: StepRow): string {
  // 思考行优先展示思考原文首行（设计图标准），去掉后端拼接的小标题
  if (row.kind === 'reasoning' && row.reasoningFull) {
    const first = row.reasoningFull
      .split('\n')
      .map((line) => line.trim())
      .find((line) => line.length > 0 && !line.startsWith('【'))
    if (first) return first
  }
  const label = row.label ?? ''
  return row.text ? `${label} ${row.text}`.trim() : label
}

function SheetRow({ row }: { row: StepRow }) {
  const [open, setOpen] = useState(false)
  const op = row.ops[0]
  const detail = [op?.argsText, op?.output, op?.error].filter(Boolean).join('\n')
  const hasDetail = !!detail || !!row.reasoningFull
  // 状态取操作状态（思考行恒为完成态），与 PC 端 ToolIcon 一致
  const status = row.kind === 'reasoning' ? 'done' : (op?.status ?? 'pending')

  return (
    <View className='exec-item-wrap'>
      <View
        className={`exec-item${hasDetail ? ' is-expandable' : ''}`}
        onClick={hasDetail ? () => setOpen(!open) : undefined}
      >
        <ToolIcon iconType={row.iconType} status={status} />
        <Text className='exec-item-text'>{rowTitle(row)}</Text>
        {hasDetail ? (
          <Icon
            name='chevron-down'
            size={13}
            color='#A6ADBB'
            strokeWidth={2}
            className={`exec-item-chevron${open ? ' is-open' : ''}`}
          />
        ) : null}
      </View>
      {open && hasDetail ? (
        <View className='exec-item-detail'>
          {row.answer ? <Text className='exec-detail'>已答：{row.answer}</Text> : null}
          {row.reasoningFull ? <Text className='exec-detail'>{row.reasoningFull}</Text> : null}
          {detail ? <Text className='exec-detail is-output'>{detail}</Text> : null}
        </View>
      ) : null}
    </View>
  )
}

interface ExecutionSheetProps {
  rows: StepRow[]
  finished: boolean
  onClose: () => void
}

/** 执行详情浮层：由页面根节点渲染，保证盖住输入框与全部内容 */
export function ExecutionSheet({ rows, finished, onClose }: ExecutionSheetProps) {
  return (
    <ExecSheet title='执行详情' onClose={onClose}>
      {rows.map((row) => (
        <SheetRow key={row.key} row={row} />
      ))}
      {finished ? (
        <View className='exec-item'>
          <Icon name='check-circle' size={16} color='#4B5563' strokeWidth={2} />
          <Text className='exec-item-text'>执行完成</Text>
        </View>
      ) : null}
    </ExecSheet>
  )
}

export function ExecutionTimeline({ nodes, onOpenDetail }: ExecutionTimelineProps) {
  const rows = useMemo(() => buildStepRows(nodes), [nodes])
  const summary = useMemo(() => buildTimelineSummary(rows), [rows])
  // 全部步骤终态（无运行中/失败）时补一行完成态，对齐设计图的列表收尾
  const finished = rows.length > 0 && !rows.some((row) => row.nodeStatus === 'running' || row.nodeStatus === 'error')

  if (rows.length === 0) return null
  // 有思考行时收敛为固定文案，否则沿用操作摘要（如「读取了 2 个文件」）
  const label = summary.reasoningCount > 0 ? '思考执行' : summary.label

  return (
    <View className='exec-trigger' onClick={() => onOpenDetail(rows, finished)}>
      <Text className='exec-trigger-text'>{label}</Text>
      <Icon name='chevron-right' size={14} color='#A6ADBB' />
    </View>
  )
}
