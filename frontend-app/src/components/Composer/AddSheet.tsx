import { Text, View } from '@tarojs/components'

import Icon from '../Icon'

export type AddAction = 'camera' | 'album' | 'file' | 'library' | 'expert'

interface AddSheetProps {
  open: boolean
  /** 会话内可切换专家；首页无会话时隐藏入口 */
  expertEnabled?: boolean
  onPick: (action: AddAction) => void
  onClose: () => void
}

const ACTIONS: { key: AddAction; icon: string; label: string }[] = [
  { key: 'camera', icon: 'camera', label: '拍摄' },
  { key: 'album', icon: 'image', label: '相册' },
  { key: 'file', icon: 'folder-up', label: '文件' },
]

/** 输入框 ⊕ 的添加面板：拍摄 / 相册 / 文件 + 技能、专家入口 */
export default function AddSheet({ open, expertEnabled = false, onPick, onClose }: AddSheetProps) {
  if (!open) return null

  return (
    <View className='sheet-layer' onClick={onClose}>
      <View className='add-sheet' onClick={(event) => event.stopPropagation()}>
        <View className='add-head'>
          <Text className='add-title'>添加</Text>
          <View className='add-close' onClick={onClose}>
            <Icon name='x' size={22} color='#B0B5BE' strokeWidth={2} />
          </View>
        </View>

        <View className='add-actions'>
          {ACTIONS.map((item) => (
            <View className='add-action' key={item.key} onClick={() => onPick(item.key)}>
              <Icon name={item.icon} size={26} color='#111316' strokeWidth={1.7} />
              <Text className='add-action-label'>{item.label}</Text>
            </View>
          ))}
        </View>

        <View className='add-divider' />

        <View className='add-link' onClick={() => onPick('library')}>
          <Icon name='puzzle' size={24} color='#111316' strokeWidth={1.7} />
          <Text className='add-link-label'>添加技能</Text>
          <Icon name='chevron-right' size={20} color='#B4B9C2' strokeWidth={1.8} />
        </View>

        {expertEnabled ? (
          <View className='add-link' onClick={() => onPick('expert')}>
            <Icon name='user' size={24} color='#111316' strokeWidth={1.7} />
            <Text className='add-link-label'>选择专家</Text>
            <Icon name='chevron-right' size={20} color='#B4B9C2' strokeWidth={1.8} />
          </View>
        ) : null}
      </View>
    </View>
  )
}
