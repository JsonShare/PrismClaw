import type { HTMLAttributes } from 'react'

interface SurfaceProps extends HTMLAttributes<HTMLDivElement> {
  /** default = 主表面（--c-surface）；subtle = 次级表面（--c-surface-2） */
  tone?: 'default' | 'subtle'
  /** 是否带 1px 主题边框（默认 true）。无边框容器传 false */
  bordered?: boolean
}

/**
 * 主题感知表面容器。替代散落的 bg-white / bg-[var(--c-surface)]，
 * 随 data-theme 整体翻转（亮色≈白、暗色≈深色），自带主题文字色。
 */
export function Surface({ className = '', tone = 'default', bordered = true, ...rest }: SurfaceProps) {
  const base = tone === 'subtle' ? 'pc-surface-subtle' : 'pc-surface'
  return <div className={`${base} ${bordered ? '' : 'border-0'} ${className}`} {...rest} />
}
