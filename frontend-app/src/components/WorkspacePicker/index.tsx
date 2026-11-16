import { Text, View } from '@tarojs/components'

import { useAppStore } from '../../stores/useAppStore'
import Icon from '../Icon'

/** 工作区切换（与 ModePicker 同风格：图标 + 文案 + 箭头） */
export default function WorkspacePicker() {
  const workspaceId = useAppStore((s) => s.workspaceId)
  const workspaces = useAppStore((s) => s.workspaces)
  const setWorkspaceId = useAppStore((s) => s.setWorkspaceId)
  const openDialog = useAppStore((s) => s.openDialog)

  const current = workspaces.find((w) => w.id === workspaceId)
  const label = current?.name || '选择工作区'

  const pickWorkspace = () => {
    openDialog({
      kind: 'sheet',
      title: '选择工作区',
      items: workspaces.map((w) => ({
        label: w.name,
        value: w.id,
        active: w.id === workspaceId,
      })),
      onSelect: (value) => setWorkspaceId(value),
    })
  }

  return (
    <View className='workspace-btn' onClick={pickWorkspace}>
      <Icon name='folder' size={14} color='#838C9E' strokeWidth={1.8} />
      <Text className='workspace-btn-text'>{label}</Text>
      <Icon name='chevron-down' size={13} color='#838C9E' />
    </View>
  )
}
