import { Image, ScrollView, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'

import { apiUrl, withToken } from '../../services/request'
import { contentToText } from '../../services/sessions'
import type { ContentBlock, ImageBlock, Message } from '../../types'
import { displayTitle } from '../../lib/chat/displayName'
import { CHIP_ICONS, splitMentions } from '../../lib/chat/markers'
import type { StepRow } from '../../lib/chat/stepRows'
import { ExecutionTimeline } from '../ExecutionTimeline'
import Icon from '../Icon'
import { MarkdownText } from '../MarkdownText'
import ReferenceRow from '../ReferenceRow'

interface ChatMessageProps {
  message: Message
  onOpenFile: (path: string) => void
  onFeedback?: (vote: 'up' | 'down' | 'none') => void
  /** 点击引导问题：直接以该问题发起下一轮 */
  onQuickReply?: (text: string) => void
  /** 打开执行详情浮层（由页面根节点渲染，保证层级最高） */
  onOpenTimeline: (rows: StepRow[], finished: boolean) => void
}

function imageSrc(block: ImageBlock): string {
  if (block.file_url) {
    const url = block.file_url.startsWith('http') ? block.file_url : apiUrl(block.file_url)
    return withToken(url)
  }
  if (block.data) return `data:${block.mime_type || 'image/png'};base64,${block.data}`
  return ''
}

function ImageGrid({ blocks }: { blocks: ContentBlock[] }) {
  const images = blocks
    .filter((block): block is ImageBlock => block.type === 'image')
    .map((block) => ({ ...block, src: imageSrc(block) }))
    .filter((block) => block.src)
  if (!images.length) return null
  const urls = images.map((item) => item.src)
  return (
    <View className='msg-images'>
      {images.map((item, index) => (
        <Image
          key={`${item.source_path || index}`}
          className='msg-image'
          src={item.src}
          mode='widthFix'
          onClick={() =>
            Taro.previewImage({ urls, current: item.src }).catch(() => undefined)
          }
        />
      ))}
    </View>
  )
}

/** 已发送的图片附件：设计稿为左对齐横向缩略条，可左右滑动，点击预览 */
function AttachmentStrip({ blocks }: { blocks: ContentBlock[] }) {
  const images = blocks
    .filter((block): block is ImageBlock => block.type === 'image')
    .map((block) => ({ ...block, src: imageSrc(block) }))
    .filter((item) => item.src)
  if (!images.length) return null
  const urls = images.map((item) => item.src)
  return (
    <ScrollView className='msg-attach-strip' scrollX>
      <View className='msg-attach-strip-inner'>
        {images.map((item, index) => (
          <View
            key={`${item.source_path || index}`}
            className='msg-attach-thumb'
            onClick={() =>
              Taro.previewImage({ urls, current: item.src }).catch(() => undefined)
            }
          >
            <Image className='msg-attach-thumb-img' src={item.src} mode='aspectFill' />
          </View>
        ))}
      </View>
    </ScrollView>
  )
}

function FileChips({
  blocks,
  onOpenFile,
}: {
  blocks: ContentBlock[]
  onOpenFile: (path: string) => void
}) {
  const files = blocks.filter((block) => block.type === 'file')
  if (!files.length) return null
  return (
    <View className='msg-files'>
      {files.map((block, index) => {
        if (block.type !== 'file') return null
        return (
          <View
            key={`${block.path}-${index}`}
            className='msg-file'
            onClick={() => onOpenFile(block.path)}
          >
            <Text className='msg-file-name'>{block.name || displayTitle({ path: block.path })}</Text>
            <Text className='msg-file-action'>打开</Text>
          </View>
        )
      })}
    </View>
  )
}

export function ChatMessage({
  message,
  onOpenFile,
  onFeedback,
  onQuickReply,
  onOpenTimeline,
}: ChatMessageProps) {
  if (message.role === 'user') {
    const content = message.content
    const hasImages =
      Array.isArray(content) && content.some((block) => block.type === 'image')
    const hasText =
      typeof content === 'string'
        ? content.trim().length > 0
        : content.some((block) => block.type === 'text' && block.text.trim().length > 0)
    const hasFiles =
      Array.isArray(content) && content.some((block) => block.type === 'file')
    // 技能/专家卡片在正文里以标记或 `@标题` 存在：拆出来按输入框同款 chip 展示
    const parts = (typeof content === 'string'
      ? [content]
      : content.filter((block) => block.type === 'text').map((block) => block.text)
    ).map(splitMentions)
    const chips = parts.flatMap((part) => part.chips)
    const bodyText = parts
      .map((part) => part.text)
      .filter((text) => text.trim().length > 0)
      .join('\n')
    return (
      <View className={`msg msg-me${hasImages ? ' has-attach-strip' : ''}`}>
        {Array.isArray(content) ? <AttachmentStrip blocks={content} /> : null}
        {typeof content === 'string' || hasText || hasFiles ? (
          <View className='bubble-me'>
            {chips.length ? (
              <View className='msg-chips'>
                {chips.map((chip, index) => (
                  <View className='msg-chip' key={`${chip.id}-${index}`}>
                    <Icon
                      name={CHIP_ICONS[chip.kind]}
                      size={13}
                      color='#111316'
                      strokeWidth={2}
                    />
                    <Text className='msg-chip-text'>{chip.label}</Text>
                  </View>
                ))}
              </View>
            ) : null}
            {bodyText.trim() ? <Text>{bodyText}</Text> : null}
            {Array.isArray(content) ? <FileChips blocks={content} onOpenFile={onOpenFile} /> : null}
          </View>
        ) : null}
      </View>
    )
  }

  if (message.role === 'error') {
    return (
      <View className='msg msg-ai'>
        <View className='msg-main'>
          <View className='msg-error'>
            <Text>{contentToText(message.content) || '任务执行失败'}</Text>
          </View>
        </View>
      </View>
    )
  }

  const text = contentToText(message.content)
  const outputFiles = message.metadata?.outputFiles ?? []
  const feedback = message.metadata?.feedback
  const followups = message.metadata?.followupQuestions ?? []
  const hasImages =
    Array.isArray(message.content) && message.content.some((block) => block.type === 'image')
  const canCopy = text.trim().length > 0

  const copyAnswer = () => {
    void Taro.setClipboardData({ data: text })
    void Taro.showToast({ title: '已复制', icon: 'none' })
  }

  // 分享优先走系统分享面板；不支持（小程序/部分浏览器）时退化为复制
  const shareAnswer = async () => {
    const nav = typeof navigator === 'undefined' ? undefined : (navigator as Navigator & {
      share?: (data: { text: string }) => Promise<void>
    })
    if (nav?.share) {
      try {
        await nav.share({ text })
      } catch {
        // 用户取消分享，不做提示
      }
      return
    }
    copyAnswer()
  }

  return (
    <View className='msg msg-ai'>
      <View className='msg-main'>
        <ReferenceRow sources={message.metadata?.referenceSources} />
        {message.timeline && message.timeline.length > 0 ? (
          <ExecutionTimeline nodes={message.timeline} onOpenDetail={onOpenTimeline} />
        ) : null}
        {text.trim() ? <MarkdownText text={text} /> : null}
        {Array.isArray(message.content) ? <ImageGrid blocks={message.content} /> : null}
        {message.metadata?.interrupted ? (
          <View className='msg-stopped'>
            <Text>已中断</Text>
          </View>
        ) : null}
        {outputFiles.length > 0 ? (
          <View className='msg-outputs'>
            {outputFiles.map((path) => (
              <View key={path} className='msg-output' onClick={() => onOpenFile(path)}>
                <Text className='msg-output-name'>{displayTitle({ path })}</Text>
                <Text className='msg-output-action'>打开</Text>
              </View>
            ))}
          </View>
        ) : null}
        {text.trim() || outputFiles.length > 0 || hasImages ? (
          <View className='msg-actions'>
            {canCopy ? (
              <View className='msg-act' onClick={copyAnswer}>
                <Icon name='copy' size={16} color='#4B5563' strokeWidth={1.7} />
              </View>
            ) : null}
            {onFeedback ? (
              <>
                <View
                  className={`msg-act${feedback === 'up' ? ' is-active' : ''}`}
                  onClick={() => onFeedback(feedback === 'up' ? 'none' : 'up')}
                >
                  <Icon
                    name='thumb-up'
                    size={16}
                    color={feedback === 'up' ? '#2C59BC' : '#4B5563'}
                    strokeWidth={1.7}
                  />
                </View>
                <View
                  className={`msg-act${feedback === 'down' ? ' is-active' : ''}`}
                  onClick={() => onFeedback(feedback === 'down' ? 'none' : 'down')}
                >
                  <Icon
                    name='thumb-down'
                    size={16}
                    color={feedback === 'down' ? '#2C59BC' : '#4B5563'}
                    strokeWidth={1.7}
                  />
                </View>
              </>
            ) : null}
            {canCopy ? (
              <View className='msg-act' onClick={() => void shareAnswer()}>
                <Icon name='share' size={16} color='#4B5563' strokeWidth={1.7} />
              </View>
            ) : null}
          </View>
        ) : null}
        {followups.length > 0 && onQuickReply ? (
          <View className='msg-followups'>
            {followups.map((question) => (
              <View
                key={question}
                className='msg-followup'
                onClick={() => onQuickReply?.(question)}
              >
                <Text className='msg-followup-text'>{question}</Text>
              </View>
            ))}
          </View>
        ) : null}
      </View>
    </View>
  )
}
