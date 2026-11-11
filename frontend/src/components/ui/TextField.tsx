import type { InputHTMLAttributes } from 'react'

interface TextFieldProps extends InputHTMLAttributes<HTMLInputElement> {}

/** 表单文本输入：唯一的输入框原语（登录页与后续表单共用）。
    颜色与聚焦环由 .field-input 在主题层驱动，调用点只传布局类。 */
export function TextField({ className = '', ...rest }: TextFieldProps) {
  return <input className={`field-input ${className}`} {...rest} />
}
