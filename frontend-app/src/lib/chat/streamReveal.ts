/**
 * 流式 Markdown 分块。
 * 按空行与代码围栏边界切块，已完成的块可被 memo 复用，只有尾块随流式刷新。
 */

function fenceMarker(line: string): string {
  const match = line.match(/^\s*(`{3,}|~{3,})/)
  return match?.[1] || ''
}

/** 按空行 / 围栏结束位置切块；streaming 为 false 时整段一块 */
export function splitStreamingMarkdown(content: string, streaming = true): string[] {
  if (!streaming) return [content]
  // 先归一 CRLF：行正则里 `.` 不匹配 \r，不归一会让首个 \n 之前的内容整段丢失
  // （例如 "a\r\n\r\nb" 只剩 "\n\nb"）。MarkdownText 内部同样按 \n 解析。
  const text = String(content || '').replace(/\r\n/g, '\n')
  const lines = text.match(/.*(?:\n|$)/g)?.filter(Boolean) || [text]
  const chunks: string[] = []
  let current = ''
  let fence = ''
  for (const line of lines) {
    current += line
    const marker = fenceMarker(line)
    if (marker) {
      if (!fence) fence = marker
      else if (marker[0] === fence[0] && marker.length >= fence.length) {
        fence = ''
        chunks.push(current)
        current = ''
      }
      continue
    }
    if (!fence && /^\s*$/.test(line) && current.trim()) {
      chunks.push(current)
      current = ''
    }
  }
  if (current) chunks.push(current)
  return chunks.length ? chunks : ['']
}

/** 稳定的块 key：内容不变时复用 memo 缓存，避免流式刷新重排已完成段落 */
export function chunkKey(value: string, index: number): string {
  let hash = 2166136261
  for (let offset = 0; offset < value.length; offset += 1) {
    hash = Math.imul(hash ^ value.charCodeAt(offset), 16777619)
  }
  return `${index}-${(hash >>> 0).toString(36)}`
}
