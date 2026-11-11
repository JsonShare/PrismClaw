import { Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useEffect, useState } from 'react'

import AppDialog from '../../components/AppDialog'
import AppHeader from '../../components/AppHeader'
import ChatHeader from '../../components/ChatHeader'
import Composer from '../../components/Composer'
import type { ComposerPayload } from '../../components/Composer'
import ConversationPane from '../../components/ConversationPane'
import Icon from '../../components/Icon'
import ModePicker from '../../components/ModePicker'
import { composeContent } from '../../lib/chat/markers'
import { useThemeClass } from '../../hooks/useThemeClass'
import { createSession } from '../../services/sessions'
import { socket } from '../../services/socket'
import { uploadAttachments } from '../../services/uploads'
import { useAppStore } from '../../stores/useAppStore'
import { useAuthStore } from '../../stores/useAuthStore'
import { useChatStore } from '../../stores/useChatStore'
import { HOME_COMPOSER_KEY, useComposerStore } from '../../stores/useComposerStore'
import type { Mode } from '../../types'
import { toast } from '../../utils/toast'

const SUGGESTIONS = ['写一份标准门诊病历模板', '总结常见检验指标解读要点', '生成科室质控周报模板']

const MODE_META: Record<Mode, { line1: string; line2: string; placeholder: string }> = {
  chat: { line1: '今天想做点什么？', line2: '问我任何问题', placeholder: '发消息，问点什么' },
  work: { line1: '把任务交给我', line2: '我直接开始执行', placeholder: '描述任务，我来开始执行' },
}

export default function Home() {
  const mode = useAppStore((s) => s.mode)
  const workspaceId = useAppStore((s) => s.workspaceId)
  const setWorkspaceId = useAppStore((s) => s.setWorkspaceId)
  const workspaces = useAppStore((s) => s.workspaces)
  const workspacesLoaded = useAppStore((s) => s.workspacesLoaded)
  const workspacesError = useAppStore((s) => s.workspacesError)
  const loadWorkspaces = useAppStore((s) => s.loadWorkspaces)
  const openDialog = useAppStore((s) => s.openDialog)
  const setComposerDraft = useComposerStore((s) => s.setDraft)
  const nickname = useAuthStore((s) => s.nickname)
  const primeConversation = useChatStore((s) => s.primeConversation)
  const sendMessage = useChatStore((s) => s.sendMessage)
  const conversations = useChatStore((s) => s.conversations)

  const pageClass = useThemeClass('page')
  const [wsState, setWsState] = useState(socket.getState())
  // 发送后在本页原地展开对话，不再跳转到独立会话页
  const [inlineSessionId, setInlineSessionId] = useState('')
  const [inlineTitle, setInlineTitle] = useState('')

  const meta = MODE_META[mode]
  const workspace = workspaces.find((item) => item.id === workspaceId)

  useEffect(() => {
    loadWorkspaces().catch(() => undefined)
    socket.connect()
    return socket.subscribeState(setWsState)
  }, [])

  const handleSend = async ({ text, chips, files }: ComposerPayload) => {
    let available = workspaces
    if (!available.length) available = await loadWorkspaces(true)
    const targetWorkspaceId = available.some((item) => item.id === workspaceId)
      ? workspaceId
      : available[0]?.id
    if (!targetWorkspaceId) throw new Error('还没有可用的工作区')

    const fallbackTitle = chips[0]?.label || files[0]?.name || '新任务'
    const res = await createSession(targetWorkspaceId, (text || fallbackTitle).slice(0, 30))
    const attachments = files.length
      ? await uploadAttachments(files, {
          workspaceId: targetWorkspaceId,
          sessionId: res.session_id,
        })
      : []
    if (targetWorkspaceId !== workspaceId) setWorkspaceId(targetWorkspaceId)
    // 先把消息放进去，再原地展开，避免首条消息丢失或需要二次拉取
    primeConversation(res.session_id, targetWorkspaceId)
    setInlineTitle((text || fallbackTitle).slice(0, 24))
    sendMessage(res.session_id, composeContent(text, chips), {
      workspaceId: targetWorkspaceId,
      mode,
      attachments,
    })
    setInlineSessionId(res.session_id)
  }

  const pickWorkspace = () => {
    const open = (list: typeof workspaces) => {
      if (!list.length) {
        toast('暂无工作区')
        return
      }
      openDialog({
        kind: 'sheet',
        title: '选择工作目录',
        items: list.map((item) => ({
          label: item.name,
          value: item.id,
          active: item.id === workspaceId,
        })),
        onSelect: (value) => {
          setWorkspaceId(value)
          toast(`已切换工作目录：${list.find((item) => item.id === value)?.name ?? value}`)
        },
      })
    }
    if (workspacesLoaded) {
      open(workspaces)
      return
    }
    loadWorkspaces(true)
      .then(open)
      .catch((error: Error) => toast(error.message))
  }

  const connectionText = wsState === 'disconnected' ? '服务器未连接 · 点击重连' : ''

  const retryConnection = () => {
    if (wsState !== 'disconnected') return
    toast('正在重新连接服务器…')
    socket.connect()
  }

  if (inlineSessionId) {
    // 列表里的会话标题优先，重命名后页头能立即更新；刚创建时用首条消息兜底
    const inlineTitleText =
      conversations.find((item) => item.id === inlineSessionId)?.title?.trim() ||
      inlineTitle ||
      '新任务'
    return (
      <View className={pageClass}>
        <ChatHeader
          sessionId={inlineSessionId}
          title={inlineTitleText}
          onBack={() => setInlineSessionId('')}
          onNew={() => setInlineSessionId('')}
          onRenamed={setInlineTitle}
          onDeleted={() => setInlineSessionId('')}
        />
        <ConversationPane
          sessionId={inlineSessionId}
          placeholder={meta.placeholder}
        />
      </View>
    )
  }

  return (
    <View className={pageClass}>
      <AppHeader variant='home' onMenu={() => Taro.navigateTo({ url: '/pages/tasks/index' })} />

      <View className='home home-start'>
        <View className='home-hero'>
          <Text className='home-greeting'>{`你好，${nickname || '朋友'}`}</Text>
          <View className='home-title'>
            <Text className='home-title-line'>{meta.line1}</Text>
            <Text className='home-title-line'>{meta.line2}</Text>
          </View>
          {connectionText ? (
            <Text className='home-sub is-action' onClick={retryConnection}>
              {connectionText}
            </Text>
          ) : null}

          <View className='home-suggest'>
            {SUGGESTIONS.map((text) => (
              <View
                className='suggest-row'
                key={text}
                onClick={() => setComposerDraft(HOME_COMPOSER_KEY, text)}
              >
                <Text className='suggest-row-text'>{text}</Text>
                <Icon name='chevron-right' size={14} color='#A6ADBB' />
              </View>
            ))}
          </View>
        </View>

        <View className='home-dock'>
          <View className='home-input'>
            <Composer
              composerKey={HOME_COMPOSER_KEY}
              placeholder={meta.placeholder}
              flat
              onSend={handleSend}
              footer={
                <View className='mode-dock'>
                  <ModePicker />
                  <View className='workspace-btn' onClick={pickWorkspace}>
                    <Icon name='folder-open' size={14} color='#575B62' strokeWidth={1.5} />
                    <Text className='workspace-btn-text'>
                      {workspace?.name ||
                        (workspacesError
                          ? '同步失败 · 点击重试'
                          : workspacesLoaded
                            ? '选择工作区'
                            : '正在同步…')}
                    </Text>
                    <Icon name='chevron-down' size={13} color='#838C9E' />
                  </View>
                </View>
              }
            />
          </View>
        </View>
      </View>

      <AppDialog />
    </View>
  )
}
