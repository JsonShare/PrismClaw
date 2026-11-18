import { ScrollView, Text, View } from '@tarojs/components'
import { Fragment, useEffect, useRef, useState } from 'react'
import Taro from '@tarojs/taro'

import AppDialog from '../AppDialog'
import Composer from '../Composer'
import type { ComposerPayload } from '../Composer'
import EmptyState from '../EmptyState'
import Icon from '../Icon'
import { ChatMessage } from '../ChatMessage'
import { ClarifyCard } from '../ClarifyCard'
import { ExecutionSheet } from '../ExecutionTimeline'
import type { StepRow } from '../../lib/chat/stepRows'
import { StreamingMarkdown } from '../StreamingMarkdown'
import ReferenceRow from '../ReferenceRow'
import WorkspacePicker from '../WorkspacePicker'
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
  currentStreamingSegmentId: 0,
  liveTimeline: [],
  liveOutputFiles: [],
  liveReferenceSources: [],
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
  placeholder = '描述任务或按住说话',
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
  const setComposerDraft = useComposerStore((s) => s.setDraft)

  // 贴底改用滚动锚点：每次自增锚点 id，避免受控 scrollTop 在重渲染时把用户
  // 手动上翻的位置强行拉回底部
  const [endAnchor, setEndAnchor] = useState('chat-end-0')
  const endTickRef = useRef(0)
  // 新提问的顶部锚点：发送后把该条问题定位到视口顶部，让用户自上而下读回答
  const [topAnchor, setTopAnchor] = useState<string | null>(null)
  // 撑在问题下方的填充高度：让问题有足够滚动余量停在顶部
  const [pinFillHeight, setPinFillHeight] = useState(0)
  // 实际触发滚动的位置（与 topAnchor 解耦，保证填充生效后再滚）
  const [scrollTarget, setScrollTarget] = useState('')
  const [wsState, setWsState] = useState(socket.getState())
  // 是否已离开底部（决定底部中央的回到底部按钮）
  const [awayFromBottom, setAwayFromBottom] = useState(false)
  // 应用内文件预览（消息附件点击）
  const [preview, setPreview] = useState<PreviewFile | null>(null)
  // 执行详情浮层：在页面根节点渲染，避免被消息滚动容器裁切层级
  const [execSheet, setExecSheet] = useState<{ rows: StepRow[]; finished: boolean } | null>(null)
  const loadedOffsetRef = useRef(conv.loadedOffset)
  const reconnectedRef = useRef(false)
  // 用户向上翻阅历史时不再跟随流式输出自动贴底
  const stickBottomRef = useRef(true)
  const bottomGapRef = useRef(Number.POSITIVE_INFINITY)
  // 消息滚动视口高度：用于按「距底部像素」判断是否贴底（对齐 PC 的 clientHeight 算法）
  const [viewportHeight, setViewportHeight] = useState(0)
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
    setTopAnchor(null)
    setScrollTarget('')
    setEndAnchor(`chat-end-${endTickRef.current}`)
  }

  // 测量消息滚动视口高度（H5/小程序通用），供贴底判断使用；窗口尺寸变化时重测
  useEffect(() => {
    const measure = () => {
      Taro.createSelectorQuery()
        .select('#chat-scroll')
        .boundingClientRect((rect) => {
          const height = (rect as { height?: number } | null)?.height
          if (height && height > 0) setViewportHeight(Math.round(height))
        })
        .exec()
    }
    measure()
    if (typeof Taro.onWindowResize === 'function') {
      Taro.onWindowResize(measure)
      return () => Taro.offWindowResize?.(measure)
    }
    return undefined
  }, [])

  // 撑高：量出提问本身高度，补足到一屏，使提问能真正停在视口顶部
  useEffect(() => {
    if (!topAnchor || !viewportHeight) {
      setPinFillHeight(0)
      return
    }
    Taro.createSelectorQuery()
      .select(`#${topAnchor}`)
      .boundingClientRect((rect) => {
        const height = (rect as { height?: number } | null)?.height ?? 0
        setPinFillHeight(Math.max(0, viewportHeight - height))
      })
      .exec()
  }, [topAnchor, viewportHeight])

  // 填充高度生效后再触发滚动，确保提问能滚动到顶部
  useEffect(() => {
    if (!topAnchor || pinFillHeight <= 0) return
    setScrollTarget(topAnchor)
  }, [topAnchor, pinFillHeight])

  // 回答结束后取消贴顶状态，撤掉多余的撑高
  useEffect(() => {
    if (topAnchor && !conv.isStreaming) setTopAnchor(null)
  }, [conv.isStreaming, topAnchor])

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
    // 距底部像素：scrollHeight - scrollTop - 视口高度。
    // 旧实现用「历史最小 gap」当基线，会被 iOS 回弹（滚过头）和内容增长污染，
    // 导致已经贴底时按钮还在显示，故改为显式测量视口高度。
    const gap = scrollHeight - top
    if (gap < bottomGapRef.current) bottomGapRef.current = gap
    const viewport = viewportHeight || Math.min(bottomGapRef.current, gap)
    // 贴顶模式下（发送后问题停在顶部）不算贴底，避免流式输出把视图拉回底部
    const stick = !topAnchor && gap - viewport < 80
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
    // 发送后不跟随底部：把新提问顶到视口顶部，回答从下方逐段展开
    stickBottomRef.current = false
    const userMessageId = sendMessage(sessionId, composeContent(text, chips), {
      workspaceId: wsId,
      attachments,
    })
    if (userMessageId) {
      setScrollTarget('')
      setEndAnchor('')
      setTopAnchor(`msg-top-${userMessageId}`)
    }
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
      onOpenTimeline={(rows, finished) => setExecSheet({ rows, finished })}
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
  // 运行态常驻一条 assistant 消息：无正文时显示执行状态，有正文时在末尾显示回复状态
  const showLive = live && !conv.clarify
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
          id='chat-scroll'
          scrollY
          className='app-body'
          style={{ height: 0, flex: 1, minHeight: 0 }}
          scrollIntoView={scrollTarget || endAnchor}
          scrollIntoViewAlignment='start'
          scrollWithAnimation={false}
          onScroll={(event) => handleScroll(event.detail.scrollTop, event.detail.scrollHeight)}
        >
          <View
            className='chat-body'
            style={topAnchor && viewportHeight ? { minHeight: `${viewportHeight}px` } : undefined}
          >
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
              conv.messages.map((message, index) => (
                <Fragment key={message.id}>
                  {message.role === 'user' ? (
                    // 用户提问外层包一层锚点容器：发送后按它的高度补足撑高
                    <View id={`msg-top-${message.id}`}>
                      {renderMessage(message, index === conv.messages.length - 1)}
                    </View>
                  ) : (
                    renderMessage(message, index === conv.messages.length - 1)
                  )}
                </Fragment>
              ))
            )}

            {showLive ? (
              <View className='msg msg-ai'>
                <View className='msg-main'>
                  <ReferenceRow sources={conv.liveReferenceSources} live />
                  {conv.streamingText.trim() ? (
                    <>
                      <View className='msg-text'>
                        <StreamingMarkdown text={conv.streamingText} />
                      </View>
                      <View className='live-status'>
                        <ThinkingIndicator text='回复中...' />
                      </View>
                    </>
                  ) : (
                    <View className='live-status'>
                      <ThinkingIndicator text='正在执行...' />
                    </View>
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

            {conv.queuedPosition ? (
              <View className='chat-hint'>
                <Icon name='clock' size={13} color='#9AA0A8' />
                <Text className='chat-hint-text'>排队中，前面还有 {conv.queuedPosition} 个任务</Text>
              </View>
            ) : null}

            {/* 发送新提问后撑满一屏，使问题能定位到视口顶部，回答在其下方展开 */}
            {pinFillHeight > 0 ? (
              <View className='chat-pin-fill' style={{ height: `${pinFillHeight}px` }} />
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

      <View className='chat-dock'>
        <Composer
          composerKey={sessionId}
          placeholder={placeholder}
          flat
          streaming={conv.isStreaming}
          onSend={handleSend}
          onAbort={() => abort(sessionId)}
          footer={
            started ? undefined : (
              <View className='dock-tools'>
                <WorkspacePicker />
              </View>
            )
          }
        />
        {started ? <Text className='chat-note'>内容由 AI 生成</Text> : null}
      </View>

      {conv.clarify ? (
        <View className='clarify-overlay' onClick={(e) => e.stopPropagation()}>
          <ClarifyCard
            key={conv.clarify.approvalId}
            clarify={conv.clarify}
            onSubmit={(answer) => answerClarify(sessionId, answer)}
            onSkip={() => answerClarify(sessionId, '（用户跳过了所有问题）')}
          />
        </View>
      ) : null}

      {execSheet ? (
        <ExecutionSheet
          rows={execSheet.rows}
          finished={execSheet.finished}
          onClose={() => setExecSheet(null)}
        />
      ) : null}

      <FilePreview file={preview} onClose={() => setPreview(null)} />

      <AppDialog />
    </>
  )
}
