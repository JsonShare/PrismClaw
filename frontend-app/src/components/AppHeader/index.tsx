import { Text, View } from '@tarojs/components'

import Icon from '../Icon'

export type HeaderVariant = 'home' | 'chat' | 'scheduled' | 'me' | 'settings' | 'automations'

export interface AppHeaderProps {
  variant: HeaderVariant
  title?: string
  sub?: string
  onMenu?: () => void
  onBack?: () => void
  onMore?: () => void
  onNew?: () => void
  onRefresh?: () => void
}

export default function AppHeader({
  variant,
  title,
  sub,
  onMenu,
  onBack,
  onMore,
  onNew,
  onRefresh
}: AppHeaderProps) {
  if (variant === 'home') {
    return (
      <View className='app-header is-plain'>
        <View className='icon-btn' onClick={onMenu}>
          <Icon name='menu' size={20} />
        </View>
        <View className='header-mid' />
      </View>
    )
  }

  if (variant === 'automations') {
    return (
      <View className='app-header'>
        <View className='icon-btn' onClick={onBack}>
          <Icon name='chevron-left' size={20} />
        </View>
        <View className='header-mid'>
          <Text className='header-title'>{title}</Text>
        </View>
        <View className='header-actions'>
          {onRefresh ? (
            <View className='icon-btn' onClick={onRefresh}>
              <Icon name='refresh' size={18} strokeWidth={1.8} color='#1F232B' />
            </View>
          ) : null}
          <View className='icon-btn' onClick={onNew}>
            <Icon name='plus' size={21} strokeWidth={1.8} color='#1F232B' />
          </View>
        </View>
      </View>
    )
  }

  return (
    <View className='app-header'>
      {variant === 'chat' && onMenu ? (
        <View className='icon-btn' onClick={onMenu}>
          <Icon name='menu' size={20} />
        </View>
      ) : (
        <View className='icon-btn' onClick={onBack}>
          <Icon name='chevron-left' size={20} />
        </View>
      )}
      <View className='header-mid'>
        <Text className='header-title'>{title}</Text>
        {sub ? (
          <View className='header-sub'>
            {variant === 'scheduled' ? (
              <Icon name='server' size={12} color='#9AA0A8' />
            ) : null}
            <Text>{sub}</Text>
          </View>
        ) : null}
      </View>
      {variant === 'chat' ? (
        onNew || onMore ? (
          <View className='header-actions'>
            {onNew ? (
              <View className='icon-btn' onClick={onNew}>
                <Icon name='message-plus' size={20} strokeWidth={1.8} color='#1F232B' />
              </View>
            ) : null}
            {onMore ? (
              <View className='icon-btn' onClick={onMore}>
                <Icon name='more-horizontal' size={20} />
              </View>
            ) : null}
          </View>
        ) : (
          <View className='header-spacer' />
        )
      ) : null}
      {variant === 'me' ? <View className='header-spacer' /> : null}
      {variant === 'scheduled' ? <View className='header-spacer' /> : null}
      {variant === 'settings' ? (
        onRefresh ? (
          <View className='icon-btn' onClick={onRefresh}>
            <Icon name='refresh' size={19} strokeWidth={1.8} color='#1F232B' />
          </View>
        ) : (
          <View className='header-spacer' />
        )
      ) : null}
    </View>
  )
}
