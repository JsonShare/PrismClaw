import { Image, Text, View } from '@tarojs/components'

import chatIcon from '../../assets/other_icon/chat.png'
import workIcon from '../../assets/other_icon/work.png'
import { useAppStore } from '../../stores/useAppStore'
import Icon from '../Icon'

/** Chat / Work 模式切换（PC ModeToggle 的移动端形态：图标 + 文案 + 箭头） */
export default function ModePicker() {
  const mode = useAppStore((s) => s.mode)
  const setMode = useAppStore((s) => s.setMode)
  const openDialog = useAppStore((s) => s.openDialog)

  const pickMode = () => {
    openDialog({
      kind: 'sheet',
      title: '选择执行模式',
      items: [
        { label: 'chat 咨询', value: 'chat', active: mode === 'chat' },
        { label: 'work 工作', value: 'work', active: mode === 'work' },
      ],
      onSelect: (value) => setMode(value === 'work' ? 'work' : 'chat'),
    })
  }

  return (
    <View className='mode-btn' onClick={pickMode}>
      <Image
        className='mode-ico-img'
        src={mode === 'work' ? workIcon : chatIcon}
        mode='aspectFit'
      />
      <Text className='mode-btn-label'>{mode === 'work' ? 'work 工作' : 'chat 咨询'}</Text>
      <Icon name='chevron-down' size={13} color='#838C9E' />
    </View>
  )
}
