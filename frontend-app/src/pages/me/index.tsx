import { ScrollView, Text, View } from '@tarojs/components'

import AppDialog from '../../components/AppDialog'
import AppHeader from '../../components/AppHeader'
import Icon from '../../components/Icon'
import { useThemeClass } from '../../hooks/useThemeClass'
import { useAppStore } from '../../stores/useAppStore'
import { useAuthStore } from '../../stores/useAuthStore'
import { goBack } from '../../utils/toast'
import { THEME_OPTIONS, themeLabel } from '../../utils/theme'
import type { Theme } from '../../utils/theme'

interface ListRowProps {
  icon: string
  title: string
  /** 右侧当前值，例如主题名称 */
  value?: string
  onClick?: () => void
}

function ListRow({ icon, title, value, onClick }: ListRowProps) {
  return (
    <View className='list-row' onClick={onClick}>
      <View className='list-ico'>
        <Icon name={icon} size={20} color='#1F232B' strokeWidth={1.7} />
      </View>
      <Text className='list-title'>{title}</Text>
      {value ? <Text className='list-value'>{value}</Text> : null}
      <View className='list-chev'>
        <Icon name='chevron-right' size={16} color='#A6ADBB' />
      </View>
    </View>
  )
}

export default function Me() {
  const nickname = useAuthStore((s) => s.nickname)
  const userId = useAuthStore((s) => s.userId)
  const logout = useAuthStore((s) => s.logout)
  const theme = useAppStore((s) => s.theme)
  const setTheme = useAppStore((s) => s.setTheme)
  const openDialog = useAppStore((s) => s.openDialog)

  const displayName = nickname || userId || '未登录'
  const initial = displayName.trim().charAt(0).toUpperCase() || '?'

  const pickTheme = () => {
    openDialog({
      kind: 'sheet',
      title: '外观主题',
      items: THEME_OPTIONS.map((option) => ({
        label: option.label,
        value: option.value,
        active: option.value === theme
      })),
      onSelect: (value) => setTheme(value as Theme)
    })
  }

  const showPrivacy = () => {
    openDialog({
      kind: 'alert',
      title: '隐私与权限',
      desc: 'PrismClaw 仅在你的设备与所属服务器之间同步必要数据，用于完成你发起的任务；不会向第三方共享你的内容。',
      confirmText: '我知道了'
    })
  }

  const showAbout = () => {
    openDialog({
      kind: 'alert',
      title: '关于 PrismClaw',
      desc: 'PrismClaw Mobile\n直连你的服务器，随时发起与继续任务。',
      confirmText: '知道了'
    })
  }

  const confirmLogout = () => {
    openDialog({
      kind: 'danger',
      title: '退出当前账号？',
      desc: '退出后将断开与服务器的连接，本地缓存的任务与会话需要重新登录后同步。',
      confirmText: '退出登录',
      onConfirm: () => logout()
    })
  }

  return (
    <View className={useThemeClass('page')}>
      <AppHeader
        variant='me'
        title='我的'
        onBack={() => goBack('/pages/home/index')}
      />

      <ScrollView scrollY className='app-body' style={{ height: 0, flex: 1, minHeight: 0 }}>
        <View className='me-body'>
          <View className='me-identity'>
            <View className='me-avatar'>
              <Text>{initial}</Text>
            </View>
            <View className='me-name-row'>
              <Text className='me-name'>{displayName}</Text>
            </View>
          </View>

          <View className='me-group'>
            <ListRow
              icon='monitor'
              title='外观主题'
              value={themeLabel(theme)}
              onClick={pickTheme}
            />
          </View>

          <View className='me-group'>
            <ListRow icon='lock' title='隐私与权限' onClick={showPrivacy} />
            <ListRow icon='info' title='关于我们' onClick={showAbout} />
          </View>
        </View>
      </ScrollView>

      <View className='me-footer'>
        <View className='me-logout' onClick={confirmLogout}>
          <Text>退出登录</Text>
        </View>
      </View>

      <AppDialog />
    </View>
  )
}
