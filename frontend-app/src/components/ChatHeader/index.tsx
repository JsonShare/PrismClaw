import { Text, View } from '@tarojs/components'
import { useState } from 'react'

import { deleteSession, renameSession } from '../../services/sessions'
import { useAppStore } from '../../stores/useAppStore'
import { useChatStore } from '../../stores/useChatStore'
import { toast } from '../../utils/toast'
import AppHeader from '../AppHeader'
import Icon from '../Icon'

interface ChatHeaderProps {
  sessionId: string
  title: string
  /** 有值时左侧显示菜单按钮，否则显示返回箭头 */
  onMenu?: () => void
  onBack?: () => void
  onNew: () => void
  /** 重命名成功后回调（首页内联会话用它同步本地标题） */
  onRenamed?: (name: string) => void
  /** 删除成功后由页面决定去向（首页退回新建态 / 独立页回首页） */
  onDeleted: () => void
}

/** 会话页统一页头：左返回或菜单，右侧新建 + 「…」（重命名 / 删除） */
export default function ChatHeader({
  sessionId,
  title,
  onMenu,
  onBack,
  onNew,
  onRenamed,
  onDeleted,
}: ChatHeaderProps) {
  const openDialog = useAppStore((s) => s.openDialog)
  const removeConversation = useChatStore((s) => s.removeConversation)
  const renameConversation = useChatStore((s) => s.renameConversation)
  const [menuOpen, setMenuOpen] = useState(false)

  const handleRename = () => {
    openDialog({
      kind: 'form',
      title: '重命名会话',
      confirmText: '保存',
      fields: [{ key: 'name', label: '会话名称', value: title }],
      onConfirm: (values) => {
        const name = values.name.trim()
        if (!name) return
        renameSession(sessionId, name)
          .then(() => {
            renameConversation(sessionId, name)
            onRenamed?.(name)
            toast('已重命名')
          })
          .catch((error: Error) => toast(error.message))
      },
    })
  }

  const handleDelete = () => {
    openDialog({
      kind: 'danger',
      title: '删除这个会话？',
      desc: '会话消息与执行记录会一并删除，且无法恢复。',
      confirmText: '删除会话',
      onConfirm: () => {
        deleteSession(sessionId)
          .then(() => {
            removeConversation(sessionId)
            onDeleted()
          })
          .catch((error: Error) => toast(error.message))
      },
    })
  }

  return (
    <>
      <AppHeader
        variant='chat'
        title={title}
        onMenu={onMenu}
        onBack={onBack}
        onNew={onNew}
        onMore={() => setMenuOpen((value) => !value)}
      />
      {menuOpen ? (
        <>
          <View className='header-menu-mask' onClick={() => setMenuOpen(false)} />
          <View className='header-menu'>
            <View
              className='header-menu-item'
              onClick={() => {
                setMenuOpen(false)
                handleRename()
              }}
            >
              <Icon name='edit' size={18} color='#1F232B' strokeWidth={1.8} />
              <Text className='header-menu-text'>重命名</Text>
            </View>
            <View
              className='header-menu-item'
              onClick={() => {
                setMenuOpen(false)
                handleDelete()
              }}
            >
              <Icon name='trash' size={18} color='#D12224' strokeWidth={1.8} />
              <Text className='header-menu-text is-danger'>删除</Text>
            </View>
          </View>
        </>
      ) : null}
    </>
  )
}
