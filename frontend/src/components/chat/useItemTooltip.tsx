import { useCallback, useState } from 'react'
import type { MouseEvent } from 'react'
import { createPortal } from 'react-dom'

export interface ItemTooltipState {
  text: string
  x: number
  y: number
  /** 锚点上方展示（以 translateY(-100%) 顶边对齐锚点） */
  above?: boolean
}

/**
 * 列表项 hover 浮层 tooltip。
 * 通过 createPortal 渲染到 document.body，彻底脱离聊天输入框内含有 transform /
 * filter / backdrop-filter 的祖先容器 —— 否则这些祖先会建立包含块，使 position:fixed
 * 相对祖先而非视口定位，导致浮层整体偏移（即"悬浮 tip 位置错位"问题）。
 * 仅在文本较长（大概率是单行 line-clamp-1 截断）时显示，避免短描述重复弹窗。
 */
export function useItemTooltip(maxLen = 24) {
  const [tip, setTip] = useState<ItemTooltipState | null>(null)

  const show = useCallback(
    (e: MouseEvent<HTMLElement>, text: string, force = false, placement: 'above' | 'below' = 'below') => {
      const t = (text ?? '').trim()
      if (!t) return
      const host = e.currentTarget as HTMLElement
      // 以真实溢出判断是否被 line-clamp 截断；没有可测量元素时退化为长度阈值。
      // 这样面板加宽 / 描述放宽到两行后，未被截断的文本不会再弹冗余浮层。
      const clampEl = host.querySelector<HTMLElement>('[data-clamp-desc]')
      const isTruncated = clampEl
        ? clampEl.scrollHeight > clampEl.clientHeight + 1
        : t.length > maxLen
      // force=true 时跳过截断判断：短标签（如输入栏图标按钮）也始终显示 tooltip
      if (!isTruncated && !force) return
      const r = host.getBoundingClientRect()
      // 估算浮层尺寸，做视口边界约束，避免溢出被裁剪
      const estW = 288 // max-w-[280px] + 8px 余量
      const estH = 140
      // 优先显示在锚点下方（不遮挡相邻的横向卡片/导航箭头）；下方放不下则放上方。
      // placement='above' 用于输入框内的附件卡片：下方就是输入区，必须向上弹。
      const above = placement === 'above' && r.top > 56
      let x = Math.min(r.left, window.innerWidth - estW - 8)
      x = Math.max(8, x)
      let y = r.bottom + 8
      if (above) {
        y = r.top - 8
      } else if (y + estH > window.innerHeight) {
        y = Math.max(8, r.top - estH)
      }
      setTip({ text: t, x, y, above })
    },
    [maxLen],
  )

  const hide = useCallback(() => setTip(null), [])

  const render = tip
    ? createPortal(
        <div
          role="tooltip"
          className="tooltip-bubble fixed z-[60] max-w-[280px] rounded-[var(--radius-md)] px-2.5 py-2 text-[length:var(--fs-sm)] leading-relaxed text-[var(--c-text)]"
          style={{
            left: tip.x,
            top: tip.y,
            pointerEvents: 'none',
            transform: tip.above ? 'translateY(-100%)' : undefined,
          }}
        >
          {tip.text}
        </div>,
        document.body,
      )
    : null

  return { show, hide, render }
}
