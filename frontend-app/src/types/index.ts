export * from './chat'
export * from './trajectory'

export type Mode = 'chat' | 'work'

export type DialogKind = 'alert' | 'confirm' | 'danger' | 'form' | 'sheet'

export interface DialogField {
  key: string
  label: string
  value: string
  placeholder?: string
  type?: 'text' | 'textarea' | 'select'
  options?: string[]
}

export interface DialogItem {
  label: string
  value: string
  danger?: boolean
  /** sheet 中标记当前选中项 */
  active?: boolean
}

export interface DialogSpec {
  kind: DialogKind
  title: string
  desc?: string
  confirmText?: string
  cancelText?: string
  fields?: DialogField[]
  items?: DialogItem[]
  onConfirm?: (values: Record<string, string>) => void
  onSelect?: (value: string) => void
}
