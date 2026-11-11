import { Image } from '@tarojs/components'
import type { CSSProperties } from 'react'

import { ICONS } from '../../constants/icons'
import { useAppStore } from '../../stores/useAppStore'
import { svgDataUri } from '../../utils/svg'

export interface IconProps {
  name: string
  size?: number
  color?: string
  /** 深色主题下的显式色（反色按钮等自动映射不适用的场景） */
  darkColor?: string
  strokeWidth?: number
  style?: CSSProperties
  className?: string
}

const cache: Record<string, string> = {}

/** 深色主题下把亮色语义色映射为对应的高对比值 */
const DARK_COLORS: Record<string, string> = {
  '#17191C': '#E9EDF4',
  '#111114': '#E9EDF4',
  '#1F232B': '#E9EDF4',
  '#575B62': '#ADB1B9',
  '#676D78': '#ADB1B9',
  '#4B5563': '#ADB1B9',
  '#5B6068': '#ADB1B9',
  '#7C8494': '#ADB1B9',
  '#838C9E': '#8B95A8',
  '#8E8E93': '#8B95A8',
  '#9A9AA0': '#8B95A8',
  '#9AA0A8': '#8B95A8',
  '#A6ADBB': '#7B8698',
  '#C6CAD1': '#4A5260',
  '#C7CCD4': '#4A5260',
  '#2C59BC': '#6E9BFF',
  '#15803D': '#3FB950',
  '#B45309': '#D29922',
  '#D12224': '#F85149'
}

function getUri(name: string, color: string, strokeWidth: number): string {
  const key = `${name}|${color}|${strokeWidth}`
  if (cache[key]) return cache[key]
  const path = ICONS[name] || ''
  // 必须带 width/height：缺少内在尺寸时 Taro H5 的 <Image mode="aspectFit">
  // 会把内部 img 按 1/2 缩放居中，导致所有图标变小
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="${color}" ` +
    `stroke-width="${strokeWidth}" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`
  cache[key] = svgDataUri(svg)
  return cache[key]
}

/**
 * 小程序不支持内联 <svg>，统一用 base64 SVG Data URI + <Image> 渲染。
 * 图标路径与原型 app.js 的 ICONS 一一对应。
 */
export default function Icon({
  name,
  size = 20,
  color = '#17191C',
  darkColor,
  strokeWidth = 1.7,
  style,
  className
}: IconProps) {
  const resolved = useAppStore((s) => (s.theme === 'system' ? s.systemTheme : s.theme))
  const actual =
    resolved === 'dark' ? darkColor || DARK_COLORS[color.toUpperCase()] || color : color

  return (
    <Image
      className={`icon${className ? ` ${className}` : ''}`}
      src={getUri(name, actual, strokeWidth)}
      mode="aspectFit"
      style={{ width: `${size}px`, height: `${size}px`, ...style }}
    />
  )
}
