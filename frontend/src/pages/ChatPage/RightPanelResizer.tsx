import { useEffect, useRef, useState } from 'react'

/** 键盘微调步长（px） */
const RESIZE_STEP = 24

function clampWidth(value: number, min: number, max: number): number {
  return Math.round(Math.min(Math.max(value, min), Math.max(min, max)))
}

interface RightPanelResizerProps {
  /** 右侧面板当前显示宽度，作为拖动起点（px） */
  width: number
  /** 可拖到的最小宽度——再窄内容会变形 */
  min: number
  /** 可拖到的最大宽度（已扣除咨询栏最小可视宽度） */
  max: number
  /**
   * 拖动中实时回调。父级用 CSS 变量命令式更新宽度，
   * 避免整个预览树（PDF / 富文本）每帧重渲染。
   */
  onDrag: (width: number) => void
  /** 松手提交（父级持久化） */
  onCommit: (width: number) => void
  /** 双击 / 回车恢复默认宽度 */
  onReset: () => void
}

/**
 * 咨询栏与预览栏之间的拖拽分隔条。
 *
 * 命中区 12px（与原先 `ml-3` 的视觉间距一致），可见指示条只有 4px 宽、
 * 默认完全透明，悬浮/聚焦才浮现，避免在极简界面上多出一条常驻竖线。
 */
export function RightPanelResizer({
  width,
  min,
  max,
  onDrag,
  onCommit,
  onReset,
}: RightPanelResizerProps) {
  const dragRef = useRef<{ pointerId: number; startX: number; startWidth: number; latest: number } | null>(null)
  const [dragging, setDragging] = useState(false)

  const stopDrag = () => {
    if (typeof document !== 'undefined') {
      document.body.style.userSelect = ''
      document.body.style.cursor = ''
    }
    setDragging(false)
  }

  // 拖动中组件被卸载（关面板 / 切走 section）时，pointerup 不会再来，
  // 必须自己把挂在 body 上的全局样式摘掉，否则整页一直禁用选中、光标卡在 col-resize。
  useEffect(
    () => () => {
      if (typeof document !== 'undefined') {
        document.body.style.userSelect = ''
        document.body.style.cursor = ''
      }
    },
    [],
  )

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="调整预览栏宽度"
      aria-valuenow={width}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      title="拖拽调整宽度，双击恢复默认"
      className="hidden xl:flex w-3 shrink-0 items-center justify-center cursor-col-resize touch-none group focus:outline-none"
      onDoubleClick={onReset}
      onPointerDown={(event) => {
        if (event.button !== 0) return
        dragRef.current = {
          pointerId: event.pointerId,
          startX: event.clientX,
          startWidth: width,
          latest: width,
        }
        setDragging(true)
        event.currentTarget.setPointerCapture(event.pointerId)
        // 拖动期间禁止选中正文、锁定指针样式（移出把手也不复位）
        if (typeof document !== 'undefined') {
          document.body.style.userSelect = 'none'
          document.body.style.cursor = 'col-resize'
        }
        event.preventDefault()
      }}
      onPointerMove={(event) => {
        const drag = dragRef.current
        if (drag === null || drag.pointerId !== event.pointerId) return
        // 指针右移 → 预览栏变窄（面板贴右侧，宽度与位移反向）
        const next = clampWidth(drag.startWidth + drag.startX - event.clientX, min, max)
        drag.latest = next
        onDrag(next)
      }}
      onPointerUp={(event) => {
        const drag = dragRef.current
        if (drag === null || drag.pointerId !== event.pointerId) return
        dragRef.current = null
        stopDrag()
        onCommit(drag.latest)
        event.currentTarget.releasePointerCapture(event.pointerId)
      }}
      onPointerCancel={() => {
        dragRef.current = null
        stopDrag()
      }}
      onKeyDown={(event) => {
        // 左键方向 = 预览栏变宽（把手向左推）
        const delta =
          event.key === 'ArrowLeft' ? RESIZE_STEP : event.key === 'ArrowRight' ? -RESIZE_STEP : 0
        if (delta === 0) {
          if (event.key === 'Enter') onReset()
          return
        }
        onCommit(clampWidth(width + delta, min, max))
        event.preventDefault()
      }}
    >
      <span
        className={`w-1 rounded-full transition-colors duration-150 ${
          dragging
            ? 'h-20 bg-[var(--c-accent)]/55'
            : 'h-14 bg-transparent group-hover:bg-[var(--c-border-strong)] group-focus-visible:bg-[var(--c-border-strong)]'
        }`}
      />
    </div>
  )
}
