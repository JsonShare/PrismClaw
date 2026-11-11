import { useEffect, useState } from 'react'
import { FileText } from 'lucide-react'
import { RichMarkdown } from '../chat/MarkdownRenderer'

interface MarkdownPreviewProps {
  file: File | Blob
  className?: string
}

function isMarkdownBlob(file: File | Blob | null): boolean {
  const type = (file?.type || '').toLowerCase()
  return type === 'text/markdown' || type === 'text/x-markdown'
}

/**
 * .md / .markdown 文件的富文本预览。
 *
 * 复用聊天区 RichMarkdown 渲染管线（remark-gfm 表格/任务列表、KaTeX 数学、
 * shiki 双主题代码高亮），保证右面板预览与聊天正文视觉一致——替代
 * @file-viewer/renderer-text 的等宽纯文本渲染。
 *
 * 内容读取遵循 FilePreview 的既有语义：Blob 引用变化（保存/回滚后父级重新
 * fetch）触发重读；StrictMode 双渲染下读取 effect 幂等（cancelled 守卫）。
 */
export function MarkdownPreview({ file, className }: MarkdownPreviewProps) {
  const [content, setContent] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    // file.text() 返回 UTF-8 解码结果；较 FileReader.readAsText 更简洁，
    // 且对 0 字节/二进制误标 md 的输入不会抛同步异常。
    file
      .text()
      .then((text) => {
        if (cancelled) return
        setContent(text)
        setLoading(false)
      })
      .catch(() => {
        if (cancelled) return
        setError('无法读取文件内容')
        setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [file])

  return (
    <div
      className={`markdown-preview-host h-full w-full min-h-0 min-w-0 overflow-y-auto ${className ?? ''}`}
    >
      {loading ? (
        <div className="flex h-full min-h-0 items-center justify-center text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/40">
          <FileText size={16} className="mr-2" />
          加载文档…
        </div>
      ) : error ? (
        <div className="flex h-full min-h-0 items-center justify-center text-[length:var(--fs-sm)] text-[var(--c-danger)]">
          {error}
        </div>
      ) : (
        <div className="markdown-preview-body mx-auto min-h-0 max-w-[820px] px-6 py-5">
          <RichMarkdown content={content} />
        </div>
      )}
    </div>
  )
}

/** 按扩展名或 MIME 判定 md 文件（与 FilePreview 的 HTML 判定逻辑对齐） */
export function isMarkdownFile(fileName: string, file?: File | Blob | null): boolean {
  const ext = (fileName.split('.').pop() || '').toLowerCase()
  if (ext === 'md' || ext === 'markdown') return true
  if (!ext || !fileName.includes('.')) return isMarkdownBlob(file ?? null)
  return false
}