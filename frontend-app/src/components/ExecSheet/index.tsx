import { ScrollView, Text, View } from '@tarojs/components'
import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import Icon from '../Icon'

interface ExecSheetProps {
  title: string
  onClose: () => void
  children: ReactNode
}

/** 拖到该比例以下松手即关闭 */
const DISMISS_RATIO = 0.4
/** 最大高度占视口比例 */
const MAX_RATIO = 0.9
/** 低于该高度松手时回弹到可用下限 */
const MIN_PX = 120

/**
 * 底部弹出层：顶部横杠可拖动。
 * 上滑变高，下滑变矮；松手时低于视口 40% 则关闭。
 */
export default function ExecSheet({ title, onClose, children }: ExecSheetProps) {
  const sheetRef = useRef<any>(null)
  const handleRef = useRef<any>(null)
  const closeRef = useRef(onClose)
  closeRef.current = onClose

  const dragRef = useRef<{
    startY: number
    startH: number
    lastY: number
    lastT: number
    velocity: number
  } | null>(null)
  const heightRef = useRef<number | null>(null)
  const [height, setHeight] = useState<number | null>(null)
  const [dragging, setDragging] = useState(false)

  useEffect(() => {
    const handle = handleRef.current
    if (!handle?.addEventListener) return

    const removeWindowListeners = () => {
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerup', onPointerUp)
      window.removeEventListener('pointercancel', onPointerUp)
    }

    function onPointerMove(event: PointerEvent) {
      const drag = dragRef.current
      if (!drag) return
      const now = performance.now()
      const dt = Math.max(1, now - drag.lastT)
      drag.velocity = (event.clientY - drag.lastY) / dt
      drag.lastY = event.clientY
      drag.lastT = now

      const next = Math.min(
        window.innerHeight * MAX_RATIO,
        Math.max(0, drag.startH - (event.clientY - drag.startY)),
      )
      heightRef.current = next
      setHeight(next)
    }

    function onPointerUp() {
      const drag = dragRef.current
      dragRef.current = null
      setDragging(false)
      removeWindowListeners()
      if (!drag) return

      const finalH = heightRef.current ?? drag.startH
      const dismiss =
        finalH < window.innerHeight * DISMISS_RATIO ||
        (drag.velocity > 0.5 && finalH < drag.startH * 0.85)
      if (dismiss) {
        closeRef.current()
        return
      }
      if (finalH < MIN_PX) {
        heightRef.current = MIN_PX
        setHeight(MIN_PX)
      }
    }

    function onPointerDown(event: PointerEvent) {
      if (event.pointerType === 'mouse' && event.button !== 0) return
      const sheet = sheetRef.current
      if (!sheet?.getBoundingClientRect) return
      event.preventDefault()

      const startH = sheet.getBoundingClientRect().height
      dragRef.current = {
        startY: event.clientY,
        startH,
        lastY: event.clientY,
        lastT: performance.now(),
        velocity: 0,
      }
      heightRef.current = startH
      setHeight(startH)
      setDragging(true)
      handle.setPointerCapture?.(event.pointerId)
      window.addEventListener('pointermove', onPointerMove)
      window.addEventListener('pointerup', onPointerUp)
      window.addEventListener('pointercancel', onPointerUp)
    }

    handle.addEventListener('pointerdown', onPointerDown)
    return () => {
      handle.removeEventListener('pointerdown', onPointerDown)
      removeWindowListeners()
    }
  }, [])

  return (
    <View className='exec-sheet-mask' onClick={onClose}>
      <View
        ref={sheetRef}
        className={`exec-sheet${dragging ? ' is-dragging' : ''}`}
        style={height !== null ? { height: `${height}px`, minHeight: 0, maxHeight: '90vh' } : undefined}
        onClick={(event) => event.stopPropagation()}
      >
        <View
          ref={handleRef}
          className='exec-sheet-handle'
          onClick={(event) => event.stopPropagation()}
        />
        <View className='exec-sheet-head'>
          <Text className='exec-sheet-title'>{title}</Text>
          <View className='exec-sheet-close' onClick={onClose}>
            <Icon name='x' size={20} color='#9AA1AD' />
          </View>
        </View>
        <ScrollView scrollY className='exec-sheet-body'>
          {children}
        </ScrollView>
      </View>
    </View>
  )
}
