import { ScrollView, Text, View } from '@tarojs/components'
import { useMemo, useState } from 'react'

import { buildStepRows, buildTimelineSummary } from '../../lib/chat/stepRows'
import type { StepRow } from '../../lib/chat/stepRows'
import type { TimelineNode } from '../../types'
import Icon from '../Icon'

interface ExecutionTimelineProps {
  nodes: TimelineNode[]
}

const STATUS_ICON: Record<string, string> = {
  running: 'clock',
  error: 'alert-triangle',
  skipped: 'ban',
  done: 'check',
}

function rowTitle(row: StepRow): string {
  const label = row.label ?? ''
  return row.text ? `${label} ${row.text}`.trim() : label
}

function SheetRow({ row }: { row: StepRow }) {
  const [open, setOpen] = useState(false)
  const op = row.ops[0]
  const detail = [op?.argsText, op?.output, op?.error].filter(Boolean).join('\n')
  const hasDetail = !!detail || !!row.reasoningFull
  const failed = row.nodeStatus === 'error'
  // 思考行固定用时钟（设计图标准），操作行按执行状态取图标
  const iconName = row.kind === 'reasoning' ? 'clock' : STATUS_ICON[row.nodeStatus] ?? 'check'

  return (
    <View className='exec-item-wrap'>
      <View className='exec-item' onClick={hasDetail ? () => setOpen(!open) : undefined}>
        <Icon
          name={iconName}
          size={16}
          color={failed ? '#D12224' : '#838C9E'}
          strokeWidth={1.8}
        />
        <Text className='exec-item-text'>{rowTitle(row)}</Text>
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

export function ExecutionTimeline({ nodes }: ExecutionTimelineProps) {
  const [open, setOpen] = useState(false)
  const rows = useMemo(() => buildStepRows(nodes), [nodes])
  const summary = useMemo(() => buildTimelineSummary(rows), [rows])

  if (rows.length === 0) return null
  // 有思考行时收敛为固定文案，否则沿用操作摘要（如「读取了 2 个文件」）
  const label = summary.reasoningCount > 0 ? '思考执行' : summary.label

  return (
    <>
      <View className='exec-trigger' onClick={() => setOpen(true)}>
        <Text className='exec-trigger-text'>{label}</Text>
        <Icon name='chevron-right' size={14} color='#A6ADBB' />
      </View>
      {open ? (
        <View className='exec-sheet-mask' onClick={() => setOpen(false)}>
          <View className='exec-sheet' onClick={(event) => event.stopPropagation()}>
            <View className='exec-sheet-handle' />
            <View className='exec-sheet-head'>
              <Text className='exec-sheet-title'>执行详情</Text>
              <View className='exec-sheet-close' onClick={() => setOpen(false)}>
                <Icon name='x' size={18} color='#838C9E' />
              </View>
            </View>
            <ScrollView
              scrollY
              className='exec-sheet-body'
              style={{ maxHeight: '56vh', height: rows.length > 6 ? '56vh' : 'auto' }}
            >
              {rows.map((row) => (
                <SheetRow key={row.key} row={row} />
              ))}
            </ScrollView>
          </View>
        </View>
      ) : null}
    </>
  )
}
