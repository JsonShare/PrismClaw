import { Text, View } from '@tarojs/components'
import { useEffect, useMemo, useRef, useState } from 'react'

import { buildStepRows } from '../../lib/chat/stepRows'
import type { TimelineNode } from '../../types'
import { ThinkingIndicator } from '../ThinkingIndicator'

interface LiveProgressProps {
  /** 会话是否处于运行中 */
  active: boolean
  nodes: TimelineNode[]
  /** 是否已开始输出正文 */
  hasText: boolean
  queued: number | null
  clarifying: boolean
  ready: boolean
}

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

/**
 * 会话运行态的外层进度条：常驻在消息流上方，
 * 滚动到任何位置都能看到「当前在做什么 + 已用时」，不用等折叠区里的思考块。
 */
export default function LiveProgress({
  active,
  nodes,
  hasText,
  queued,
  clarifying,
  ready,
}: LiveProgressProps) {
  const [elapsed, setElapsed] = useState(0)
  const startedAtRef = useRef(0)

  useEffect(() => {
    if (!active) {
      startedAtRef.current = 0
      setElapsed(0)
      return
    }
    if (!startedAtRef.current) startedAtRef.current = Date.now()
    const tick = () => setElapsed(Math.floor((Date.now() - startedAtRef.current) / 1000))
    tick()
    const timer = setInterval(tick, 1000)
    return () => clearInterval(timer)
  }, [active])

  // 正在执行的那一步（工具名 + 目标）；节点不变时不重复构建
  const runningStep = useMemo(() => {
    const row = buildStepRows(nodes)
      .filter((item) => item.kind === 'operation' && item.nodeStatus === 'running')
      .pop()
    if (!row) return ''
    return row.text ? `${row.label} · ${row.text}` : row.label
  }, [nodes])

  const label = clarifying
    ? '等待你的确认'
    : queued
      ? `排队中，前面还有 ${queued} 个任务`
      : runningStep || (hasText ? '正在生成回答…' : ready ? '正在思考…' : '准备中…')

  if (!active) return null

  return (
    <View className='live-progress-dock'>
      <View className='live-progress'>
        <View className='live-progress-main'>
          <ThinkingIndicator text={label} />
          <Text className='live-progress-time'>{formatElapsed(elapsed)}</Text>
        </View>
        <View className='live-progress-bar'>
          <View className='live-progress-bar-fill' />
        </View>
      </View>
    </View>
  )
}
