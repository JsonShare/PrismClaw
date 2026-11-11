import type { ButtonHTMLAttributes } from 'react'

type PrimaryButtonSize = 'sm' | 'md' | 'lg'

interface PrimaryButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** 尺寸档：sm 28 / md 36 / lg 40。默认 md（表单提交、对话框页脚）。 */
  size?: PrimaryButtonSize
}

/**
 * 主操作按钮：全站唯一的主要动作原语。
 * 尺寸 / 内边距 / 圆角 / 字重 / 禁用态全部由 .btn 尺寸档与 .btn-primary 变体给出，
 * 调用点只传业务类与 onClick——不再传 h-* / px-* / rounded-*，那是尺寸失控的根源。
 */
export function PrimaryButton({
  size = 'md',
  type = 'button',
  className = '',
  children,
  ...rest
}: PrimaryButtonProps) {
  return (
    <button type={type} className={`btn btn-${size} btn-primary ${className}`} {...rest}>
      {children}
    </button>
  )
}
