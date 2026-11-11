/**
 * 进度环 —— 蓝色主题版，对应 CancerDAO "病历已收到/继续补充" 全屏态的视觉骨架
 *
 * 用 SVG 画两个半弧：上半弧深蓝（已完成进度），下半弧浅蓝（剩余）
 * 中间放 ✓ / + / 子内容（由 children 决定）
 */

import type { ReactNode } from 'react'

interface ProgressRingProps {
  /** 0..1，已完成占比（视觉填充比例，仅控制弧长；语义由 children 表达） */
  progress: number
  /** 环内文字/图标 */
  children: ReactNode
  /** 环外直径 px */
  size?: number
  /** 描边粗细 px */
  stroke?: number
  /** 自定义色（默认上半深蓝、下半浅蓝） */
  color?: string
  trackColor?: string
  className?: string
}

export function ProgressRing({
  progress,
  children,
  size = 120,
  stroke = 12,
  color = 'var(--c-accent)',
  trackColor = 'var(--c-accent-soft)',
  className,
}: ProgressRingProps) {
  const r = (size - stroke) / 2
  const cx = size / 2
  const cy = size / 2
  const C = 2 * Math.PI * r
  // 上半弧满 = 进度满；我们用整圈做，但只画 progress 部分为前景色，剩余部分为底色
  const dash = Math.max(0, Math.min(1, progress)) * C

  return (
    <div className={className} style={{ width: size, height: size, position: 'relative' }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        {/* 底色环（整圈） */}
        <circle cx={cx} cy={cy} r={r} fill="none" stroke={trackColor} strokeWidth={stroke} />
        {/* 前景环（按 progress 长度） */}
        <circle
          cx={cx}
          cy={cy}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${dash} ${C - dash}`}
          strokeDashoffset={C * 0.25}
          transform={`rotate(-90 ${cx} ${cy})`}
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center">{children}</div>
    </div>
  )
}