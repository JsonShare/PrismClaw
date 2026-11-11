import { useMemo, useState, useEffect, useCallback } from 'react'
import runIcon from '../../images/other_icon/run.png'

interface HtmlPreviewProps {
  file: File | Blob
  fileName?: string
  className?: string
}

function isHtmlFileName(name: string): boolean {
  const ext = name.split('.').pop()?.toLowerCase() || ''
  return ext === 'html' || ext === 'htm'
}

function isHtmlMimeType(blob: Blob): boolean {
  const type = (blob.type || '').toLowerCase()
  return type === 'text/html' || type === 'application/xhtml+xml' || type.includes('html')
}

/**
 * HTML 预览器。纯 Web 实现：用 Blob URL + sandboxed iframe 渲染。
 * （原 Electron <webview> 内嵌浏览器模式已随客户端封装移除。）
 */
export function HtmlPreview({ file, fileName = '', className = '' }: HtmlPreviewProps) {
  const [active, setActive] = useState(false)
  const [objectUrl, setObjectUrl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const canRun = useMemo(() => {
    if (isHtmlFileName(fileName)) return true
    return isHtmlMimeType(file)
  }, [file, fileName])

  const run = useCallback(async () => {
    if (!canRun) return
    setActive(true)
    setError(null)
    try {
      const html = await file.text()
      const blob = new Blob([html], { type: 'text/html; charset=utf-8' })
      const url = URL.createObjectURL(blob)
      setObjectUrl((prev) => {
        if (prev && prev !== url) URL.revokeObjectURL(prev)
        return url
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败')
      setActive(false)
    }
  }, [canRun, file])

  // 自动运行
  useEffect(() => {
    if (canRun && !active && !objectUrl) {
      const id = window.setTimeout(run, 0)
      return () => window.clearTimeout(id)
    }
  }, [canRun, active, objectUrl, run])

  // 清理 Blob URL
  useEffect(() => {
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [objectUrl])

  // 文件变化时清除上一次运行残留的 error（避免 null→blob 过渡期或重开不同
  // 文件时旧 error 误显示），run() 失败会重新置位。
  useEffect(() => {
    setError(null)
  }, [file])

  if (!canRun) {
    return (
      <div className={`flex items-center justify-center p-6 text-[var(--c-text-secondary)] ${className}`}>
        <span>非 HTML 文件，无法运行预览</span>
      </div>
    )
  }

  return (
    <div className={`flex flex-col h-full bg-white ${className}`}>
      <div className="flex-1 relative bg-white min-h-0">
        {error ? (
          <div className="absolute inset-0 flex items-center justify-center p-6 text-[var(--c-error)] text-[length:var(--fs-sm)]">{error}</div>
        ) : objectUrl ? (
          <iframe src={objectUrl} title={fileName || 'HTML preview'} className="w-full h-full border-0" sandbox="allow-scripts allow-same-origin" />
        ) : (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-[var(--c-text-secondary)]">
            <img src={runIcon} alt="运行" className="w-10 h-10 opacity-40 app-icon" />
            <button type="button" onClick={run}
              className="btn btn-md btn-ghost group">
              <span className="inline-flex items-center justify-center w-8 h-8 rounded-[var(--radius-control)] bg-[var(--c-surface)] group-hover:bg-[var(--c-surface-2)] shadow-sm border border-[var(--c-border)]/30 transition-colors">
                <img src={runIcon} alt="运行" className="w-5 h-5 app-icon" />
              </span>
              点击运行 HTML
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
