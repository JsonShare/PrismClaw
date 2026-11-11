import { Input, Text, Textarea, View } from '@tarojs/components'
import { useEffect, useState } from 'react'

import { useAppStore } from '../../stores/useAppStore'
import Icon from '../Icon'

/**
 * 统一弹框：alert / confirm / danger / form / sheet
 * 规范见 docs/design-system.html「04 弹框与浮层规范」
 */
export default function AppDialog() {
  const dialog = useAppStore((s) => s.dialog)
  const closeDialog = useAppStore((s) => s.closeDialog)
  const [values, setValues] = useState<Record<string, string>>({})
  const [error, setError] = useState('')

  useEffect(() => {
    if (!dialog) return
    const init: Record<string, string> = {}
    ;(dialog.fields || []).forEach((f) => {
      init[f.key] = f.value || (f.options && f.options.length ? f.options[0] : '')
    })
    setValues(init)
    setError('')
  }, [dialog])

  useEffect(() => {
    if (!dialog || typeof window === 'undefined') return
    const onKey = (e: KeyboardEvent) => {
      // 表单/危险确认不允许 Esc 关闭，避免误触丢失输入或误判风险操作
      if (e.key === 'Escape' && dialog.kind !== 'form' && dialog.kind !== 'danger') {
        closeDialog()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [dialog, closeDialog])

  if (!dialog) return null

  const isSheet = dialog.kind === 'sheet'
  const isDanger = dialog.kind === 'danger'
  const confirmText =
    dialog.confirmText ||
    (dialog.kind === 'alert' ? '知道了' : dialog.kind === 'form' ? '保存' : '确定')
  const cancelText = dialog.cancelText || '取消'

  const handleMask = () => {
    // 表单/危险确认禁止点遮罩关闭；提示与普通确认允许点击空白处收起
    if (!isDanger && dialog.kind !== 'form') closeDialog()
  }

  const handleConfirm = () => {
    const fields = dialog.fields || []
    const missing = fields.find((f) => !(values[f.key] || '').trim())
    if (missing) {
      setError(`请填写${missing.label}`)
      return
    }
    closeDialog()
    if (dialog.onConfirm) dialog.onConfirm(values)
  }

  const handleSelect = (value: string) => {
    // 先关闭当前浮层，避免回调中打开的新弹框被立即关闭
    closeDialog()
    if (dialog.onSelect) dialog.onSelect(value)
  }

  const iconName =
    dialog.kind === 'danger' ? 'trash' : dialog.kind === 'form' ? 'message' : 'info'

  return (
    <View className={`dlg-layer is-open${isSheet ? ' is-sheet' : ''}`} onClick={handleMask}>
      {isSheet ? (
        <View className='dlg-sheet' onClick={(e) => e.stopPropagation()}>
          <View className='dlg-grab' />
          <Text className='dlg-sheet-title'>{dialog.title}</Text>
          <View className='dlg-sheet-list'>
            {(dialog.items || []).map((item) => (
              <View
                className={`dlg-sheet-item${item.danger ? ' is-danger' : ''}${item.active ? ' is-active' : ''}`}
                key={item.value}
                onClick={() => handleSelect(item.value)}
              >
                <Text>{item.label}</Text>
              </View>
            ))}
          </View>
          <View className='dlg-sheet-cancel' onClick={closeDialog}>
            <Text>{cancelText}</Text>
          </View>
        </View>
      ) : (
        <View className='dlg-card' onClick={(e) => e.stopPropagation()}>
          <View className={`dlg-mark${isDanger ? ' is-danger' : ''}`}>
            <Icon
              name={iconName}
              size={20}
              color={isDanger ? '#D12224' : '#2C59BC'}
              strokeWidth={1.7}
            />
          </View>
          <Text className='dlg-title'>{dialog.title}</Text>
          {dialog.desc ? <Text className='dlg-desc'>{dialog.desc}</Text> : null}

          {dialog.fields && dialog.fields.length ? (
            <View className='dlg-fields'>
              {dialog.fields.map((f) => (
                <View className='dlg-field' key={f.key}>
                  <Text className='dlg-label'>{f.label}</Text>
                  {f.type === 'select' ? (
                    <View className='dlg-chips'>
                      {(f.options || []).map((opt) => (
                        <View
                          className={`dlg-chip${values[f.key] === opt ? ' is-on' : ''}`}
                          key={opt}
                          onClick={() => setValues((v) => ({ ...v, [f.key]: opt }))}
                        >
                          <Text>{opt}</Text>
                        </View>
                      ))}
                    </View>
                  ) : f.type === 'textarea' ? (
                    <Textarea
                      className='dlg-input dlg-textarea'
                      value={values[f.key] || ''}
                      placeholder={f.placeholder || `请输入${f.label}`}
                      maxlength={3000}
                      onInput={(e) => {
                        setError('')
                        setValues((v) => ({ ...v, [f.key]: e.detail.value }))
                      }}
                    />
                  ) : (
                    <Input
                      className='dlg-input'
                      value={values[f.key] || ''}
                      placeholder={f.placeholder || `请输入${f.label}`}
                      onConfirm={handleConfirm}
                      onInput={(e) => {
                        setError('')
                        setValues((v) => ({ ...v, [f.key]: e.detail.value }))
                      }}
                    />
                  )}
                </View>
              ))}
              {error ? (
                <View className='dlg-error'>
                  <Icon name='alert-triangle' size={12} color='#D12224' />
                  <Text>{error}</Text>
                </View>
              ) : null}
            </View>
          ) : null}

          <View className={`dlg-actions${dialog.kind === 'alert' ? ' is-single' : ''}`}>
            {dialog.kind !== 'alert' ? (
              <View className='dlg-btn' onClick={closeDialog}>
                <Text>{cancelText}</Text>
              </View>
            ) : null}
            <View
              className={`dlg-btn is-primary${isDanger ? ' is-danger' : ''}`}
              onClick={handleConfirm}
            >
              <Text>{confirmText}</Text>
            </View>
          </View>
        </View>
      )}
    </View>
  )
}
