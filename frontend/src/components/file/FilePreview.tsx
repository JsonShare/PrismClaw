import { useMemo } from 'react'
import FileViewer from '@file-viewer/react'
import type { ViewerOptions } from '@file-viewer/react'
import officePreset from '@file-viewer/preset-office'
import textRenderer from '@file-viewer/renderer-text'
import imageRenderer from '@file-viewer/renderer-image'
import archiveRenderer from '@file-viewer/renderer-archive'
import mediaRenderer from '@file-viewer/renderer-media'
import mindmapRenderer from '@file-viewer/renderer-mindmap'
import drawingRenderer from '@file-viewer/renderer-drawing'
import { useResolvedTheme } from '../../hooks/useResolvedTheme'
import { HtmlPreview } from './HtmlPreview'
import { isMarkdownFile, MarkdownPreview } from './MarkdownPreview'

interface FilePreviewProps {
  file: File | Blob
  fileName: string
  className?: string
}

function isHtmlFileName(name: string): boolean {
  const ext = name.split('.').pop()?.toLowerCase() || ''
  return ext === 'html' || ext === 'htm'
}

function isHtmlBlob(file: File | Blob | null): boolean {
  if (!file) return false
  const type = (file.type || '').toLowerCase()
  return type === 'text/html' || type === 'application/xhtml+xml' || type.includes('html')
}

export function FilePreview({ file, fileName, className }: FilePreviewProps) {
  const theme = useResolvedTheme()

  // options 必须记忆化：@file-viewer/react 对 options 引用敏感——每次渲染
  // 传入新对象都会触发 controller.update() → loadSource() 全量重解析文档
  // （docx 预览闪烁的根因）。theme 是唯一动态项，仅在主题真正切换时才
  // 生成新引用。useMemo 必须放在 isHtmlFileName 早退之前（hooks 顺序）。
  const options = useMemo<ViewerOptions>(
    () => ({ ...baseViewerOptions, theme }),
    [theme],
  )

  // Markdown 文件渲染为富文本（复用聊天区渲染管线，与正文视觉一致）。
  if (isMarkdownFile(fileName, file)) {
    return <MarkdownPreview file={file} className={className} />
  }

  // HTML 文件使用内嵌浏览器式预览。按文件名 OR MIME 判定，确保即使文件名
  // 缺扩展名（如某些下载/重命名路径），HTML 内容也渲染成页面而非纯文本。
  if (isHtmlFileName(fileName) || isHtmlBlob(file)) {
    return <HtmlPreview file={file} fileName={fileName} className={className} />
  }

  return (
    <div className={`ofv-host h-full ${className ?? ''}`}>
      <FileViewer
        file={file}
        name={fileName}
        options={options}
      />
    </div>
  )
}

// @file-viewer/renderer-text 的 Handler 泛型使用 HTMLDivElement，
// ViewerOptions.preset 的默认泛型使用 HTMLElement，TypeScript 逆变报错。
// 运行时完全兼容，用类型断言绕过。
const baseViewerOptions: Omit<ViewerOptions, 'theme'> = {
  preset: [
    officePreset,
    textRenderer,
    imageRenderer,
    archiveRenderer,
    mediaRenderer,
    mindmapRenderer,
    drawingRenderer,
  ] as ViewerOptions['preset'],
  rendererMode: 'replace',
  styleIsolation: 'shadow',
  toolbar: false,
}
