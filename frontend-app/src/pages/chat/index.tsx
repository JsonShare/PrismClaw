import { View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useEffect } from 'react'

import ChatHeader from '../../components/ChatHeader'
import ConversationPane from '../../components/ConversationPane'
import { useThemeClass } from '../../hooks/useThemeClass'
import { contentToText } from '../../services/sessions'
import { useAppStore } from '../../stores/useAppStore'
import { useChatStore } from '../../stores/useChatStore'
import { goHome } from '../../utils/toast'

/** 还原路由中被编码过一次的会话 id */
function decodeRouteId(value: string): string {
  if (!/%[0-9A-Fa-f]{2}/.test(value)) return value
  try {
    return decodeURIComponent(value)
  } catch {
    return value
  }
}

export default function Chat() {
  // Taro 会对路由 query 做一次编码，含中文工作区名的会话 id 到达时仍是编码态；
  // 服务层会再次 encodeURIComponent，这里先还原一次，避免出现 %253A。
  const paramId = decodeRouteId(Taro.getCurrentInstance().router?.params?.id || '')
  const storeActiveId = useChatStore((s) => s.activeId)
  // 页面以路由参数为准，避免从其他会话返回时短暂展示上一个会话的缓存
  const activeId = paramId || storeActiveId
  const conv = useChatStore((s) => s.convStates[activeId])
  const conversations = useChatStore((s) => s.conversations)
  const loadConversations = useChatStore((s) => s.loadConversations)

  const workspaceId = useAppStore((s) => s.workspaceId)
  const loadWorkspaces = useAppStore((s) => s.loadWorkspaces)

  useEffect(() => {
    if (!paramId) return
    if (!conversations.length) void loadConversations(workspaceId, true).catch(() => undefined)
    void loadWorkspaces().catch(() => undefined)
  }, [paramId])

  const current = conversations.find((item) => item.id === activeId)
  // 直接打开旧会话（不在当前分页列表）时，用首条用户消息兜底标题
  const firstUserMessage = conv?.messages.find((message) => message.role === 'user')
  const fallbackTitle = firstUserMessage
    ? contentToText(firstUserMessage.content).replace(/\s+/g, ' ').trim().slice(0, 24)
    : ''
  const title = current?.title?.trim() || fallbackTitle || '新任务'

  return (
    <View className={useThemeClass('page')}>
      <ChatHeader
        sessionId={activeId}
        title={title}
        onMenu={() => void Taro.navigateTo({ url: '/pages/tasks/index' })}
        onNew={goHome}
        onDeleted={goHome}
      />
      {activeId ? <ConversationPane sessionId={activeId} /> : null}
    </View>
  )
}
