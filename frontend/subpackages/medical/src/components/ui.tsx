/**
 * 医疗域 · 共享 UI 原语
 *
 * 统一封装新版临床工作台的视觉原子（面板 / 胶囊 / 按钮 / 输入 / 标签 / 迷你走势图），
 * 样式一律走 .mw-* 类 + 主题 token，页面层不再散落颜色值。
 */

import type {
  ButtonHTMLAttributes,
  HTMLAttributes,
  SelectHTMLAttributes,
  ReactNode,
} from 'react'
import { ChevronDown, Search, X, type LucideIcon } from 'lucide-react'
import { clsx } from 'clsx'
import type { Tone } from '../lib/tone'

export type { Tone }

/** className 拼接（内部使用；组件已封装 tone/size/variant，调用方通常不需要自行拼类名） */
function cn(...parts: Array<string | false | null | undefined>): string {
  return clsx(parts)
}

const TONE_CLASS: Record<Tone, string> = {
  accent: 'mw-tone-accent',
  success: 'mw-tone-success',
  warning: 'mw-tone-warning',
  danger: 'mw-tone-danger',
  purple: 'mw-tone-purple',
  neutral: 'mw-tone-neutral',
}

/** tone → .mw-tone-* 类 */
export function toneClass(tone: Tone): string {
  return TONE_CLASS[tone]
}

/* ════════════════════════ 玻璃面板 ════════════════════════ */

interface PanelProps extends HTMLAttributes<HTMLDivElement> {
  /** 是否带 hover 提亮（列表行用） */
  hover?: boolean
  /** 是否被选中（列表行用） */
  active?: boolean
  children?: ReactNode
}

export function Panel({ hover = false, active = false, className, children, ...rest }: PanelProps) {
  return (
    <div
      className={cn(
        'rounded-xl',
        'mw-glass',
        hover && 'mw-glass-hover',
        // 选中态用 .mw-glass-active 换 accent 混色填充。
        // 勿改成 ring-1（外扩 box-shadow）：列表列与 main 左缘齐平，
        // main 的 overflow-y:auto 会把 overflow-x 强制算成 auto，
        // 外扩的 1px 恰好落在裁剪框外 → 左侧标记整段消失（标记2）。
        active && 'mw-glass-active',
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  )
}

/* ════════════════════════ 胶囊 ════════════════════════ */

interface PillProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: Tone
  children?: ReactNode
}

export function Pill({ tone = 'neutral', className, children, ...rest }: PillProps) {
  return (
    <span className={cn('mw-pill', TONE_CLASS[tone], className)} {...rest}>
      {children}
    </span>
  )
}

/* ════════════════════════ 按钮 ════════════════════════ */

interface BtnProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  size?: 'sm' | 'md'
  /** glass = 玻璃次级，dark = 反色主 CTA，primary = 品牌蓝 */
  variant?: 'glass' | 'dark' | 'primary'
  children?: ReactNode
}

export function Btn({ size = 'md', variant = 'glass', className, children, ...rest }: BtnProps) {
  return (
    <button
      type="button"
      className={cn('mw-btn', `mw-btn-${size}`, `mw-btn-${variant}`, className)}
      {...rest}
    >
      {children}
    </button>
  )
}

/* ════════════════════════ 搜索输入 ════════════════════════ */

interface SearchInputProps {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  className?: string
  /** 右侧清空按钮，值非空时显示 */
  clearable?: boolean
}

export function SearchInput({ value, onChange, placeholder, className, clearable = true }: SearchInputProps) {
  return (
    <div className={cn('mw-input', className)}>
      <Search className="h-3.5 w-3.5 shrink-0 text-[var(--c-text-secondary)]" />
      <input
        type="text"
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
      {clearable && value && (
        <button
          type="button"
          onClick={() => onChange('')}
          title="清空"
          className="shrink-0 text-[var(--c-text-secondary)] hover:text-[var(--c-text)] transition-colors cursor-pointer"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  )
}

/* ════════════════════════ 下拉选择 ════════════════════════ */

interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'onChange' | 'value'> {
  value: string
  onChange: (value: string) => void
  options: readonly string[]
  className?: string
}

export function Select({ value, onChange, options, className, ...rest }: SelectProps) {
  return (
    <span className="relative inline-flex items-center">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={cn('mw-select', value === '全部' && 'text-[var(--c-text-secondary)]', className)}
        {...rest}
      >
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
      <ChevronDown className="h-3 w-3 text-[var(--c-text-secondary)] absolute right-2.5 pointer-events-none" />
    </span>
  )
}

/* ════════════════════════ 空状态 ════════════════════════ */

export function EmptyState({
  icon: Icon,
  title,
  hint,
  action,
  className,
}: {
  icon: LucideIcon
  title: string
  hint?: string
  action?: ReactNode
  className?: string
}) {
  return (
    <Panel className={cn('flex flex-col items-center justify-center text-center p-10', className)}>
      <div className="h-12 w-12 rounded-lg bg-[var(--c-surface-3)] flex items-center justify-center">
        <Icon className="h-5 w-5 text-[var(--c-text-secondary)]" />
      </div>
      <p className="mt-3 text-[length:var(--fs-md)] mw-muted">{title}</p>
      {hint && <p className="text-[length:var(--fs-sm)] mw-faint mt-1">{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </Panel>
  )
}

/* ════════════════════════ 其他原子 ════════════════════════ */

/** 等宽数值文本，保证列表纵向对齐 */
export function Num({ className, children, ...rest }: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span className={cn('mw-num', className)} {...rest}>
      {children}
    </span>
  )
}

/** 侧栏/页面小标题（10px 追踪字距） */
export function FieldLabel({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <p className={cn('text-[length:var(--fs-xs)] mw-faint tracking-wider px-2 mb-1.5', className)}>
      {children}
    </p>
  )
}
