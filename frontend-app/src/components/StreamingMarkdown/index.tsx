import { memo } from 'react'

import { MarkdownText } from '../MarkdownText'
import { chunkKey, splitStreamingMarkdown } from '../../lib/chat/streamReveal'

const MarkdownChunk = memo(function MarkdownChunk({
  content,
  chunked,
}: {
  content: string
  chunked: boolean
}) {
  return <MarkdownText text={content} chunked={chunked} />
})

/**
 * 流式 Markdown：直接渲染后端推送的正文，不做任何本地节流或逐字动画。
 *
 * 分块只为渲染性能——按空行/围栏切块后，已完成的块被 memo 复用，
 * 每次推送只需重解析尾块。视觉上等价于「推多少显示多少」，
 * 节奏完全由后端 chat.delta 决定。
 */
export function StreamingMarkdown({ text }: { text: string }) {
  const chunks = splitStreamingMarkdown(text, true)
  return (
    <>
      {chunks.map((chunk, index) => (
        <MarkdownChunk
          key={index === chunks.length - 1 ? 'streaming-tail' : chunkKey(chunk, index)}
          content={chunk}
          chunked={index !== chunks.length - 1}
        />
      ))}
    </>
  )
}
