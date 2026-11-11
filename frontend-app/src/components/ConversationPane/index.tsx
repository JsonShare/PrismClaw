import { ScrollView, Text, View } from '@tarojs/components'
import { useEffect, useRef, useState } from 'react'

import AppDialog from '../AppDialog'
import Composer from '../Composer'
import type { ComposerPayload } from '../Composer'
import EmptyState from '../EmptyState'
import Icon from '../Icon'
import { ChatMessage } from '../ChatMessage'
import { ClarifyCard } from '../ClarifyCard'
import { ExecutionTimeline } from '../ExecutionTimeline'
import LiveProgress from '../LiveProgress'
import { MarkdownText } from '../MarkdownText'
import ModePicker from '../ModePicker'
import { ThinkingIndicator } from '../ThinkingIndicator'
import { composeContent } from '../../lib/chat/markers'
import FilePreview from '../FilePreview'
import type { PreviewFile } from '../FilePreview'
import { socket } from '../../services/socket'
import { uploadAttachments } from '../../services/uploads'
import { useAppStore } from '../../stores/useAppStore'
import { useChatStore } from '../../stores/useChatStore'
import { useComposerStore } from '../../stores/useComposerStore'
import type { ConvState } from '../../stores/useChatStore'
import type { Message } from '../../types'
import { toast } from '../../utils/toast'

const EMPTY_CONV: ConvState = {
  workspaceId: '',
  messages: [],
  streamingText: '',
  liveTimeline: [],
  liveOutputFiles: [],
  clarify: null,
  isStreaming: false,
  queuedPosition: null,
  hydrated: false,
  hydration: 'idle',
  loadedOffset: 0,
  hasMore: false,
  loadingOlder: false,
  lastFinalizedAt: null,
}

export interface ConversationPaneProps {
  sessionId: string
  placeholder?: string
}

/**
 * 会话正文 + 输入区。首页原地展开对话与独立会话页共用，
 * 只负责内容渲染，页头由页面自行提供。
 */
export default function ConversationPane({
  sessionId,
  placeholder = '发消息给 PrismClaw',
}: ConversationPaneProps) {
  const conv = useChatStore((s) => s.convStates[sessionId]) ?? EMPTY_CONV
  const openConversation = useChatStore((s) => s.openConversation)
  const syncConversation = useChatStore((s) => s.syncConversation)
  const loadOlder = useChatStore((s) => s.loadOlder)
  const sendMessage = useChatStore((s) => s.sendMessage)
  const abort = useChatStore((s) => s.abort)
  const answerClarify = useChatStore((s) => s.answerClarify)
  const submitFeedback = useChatStore((s) => s.submitFeedback)

  const workspaceId = useAppStore((s) => s.workspaceId)
  const mode = useAppStore((s) => s.mode)
  const setComposerDraft = useComposerStore((s) => s.setDraft)

  // 贴底改用滚动锚点：每次自增锚点 id，避免受控 scrollTop 在重渲染时把用户
  // 手动上翻的位置强行拉回底部
  const [endAnchor, setEndAnchor] = useState('chat-end-0')
  const endTickRef = useRef(0)
  const [wsState, setWsState] = useState(socket.getState())
  // 是否已离开底部（决定底部中央的回到底部按钮）
  const [awayFromBottom, setAwayFromBottom] = useState(false)
  // 应用内文件预览（消息附件点击）
  const [preview, setPreview] = useState<PreviewFile | null>(null)
  const loadedOffsetRef = useRef(conv.loadedOffset)
  const reconnectedRef = useRef(false)
  // 用户向上翻阅历史时不再跟随流式输出自动贴底
  const stickBottomRef = useRef(true)
  const bottomGapRef = useRef(Number.POSITIVE_INFINITY)
  // 用户是否离开过顶部：离开后再回到顶部才自动加载更早消息
  const topArmedRef = useRef(false)

  useEffect(() => {
    if (!sessionId) return
    void openConversation(sessionId)
  }, [sessionId, openConversation])

  useEffect(() => {
    if (!sessionId) return
    socket.connect()
    return socket.subscribeState(setWsState)
  }, [sessionId])

  // 断线重连后主动同步一次，补齐断线期间可能错过的消息与结束帧
  useEffect(() => {
    if (wsState === 'ready') {
      if (reconnectedRef.current && sessionId) void syncConversation(sessionId)
      reconnectedRef.current = false
      return
    }
    if (wsState === 'disconnected') reconnectedRef.current = true
  }, [wsState, sessionId, syncConversation])

  const scrollToEnd = () => {
    endTickRef.current += 1
    setEndAnchor(`chat-end-${endTickRef.current}`)
  }

  // 加载更早消息会向前插入内容，此时保持当前位置，避免被强制拉回底部
  useEffect(() => {
    const prepended = conv.loadedOffset < loadedOffsetRef.current
    loadedOffsetRef.current = conv.loadedOffset
    if (prepended || !stickBottomRef.current) return
    scrollToEnd()
  }, [
    conv.messages.length,
    conv.streamingText,
    conv.liveTimeline,
    conv.clarify,
    conv.loadedOffset,
  ])

  const handleScroll = (top: number, scrollHeight: number) => {
    // 越接近底部，scrollHeight - scrollTop 越小；记录历史最小值作为“贴底”基线
    const gap = scrollHeight - top
    if (gap < bottomGapRef.current) bottomGapRef.current = gap
    const stick = gap - bottomGapRef.current < 80
    stickBottomRef.current = stick
    setAwayFromBottom(!stick)

    // 上翻到顶自动加载更早消息：先离开顶部（>160）再回到顶部才触发，
    // 避免初次打开自动贴底时把历史一次性拉完
    if (top > 160) topArmedRef.current = true
    else if (top <= 60 && topArmedRef.current) {
      topArmedRef.current = false
      if (conv.hasMore && !conv.loadingOlder) void loadOlder(sessionId)
    }
  }

  const jumpToBottom = () => {
    stickBottomRef.current = true
    setAwayFromBottom(false)
    scrollToEnd()
  }

  const handleSend = async ({ text, chips, files }: ComposerPayload) => {
    if (!sessionId) throw new Error('会话尚未就绪，请稍后重试')
    const wsId = conv.workspaceId || workspaceId
    const attachments = files.length
      ? await uploadAttachments(files, { workspaceId: wsId, sessionId })
      : []
    stickBottomRef.current = true
    sendMessage(sessionId, composeContent(text, chips), {
      workspaceId: wsId,
      mode,
      attachments,
    })
  }

  const openMessageFile = (path: string) => {
    const name = path.split('/').pop() || path
    const ext = name.includes('.') ? name.split('.').pop() || '' : ''
    setPreview({
      workspaceId: conv.workspaceId || workspaceId,
      path,
      name,
      ext: ext.toLowerCase(),
    })
  }

  // 引导问题只保留在最后一条消息上：发出新消息后，上面的引导问题随之隐藏
  const renderMessage = (message: Message, isLast: boolean) => (
    <ChatMessage
      key={message.id}
      message={message}
      onOpenFile={openMessageFile}
      onFeedback={
        message.id.startsWith('user_') || message.role !== 'assistant'
          ? undefined
          : (vote) => submitFeedback(sessionId, message.id, vote)
      }
      onQuickReply={
        isLast && message.role === 'assistant'
          ? (question) => {
              void handleSend({ text: question, chips: [], files: [] }).catch((error: Error) =>
                toast(error.message),
              )
            }
          : undefined
      }
    />
  )

  const live = conv.isStreaming
  // 对话一旦开始（已有消息或正在输出），就不再提供 chat / 工作区切换入口
  const started = conv.messages.length > 0 || live
  const showLive = live && (conv.liveTimeline.length > 0 || conv.streamingText.trim().length > 0)
  const showEmpty =
    conv.messages.length === 0 &&
    !showLive &&
    conv.hydration !== 'loading' &&
    conv.hydration !== 'error'

  return (
    <>
      {wsState === 'disconnected' ? (
        <View className='conn-banner' onClick={() => socket.connect()}>
          <Text>连接已断开，点击重连</Text>
        </View>
      ) : null}

      <View className='chat-scroll-wrap'>
        <ScrollView
          scrollY
          className='app-body'
          style={{ height: 0, flex: 1, minHeight: 0 }}
          scrollIntoView={endAnchor}
          scrollWithAnimation={false}
          onScroll={(event) => handleScroll(event.detail.scrollTop, event.detail.scrollHeight)}
        >
          <View className='chat-body'>
            {conv.hydration === 'loading' && conv.messages.length === 0 ? (
              <View className='chat-loading'>
                <Text>正在加载会话…</Text>
              </View>
            ) : null}

            {conv.hydration === 'error' ? (
              <View className='chat-loading'>
                <Text>会话加载失败</Text>
                <Text
                  className='chat-retry'
                  onClick={() => openConversation(sessionId)}
                >
                  重试
                </Text>
              </View>
            ) : null}

            {conv.hasMore || conv.loadingOlder ? (
              <View className={`chat-more${conv.loadingOlder ? ' is-loading' : ''}`}>
                <Text>{conv.loadingOlder ? '正在加载更早的消息…' : '上滑加载更早的消息'}</Text>
              </View>
            ) : null}

            {showEmpty ? (
              <EmptyState
                title='开始新的对话'
                desc={'描述你的任务，PrismClaw 会直接连接服务器执行\n并实时返回结果'}
              >
                <View className='hero-chips'>
                  <View className='chip' onClick={() => setComposerDraft(sessionId, '帮我整理这份数据')}>
                    <Icon name='chart' size={16} color='#676D78' />
                    <Text>帮我整理这份数据</Text>
                  </View>
                  <View className='chip' onClick={() => setComposerDraft(sessionId, '总结最近的出院小结')}>
                    <Icon name='file-text' size={16} color='#676D78' />
                    <Text>总结最近的出院小结</Text>
                  </View>
                </View>
              </EmptyState>
            ) : (
              conv.messages.map((message, index) =>
                renderMessage(message, index === conv.messages.length - 1),
              )
            )}

            {showLive ? (
              <View className='msg msg-ai'>
                <View className='msg-main'>
                  <ExecutionTimeline nodes={conv.liveTimeline} />
                  {conv.streamingText.trim() ? (
                    <View className='msg-text'>
                      <MarkdownText text={conv.streamingText} />
                      <View className='caret' />
                    </View>
                  ) : (
                    <ThinkingIndicator text='正在思考…' />
                  )}
                  {conv.liveOutputFiles.length ? (
                    <View className='msg-outputs'>
                      {conv.liveOutputFiles.map((path) => (
                        <View
                          className='msg-output'
                          key={path}
                          onClick={() => openMessageFile(path)}
                        >
                          <Text className='msg-output-name'>{path.split('/').pop() || path}</Text>
                          <Text className='msg-output-action'>打开</Text>
                        </View>
                      ))}
                    </View>
                  ) : null}
                </View>
              </View>
            ) : null}

            {live && !showLive && !conv.clarify ? (
              <View className='msg msg-ai'>
                <View className='msg-main'>
                  <ThinkingIndicator
                    text={wsState === 'ready' ? '正在思考…' : '准备中…'}
                  />
                </View>
              </View>
            ) : null}

            {conv.queuedPosition ? (
              <View className='chat-hint'>
                <Icon name='clock' size={13} color='#9AA0A8' />
                <Text className='chat-hint-text'>排队中，前面还有 {conv.queuedPosition} 个任务</Text>
              </View>
            ) : null}

            {conv.clarify ? (
              <ClarifyCard
                key={conv.clarify.approvalId}
                clarify={conv.clarify}
                onSubmit={(answer) => answerClarify(sessionId, answer)}
                onSkip={() => answerClarify(sessionId, '（用户跳过了所有问题）')}
              />
            ) : null}

            <View id={endAnchor} style={{ height: '1px' }} />
          </View>
        </ScrollView>

        {awayFromBottom ? (
          <View className='jump-bottom' onClick={jumpToBottom}>
            <Icon name='chevron-down' size={20} color='#1F232B' strokeWidth={2} />
          </View>
        ) : null}
      </View>

      <LiveProgress
        active={conv.isStreaming}
        nodes={conv.liveTimeline}
        hasText={conv.streamingText.trim().length > 0}
        queued={conv.queuedPosition}
        clarifying={!!conv.clarify}
        ready={wsState === 'ready'}
      />

      <Composer
        composerKey={sessionId}
        placeholder={placeholder}
        streaming={conv.isStreaming}
        onSend={handleSend}
        onAbort={() => abort(sessionId)}
        footer={
          started ? undefined : (
            <View className='mode-dock'>
              <ModePicker />
            </View>
          )
        }
      />

      <FilePreview file={preview} onClose={() => setPreview(null)} />

      <AppDialog />
    </>
  )
}
