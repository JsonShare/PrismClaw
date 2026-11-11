import { Image, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'

import { apiUrl, withToken } from '../../services/request'
import { contentToText } from '../../services/sessions'
import type { ContentBlock, ImageBlock, Message } from '../../types'
import { displayTitle } from '../../lib/chat/displayName'
import { ExecutionTimeline } from '../ExecutionTimeline'
import Icon from '../Icon'
import { MarkdownText } from '../MarkdownText'

interface ChatMessageProps {
  message: Message
  onOpenFile: (path: string) => void
  onFeedback?: (vote: 'up' | 'down' | 'none') => void
  /** 点击引导问题：直接以该问题发起下一轮 */
  onQuickReply?: (text: string) => void
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

export function ChatMessage({ message, onOpenFile, onFeedback, onQuickReply }: ChatMessageProps) {
  if (message.role === 'user') {
    const content = message.content
    return (
      <View className='msg msg-me'>
        <View className='bubble-me'>
          {typeof content === 'string' ? (
            <Text>{content}</Text>
          ) : (
            <>
              {content
                .filter((block) => block.type === 'text')
                .map((block, index) => (
                  <Text key={index}>{block.type === 'text' ? block.text : ''}</Text>
                ))}
              <ImageGrid blocks={content} />
              <FileChips blocks={content} onOpenFile={onOpenFile} />
            </>
          )}
        </View>
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
        {message.timeline && message.timeline.length > 0 ? (
          <ExecutionTimeline nodes={message.timeline} />
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
