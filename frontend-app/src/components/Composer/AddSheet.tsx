import { Text, View } from '@tarojs/components'

import Icon from '../Icon'

const ACTIONS = [
  { key: 'camera', icon: 'camera', label: '拍摄' },
  { key: 'album', icon: 'image', label: '相册' },
  { key: 'files', icon: 'folder-up', label: '文件' }
] as const

type ActionKey = (typeof ACTIONS)[number]['key']

interface AddSheetProps {
  open: boolean
  onClose: () => void
  onPick: (kind: ActionKey) => void
  /** 技能与专家共用一个入口，与设计稿一致 */
  onLibrary: () => void
}

/** 输入框「＋」弹层：拍摄 / 相册 / 文件 + 添加技能或专家 */
export default function AddSheet({ open, onClose, onPick, onLibrary }: AddSheetProps) {
  if (!open) return null

  return (
    <View className='sheet-layer' onClick={onClose}>
      <View className='add-sheet' onClick={(event) => event.stopPropagation()}>
        <View className='add-head'>
          <Text className='add-title'>添加</Text>
          <View className='add-close' onClick={onClose}>
            <Icon name='x' size={20} color='#8E8E93' strokeWidth={2} />
          </View>
        </View>

        <View className='add-actions'>
          {ACTIONS.map((action) => (
            <View className='add-action' key={action.key} onClick={() => onPick(action.key)}>
              <Icon name={action.icon} size={21} color='#1F232B' strokeWidth={1.8} />
              <Text className='add-action-label'>{action.label}</Text>
            </View>
          ))}
        </View>

        <View className='add-divider' />

        <View className='add-link' onClick={onLibrary}>
          <Icon name='puzzle' size={20} color='#1F232B' strokeWidth={1.8} />
          <Text className='add-link-label'>添加技能或专家</Text>
          <Icon name='chevron-right' size={16} color='#8A9099' strokeWidth={1.8} />
        </View>
      </View>
    </View>
  )
}
