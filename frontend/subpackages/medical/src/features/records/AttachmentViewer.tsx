/**
 * 原始报告附件查看器 —— 缩略图 / 就地预览 / 灯箱（PDF 与图片，可多张）
 *
 * 取文件走 fetch + Bearer → blob objectURL（<img>/<iframe> 无法自定义请求头），
 * 卸载时 revokeObjectURL 避免内存泄漏。三种呈现共用同一个 useAttachmentUrl。
 */

import { useEffect, useState } from 'react'
import {
  ChevronLeft,
  ChevronRight,
  FileText,
  Image as ImageIcon,
  Loader2,
  Maximize2,
  X,
} from 'lucide-react'
import { fetchReportAttachmentUrl } from '../../api/client'
import type { PatientReport } from '../../types/view'

// ── 附件 URL 加载（缩略图 / 就地预览 / 灯箱共用） ──

function useAttachmentUrl(reportId: string, index: number) {
  const [url, setUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let revoked = false
    let objectUrl: string | null = null
    setUrl(null)
    setFailed(false)
    fetchReportAttachmentUrl(reportId, index)
      .then((u) => {
        if (revoked) { URL.revokeObjectURL(u); return }
        objectUrl = u
        setUrl(u)
      })
      .catch(() => { if (!revoked) setFailed(true) })
    return () => {
      revoked = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [reportId, index])

  return { url, failed }
}

// ── 缩略图 ──

interface ThumbProps {
  reportId: string
  index: number
  mime: string
  pageNo?: number
  onClick: () => void
}

export function AttachmentThumb({ reportId, index, mime, pageNo, onClick }: ThumbProps) {
  const { url, failed } = useAttachmentUrl(reportId, index)
  const isPdf = mime.includes('pdf')

  return (
    <button
      type="button"
      onClick={onClick}
      title={isPdf ? `PDF 第 ${pageNo ?? index + 1} 页` : `原始图片 ${index + 1}`}
      className="relative h-16 w-14 shrink-0 rounded-md overflow-hidden border border-[var(--c-border)] bg-[var(--c-surface-1)] grid place-items-center hover:border-[var(--c-accent)] transition-colors cursor-pointer"
    >
      {failed ? (
        <FileText className="h-4 w-4 text-[var(--c-text-secondary)]" />
      ) : url && !isPdf ? (
        <img src={url} alt={`附件 ${index + 1}`} className="h-full w-full object-cover" />
      ) : (
        <Loader2 className="h-3.5 w-3.5 text-[var(--c-text-secondary)] animate-spin" />
      )}
      {isPdf && (
        <span className="absolute bottom-0.5 right-0.5 rounded bg-black/55 px-1 text-[length:var(--fs-xs)] text-white leading-tight">
          PDF
        </span>
      )}
      {!isPdf && !failed && (
        <ImageIcon className="absolute bottom-0.5 right-0.5 h-3 w-3 text-white/80 drop-shadow" />
      )}
    </button>
  )
}

// ── 就地预览（内嵌在报告行下方，不打断列表浏览） ──

interface InlineProps {
  report: PatientReport
  index: number
  onIndexChange: (i: number) => void
  /** 全屏看原件（灯箱） */
  onExpand?: () => void
}

export function AttachmentInline({ report, index, onIndexChange, onExpand }: InlineProps) {
  const atts = report.attachments ?? []
  const total = atts.length
  const current = atts[index]
  const { url, failed } = useAttachmentUrl(report.id, index)
  const isPdf = (current?.mime ?? '').includes('pdf')

  return (
    <div className="rounded-lg border border-[var(--c-border)] bg-[var(--c-surface-1)] overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--c-border)]">
        <span className="text-[length:var(--fs-sm)] mw-muted truncate flex-1">{report.name}</span>
        {onExpand && (
          <button
            type="button"
            onClick={onExpand}
            className="h-6 w-6 rounded grid place-items-center text-[var(--c-text-secondary)] hover:bg-[var(--c-surface-2)] transition-colors cursor-pointer shrink-0"
            title="全屏查看原件"
          >
            <Maximize2 className="h-3.5 w-3.5" />
          </button>
        )}
        {total > 1 && (
          <div className="flex items-center gap-1 shrink-0">
            <button
              type="button"
              onClick={() => onIndexChange(index - 1)}
              disabled={index === 0}
              className="h-6 w-6 rounded grid place-items-center text-[var(--c-text-secondary)] hover:bg-[var(--c-surface-2)] disabled:opacity-30 transition-colors cursor-pointer"
              title="上一张"
            >
              <ChevronLeft className="h-3.5 w-3.5" />
            </button>
            <span className="text-[length:var(--fs-xs)] mw-faint mw-num">
              {index + 1} / {total}
            </span>
            <button
              type="button"
              onClick={() => onIndexChange(index + 1)}
              disabled={index === total - 1}
              className="h-6 w-6 rounded grid place-items-center text-[var(--c-text-secondary)] hover:bg-[var(--c-surface-2)] disabled:opacity-30 transition-colors cursor-pointer"
              title="下一张"
            >
              <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </div>
        )}
      </div>

      {/* 原件是主体：PDF 全页渲染，不做分页、不呈现识别文本 */}
      <div className="grid place-items-center bg-[var(--c-surface-2)] min-h-[360px]">
        {failed ? (
          <p className="text-[length:var(--fs-sm)] text-[var(--c-danger)] p-6 text-center">
            附件加载失败（可能是鉴权未通过或文件缺失）
          </p>
        ) : !url ? (
          <Loader2 className="h-6 w-6 text-[var(--c-text-secondary)] animate-spin" />
        ) : isPdf ? (
          <iframe src={url} title={`原件 ${index + 1}`} className="w-full h-[70vh] bg-white" />
        ) : (
          <img src={url} alt={`原件 ${index + 1}`} className="max-h-[70vh] w-auto object-contain" />
        )}
      </div>
    </div>
  )
}

// ── 灯箱（全屏看原件） ──

interface LightboxProps {
  report: PatientReport
  index: number
  onIndexChange: (i: number) => void
  onClose: () => void
}

export function AttachmentLightbox({ report, index, onIndexChange, onClose }: LightboxProps) {
  const atts = report.attachments ?? []
  const total = atts.length
  const current = atts[index]
  const { url, failed } = useAttachmentUrl(report.id, index)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      if (e.key === 'ArrowLeft' && index > 0) onIndexChange(index - 1)
      if (e.key === 'ArrowRight' && index < total - 1) onIndexChange(index + 1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [index, total, onClose, onIndexChange])

  const isPdf = (current?.mime ?? '').includes('pdf')

  return (
    <div className="fixed inset-0 z-50 bg-black/70 grid place-items-center p-6" onClick={onClose}>
      <div
        className="relative max-w-4xl w-full flex flex-col items-center gap-2"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="w-full flex items-center gap-2 text-white/85">
          <span className="text-[length:var(--fs-sm)] truncate flex-1">
            {report.name} · 第 {index + 1}/{total} 张
          </span>
          <button
            type="button"
            onClick={onClose}
            className="h-8 w-8 rounded-md grid place-items-center hover:bg-white/15 transition-colors cursor-pointer"
            title="关闭 (Esc)"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="w-full rounded-lg bg-white/95 overflow-hidden grid place-items-center min-h-[50vh]">
          {failed ? (
            <p className="text-[length:var(--fs-sm)] text-[var(--c-danger)] p-6">
              附件加载失败（可能是鉴权未通过或文件缺失）
            </p>
          ) : !url ? (
            <Loader2 className="h-6 w-6 text-[var(--c-text-secondary)] animate-spin" />
          ) : isPdf ? (
            <iframe src={url} title={`附件 ${index + 1}`} className="w-full h-[72vh] bg-white" />
          ) : (
            <img src={url} alt={`附件 ${index + 1}`} className="max-h-[72vh] w-auto object-contain" />
          )}
        </div>

        {total > 1 && (
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => onIndexChange(index - 1)}
              disabled={index === 0}
              className="h-8 w-8 rounded-md grid place-items-center text-white/85 hover:bg-white/15 disabled:opacity-30 transition-colors cursor-pointer"
              title="上一张"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <span className="text-[length:var(--fs-sm)] text-white/70 mw-num">
              {index + 1} / {total}
            </span>
            <button
              type="button"
              onClick={() => onIndexChange(index + 1)}
              disabled={index === total - 1}
              className="h-8 w-8 rounded-md grid place-items-center text-white/85 hover:bg-white/15 disabled:opacity-30 transition-colors cursor-pointer"
              title="下一张"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
