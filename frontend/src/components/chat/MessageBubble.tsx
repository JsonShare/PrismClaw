import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Check, ChevronRight, Clock, CornerDownRight, Copy, Download, FileText, Loader2, MoreHorizontal, Share2, ThumbsDown, ThumbsUp } from 'lucide-react'
import type { ContentBlock, FileBlock, FileChange, ImageBlock, Message } from '../../types'
import MarkdownRenderer, {
  StreamingMarkdown,
} from './MarkdownRenderer'
import { renderMarkdownToHtml } from '../../lib/render/markdown-export'
import SystemMessage from './SystemMessage'
import ErrorBubble from './ErrorBubble'
import {
  basename,
  extractLegacyUploadedAttachmentText,
  isSystemNoteText,
  mergeDisplayedFileAttachments,
  splitAttachmentContent,
  type UploadedAttachmentDisplay,
} from '../../lib/chat/attachments'
import { imageBlockToDataUrl } from '../../lib/file/image'
import { outputDownloadUrl } from '../../lib/file/outputFiles'

import ExecutionTimeline from './ExecutionTimeline'
import { isReadOnlyTimeline } from '../../lib/chat/timelineClassify'
import type { HeartbeatPayload } from '../../types'
import FileIcon from '../file/FileIcon'
import InlineChips from './InlineChips'
import { hasMarkers, markersToReadable } from '../../lib/chat/inputMarkers'
import { apiUrl } from '../../lib/system-adapter'

interface Props {
  message: Message
  isStreaming?: boolean
  phase?: HeartbeatPayload | null
  /** runGate: HITL 单一真相源（传给 ExecutionTimeline 取代 phase.state 语义） */
  runGate?: 'awaiting_clarification' | 'awaiting_confirmation' | null
  /** 心跳秒表当前值（usePhaseClock，前端每秒推进）；与 phase 配对传递 */
  elapsedS?: number
  followupQuestions?: string[]
  showSupplementalSections?: boolean
  /** 导出时使用的用户提问文本（仅 assistant 消息需要，avatar 消息不需要） */
  questionText?: string | null
  onRetry?: () => void
  onQuickReply?: (text: string) => void
  onOpenFile?: (path: string) => void
  onOpenFilePreview?: (path: string) => void
  onCopy?: (message: Message) => void | Promise<void>
  onFeedback?: (messageId: string, vote: 'up' | 'down' | null) => void | Promise<void>
  onShare?: (message: Message) => void | Promise<void>
  workspaceId?: string
  token?: string
  conversationId?: string
  onOpenReviewInPanel?: (change: FileChange) => void
}

function formatTimestamp(ts: number): string {
  const d = new Date(ts)
  const pad2 = (n: number) => String(n).padStart(2, '0')
  const yyyy = d.getFullYear()
  const mm = pad2(d.getMonth() + 1)
  const dd = pad2(d.getDate())
  const hh = pad2(d.getHours())
  const mi = pad2(d.getMinutes())
  const ss = pad2(d.getSeconds())
  return `${yyyy}-${mm}-${dd} ${hh}:${mi}:${ss}`
}

/** Compact duration formatter for chat message footer badge. */
function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return ''
  if (ms < 1000) return `${Math.round(ms)}ms`
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  const rem = s % 60
  return rem ? `${m}m ${rem}s` : `${m}m`
}

/* ════════════════════════════════════════════════════════════════════════
   EXPORT · 正文导出（Word .doc + PDF 直接下载）
   设计原则：
   1) 单一真相源 — Word 和 PDF 共用同一份 standalone HTML（样式/内容同源）。
   2) 导出完整正文：遍历 message.content 块并按原始顺序拼接 —
      text → Markdown 渲染；image → dataURL 内联（无内联数据时退化为文件卡，
      不臆造服务端拉取）；file → 文件卡。不含 timeline/reasoning/执行时长。
   3) 样式 1:1 抄 .markdown-content，CSS 变量落地为亮色硬编码值；
      导出自带文档、隔离于父页面，故不写任何 `!important` 继承覆盖
      （那类规则会误杀 shiki 的内联配色）。
   4) 边距不在 body 上写死：PDF 由 jsPDF 统一加 marginX/marginY，
      Word 由 @page / 模板页边距控制，两侧边距一致、跨页稳定。
   5) 代码高亮走项目已缓存的 shiki highlighter（github-light），与正文一致。
   6) Word 导出 .doc（Word 97-2003 "Web Page, Filtered" 单文件格式，
      mso 命名空间 + Generator 标记，原生打开无安全警告）。
   7) PDF 走 jspdf + html2canvas：渲染去 padding 的 body 为整图，
      再按顶层子块边界分页（块不被切断），直接 .pdf 下载。
   ════════════════════════════════════════════════════════════════════════ */

/**
 * 导出专属 CSS（从 index.css 中 .markdown-content 规则 1:1 抄底）。
 * 所有 var(--c-xxx) 替换为亮色主题硬编码值（见 web/src/theme/light.css）。
 */
function buildExportCss(): string {
  return `
    /* —— A4 页面定义（Word + 打印共用；jsPDF 走自身 format:'a4'）—— */
    @page { size: A4; margin: 20mm; }
    @page WordSection1 { size: A4; margin: 20mm; }
    div.WordSection1 { page: WordSection1; }

    html { font-size: 15px; }
    body {
      margin: 0;
      padding: 0;
      color: #1F232B;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
        "Hiragino Sans GB", "Microsoft YaHei", Roboto, Helvetica, Arial, sans-serif;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
      text-rendering: optimizeLegibility;
      /* Word 兼容：段前段后归零，mso 专用字段避免 Word 自动加段间距 */
      mso-margin-top-alt: 0;
      mso-margin-bottom-alt: 0;
    }
    /* 导出文档自带 DOM、与父页面样式完全隔离，无需任何 !important 继承覆盖
       （那类规则会盖过 shiki 内联配色，导致代码高亮全部丢失）。
       仅保留打印颜色保护，确保背景/配色在打印或另存为 PDF 时不褪色。 */
    * {
      print-color-adjust: exact !important;
      -webkit-print-color-adjust: exact !important;
    }
    code, pre, pre *, kbd {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace !important;
    }

    /* ================ 直接复制自 index.css .markdown-content（去掉前缀 + var 硬编码） ================ */
    h1 { font-size: 1.5em; margin: 1.8em 0 0.9em; font-weight: 700; color: #1F232B;
         line-height: 1.3; padding-bottom: 0.4em; border-bottom: 1px solid rgba(17,24,39,.08); }
    h2 { font-size: 1.3em; margin: 1.6em 0 0.7em; font-weight: 700; color: #1F232B;
         line-height: 1.4; padding-left: 0.6em; border-left: 3px solid #2C59BC; }
    h3 { font-size: 1.15em; margin: 1.3em 0 0.6em; font-weight: 600; color: #1F232B; line-height: 1.5; }
    h4 { font-size: 1em; margin: 1.1em 0 0.5em; font-weight: 600; color: #1F232B; line-height: 1.5; }
    h5 { font-size: 0.95em; margin: 0.9em 0 0.5em; font-weight: 600; color: #575B62; line-height: 1.5; }
    h6 { font-size: 0.9em;  margin: 0.8em 0 0.5em; font-weight: 500; color: #575B62; line-height: 1.5; }

    p { margin: 0.75em 0; line-height: 1.75; }

    hr { border: 0; border-top: 1px solid rgba(17,24,39,.14); margin: 2em 0; }

    strong, b { font-weight: 650; color: #1F232B; }
    em, i { font-style: italic; color: #1F232B; }
    del, s { text-decoration: line-through; opacity: 0.6; }

    kbd { display: inline-block; padding: 0.1em 0.45em;
          font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
          font-size: 0.85em; line-height: 1.4; color: #1F232B;
          background-color: #F1F6FF; border: 1.5px solid rgba(17,24,39,.14);
          border-radius: 5px; box-shadow: 0 1px 0 rgba(17,24,39,.08); vertical-align: baseline; }

    a { color: #2C59BC; text-decoration: none; border-bottom: 1px solid transparent;
        padding: 0 1px; border-radius: 3px; }
    a:hover { border-bottom-color: #2C59BC;
              background-color: rgba(9,105,218,.08); }

    /* —— blockquote（冷中性淡灰底 + 3px 左边框，不斜体）—— */
    blockquote { margin: 1em 0; padding: 0.6em 1.2em;
                 border-left: 3px solid rgba(17,24,39,.14);
                 background-color: rgba(17,24,39,.04);
                 border-radius: 0 6px 6px 0;
                 color: #575B62; font-style: normal; }
    blockquote p { margin: 0.4em 0; }
    blockquote p:first-child { margin-top: 0; }
    blockquote p:last-child { margin-bottom: 0; }

    /* —— 列表（原生 list-style + ::marker 调色；Word/Blink/Chrome 三端均支持）—— */
    li { margin: 0.3em 0; line-height: 1.7; }
    body > ul, body > ol, li > ul, li > ol { margin: 0.75em 0; padding-left: 1.6em; }
    ul ul, ol ul, ul ol, ol ol { margin: 0.25em 0; padding-left: 1.3em; }

    ul { list-style-type: disc; }
    ul > li { padding-left: 0.35em; }
    ul > li::marker { color: #575B62; font-size: 0.85em; }
    ul ul { list-style-type: circle; }
    ul ul ul { list-style-type: square; }

    ol { list-style-type: decimal; }
    ol > li { padding-left: 0.45em; }
    ol > li::marker { color: #575B62; font-weight: 600; font-variant-numeric: tabular-nums; }

    /* —— GFM 任务列表（导出时 <input checkbox> 被后处理替换为 ☐/☑ Unicode 字符）—— */
    li.task-list-item > input[type='checkbox'] {
      margin-right: 0.4em; vertical-align: middle;
    }

    /* —— 行内 code —— */
    :not(pre) > code {
      background-color: #F1F6FF; border: 1px solid rgba(17,24,39,.08);
      color: #1F232B; padding: 0.15em 0.4em; border-radius: 4px;
      font-size: 0.87em; font-weight: 500;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }

    img { max-width: 100%; border-radius: 10px; margin: 1em 0;
          box-shadow: 0 1px 2px rgba(0,0,0,.04); }

    /* —— table · card-style —— */
    table { width: 100%; border-collapse: separate; border-spacing: 0;
            margin: 1.5em 0; font-size: 0.95em;
            border: 1px solid rgba(17,24,39,.08);
            border-radius: 12px; overflow: hidden;
            box-shadow: 0 1px 2px rgba(0,0,0,.04); }
    th { background-color: #F1F6FF; font-weight: 600; text-align: left;
         padding: 0.75em 1em; border-bottom: 1px solid rgba(17,24,39,.14);
         color: #1F232B; font-size: 0.95em; position: relative; }
    th:not(:last-child)::after {
      content: ''; position: absolute; right: 0; top: 25%;
      height: 50%; width: 1px; background-color: rgba(17,24,39,.08); opacity: 0.5;
    }
    td { padding: 0.7em 1em; border-bottom: 1px solid rgba(17,24,39,.08); color: #1F232B; }
    tr:nth-child(even) td { background-color: rgba(246,248,250,.5); }
    tr:last-child td { border-bottom: none; }

    /* —— 代码块外壳（与 CodeBlock 组件样式镜像，用于导出）—— */
    .code-block {
      position: relative; margin: 1em 0; overflow: hidden;
      border-radius: 6px; border: 1px solid rgba(17,24,39,.08);
      background: #FFFFFF;
    }
    .code-block-header {
      display: flex; align-items: center; justify-content: space-between;
      border-bottom: 1px solid rgba(17,24,39,.08);
      padding: 6px 16px; font-size: 0.82em; font-weight: 500; color: rgba(90,93,98,.8);
    }
    .code-block-body {
      overflow-x: auto; font-size: 0.88em; line-height: 1.65;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .code-block-body pre { margin: 0 !important; padding: 12px 16px !important; background: transparent !important;
      /* 长代码行换行而非横向溢出，避免导出 PDF 时被切掉右侧 */
      white-space: pre-wrap; word-break: break-word; overflow-wrap: anywhere; }
    .code-block-body code { font-family: inherit !important; }

    /* —— 导出内嵌图片（与聊天气泡 ImageGrid 一致：有 data 才内联，否则退化为文件卡）—— */
    .export-image { margin: 1em 0; }
    .export-image img {
      display: block; max-width: 100%; height: auto; border-radius: 10px;
      box-shadow: 0 1px 2px rgba(0,0,0,.04);
    }

    /* —— 文件卡片（图片无内联数据时 / file 附件）—— */
    .export-file {
      display: flex; align-items: center; gap: 12px;
      margin: 1em 0; padding: 10px 14px;
      border: 1px solid rgba(17,24,39,.08); border-radius: 10px;
      background-color: #F1F6FF;
    }
    .export-file-icon {
      flex: 0 0 auto; width: 38px; height: 38px; border-radius: 8px;
      display: grid; place-items: center;
      background-color: #FFFFFF; border: 1px solid rgba(17,24,39,.08);
      font-size: 10px; font-weight: 700; letter-spacing: .04em;
      color: #2C59BC; text-transform: uppercase;
    }
    .export-file-meta { min-width: 0; }
    .export-file-name {
      font-size: 0.92em; font-weight: 600; color: #1F232B;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .export-file-path {
      font-size: 0.8em; color: #575B62; margin-top: 2px;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }

    /* —— 打印降级（@page 已在主 CSS 定义，这里仅微调字号）—— */
    @media print {
      html { font-size: 14px; }
      body { padding: 0; }
    }
  `.replace(/\s+/g, ' ').trim()
}

/* —————————————————————————————————— 导出内容构建 —————————————————————————————————— */

function buildAnswerBodyHtml(text: string): string {
  const trimmed = text.trim()
  if (!trimmed) return `<p style="color:#59636E;font-style:italic;">(回答内容为空)</p>`
  return renderMarkdownToHtml(trimmed, 'export')
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/** 从消息 content（string | ContentBlock[]）中提取纯文本问题（仅 text 块） */
export function extractQuestionPlainText(content: string | ContentBlock[]): string {
  // 导出/分享用的是可读文本：正文里的内联卡片标记还原成 `@标题`
  if (typeof content === 'string') return markersToReadable(content).replace(/\s+/g, ' ').trim()
  return content
    .filter((b): b is ContentBlock & { type: 'text' } => b.type === 'text' && !isSystemNoteText(b.text))
    .map((b) => markersToReadable(b.text).replace(/\s+/g, ' ').trim())
    .filter(Boolean)
    .join(' ')
}

/* ── 文件大小人类可读（导出文件卡用） ─────────────────────────────── */
function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return ''
  const units = ['B', 'KB', 'MB', 'GB']
  let v = bytes
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v >= 10 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`
}

/* ── 文件卡片（图片无内联数据 / file 附件 共用） ─────────────────── */
function renderFileCardHtml(name: string, path: string, ext: string, size = ''): string {
  const meta = [path, size].filter(Boolean).join('  ·  ')
  return (
    `<div class="export-file">` +
    `<div class="export-file-icon">${escapeHtml(ext.slice(0, 4))}</div>` +
    `<div class="export-file-meta">` +
    `<div class="export-file-name">${escapeHtml(name)}</div>` +
    (meta ? `<div class="export-file-path">${escapeHtml(meta)}</div>` : '') +
    `</div></div>`
  )
}

/* ── 图片块：有内联 data 才内联，否则退化为文件卡（与聊天气泡一致，不臆造服务端拉取） ── */
function renderImageBlockHtml(img: ImageBlock): string {
  const hasInline = Boolean(img.data && img.data.trim()) && (img.mime_type || '').startsWith('image/')
  if (hasInline) {
    return `<div class="export-image"><img src="${imageBlockToDataUrl(img)}" alt="图片" /></div>`
  }
  const name = (img.source_path ? img.source_path.split('/').pop() : '') || 'image'
  const ext = (name.split('.').pop() || 'IMG').toUpperCase()
  return renderFileCardHtml(name, img.source_path || '', ext)
}

/* ── file 附件块 ─────────────────────────────────────────────────── */
function renderFileBlockHtml(file: FileBlock): string {
  const name = file.name?.trim() || basename(file.path) || 'file'
  const ext = (name.split('.').pop() || file.path.split('.').pop() || 'FILE').toUpperCase()
  const size = typeof file.size_bytes === 'number' && file.size_bytes > 0 ? formatBytes(file.size_bytes) : ''
  return renderFileCardHtml(name, file.path, ext, size)
}

/**
 * 按 message.content 的原始块顺序构建正文 HTML：
 * - text  → Markdown 渲染（buildAnswerBodyHtml）
 * - image → 内联图片或文件卡
 * - file  → 文件卡
 * 块直接拼接为 body 子节点（无外层包裹），供 PDF 按块边界分页。
 */
async function buildExportBodyHtml(content: string | ContentBlock[]): Promise<string> {
  const sections: string[] = []
  if (typeof content === 'string') {
    sections.push(buildAnswerBodyHtml(content))
  } else {
    for (const block of content) {
      if (block.type === 'text') {
        if (isSystemNoteText(block.text)) continue
        if (!block.text.trim()) continue
        sections.push(buildAnswerBodyHtml(block.text))
      } else if (block.type === 'image') {
        sections.push(renderImageBlockHtml(block))
      } else if (block.type === 'file') {
        sections.push(renderFileBlockHtml(block))
      }
    }
  }
  return sections.join('')
}

/**
 * 构建 Word/PDF 共用的 standalone HTML（带 <!doctype>/<html>/<head>/<body>）。
 * 内容格式：问题：xxx / 回答：yyy（两段式，不含 timeline/reasoning/执行时长）。
 * @param questionText 用户提问文本；为 null/空串时仅渲染回答。
 */
async function buildStandaloneAnswerHtml(message: Message, questionText?: string | null): Promise<string> {
  const ts = formatTimestamp(message.timestamp)
  const hasQuestion = Boolean(questionText && questionText.trim())

  const bodyInner = await buildExportBodyHtml(message.content)
  const css = buildExportCss()

  // 问题段 HTML：仅当 questionText 非空时渲染
  const questionSectionHtml = hasQuestion
    ? `<div style="margin-bottom: 28px;">` +
      `<div style="font-size: 13px; font-weight: 600; color: #2C59BC; margin-bottom: 6px;">问题</div>` +
      `<div style="font-size: 15px; color: #1F2328; line-height: 1.75;">${escapeHtml(questionText!.trim())}</div>` +
      `</div>`
    : ''

  // 回答段 HTML
  const answerSectionHtml =
    `<div>` +
    `<div style="font-size: 13px; font-weight: 600; color: #2C59BC; margin-bottom: 6px;">回答</div>` +
    (bodyInner || `<p style="color:#59636E;font-style:italic;">(内容为空)</p>`) +
    `</div>`

  let html =
      `<!doctype html><html><head><meta charset="utf-8">` +
      `<meta name="viewport" content="width=device-width, initial-scale=1">` +
      `<title>${escapeHtml(hasQuestion ? questionText!.trim().slice(0, 30) : '回答')} — ${escapeHtml(ts)}</title>` +
      `<style>${css}</style></head><body>` +
      `<div class="WordSection1">` +
      // 文档标题（角色 + 时间）
      `<div style="border-bottom:1px solid rgba(17,24,39,.08); padding-bottom: 10px; margin-bottom: 24px;">` +
      `<h1 style="margin: 0 0 2px 0; border: 0; padding: 0; font-size: 20px; font-weight: 700; color: #1F2328; line-height: 1.3;">${escapeHtml(hasQuestion ? '问答记录' : (message.role === 'assistant' ? '助手回答' : '用户提问'))}</h1>` +
      `<div style="font-size: 12px; color: #59636E; margin-top: 2px;">${escapeHtml(ts)}</div>` +
      `</div>` +
      questionSectionHtml +
      answerSectionHtml +
      `</div>` +
      `</body></html>`

    // Word 不渲染 HTML 表单控件 <input checkbox>；替换为跨引擎 Unicode 字符
    html = html.replace(
      /<input[^>]*type=["']?checkbox["']?[^>]*>/gi,
      (m: string) => (m.includes('checked') ? '\u2611' : '\u2610'),
    )

    return html
}

/* —————————————————————————————————— 下载触发（Blob + a.download；RFC5987 中文文件名兼容） —————————————————————————————————— */

/**
 * 触发浏览器保存文件下载。
 * 中文文件名做 RFC5987 编码（download + download*=UTF-8''双保险），
 * 兼容 Chrome/Safari/Firefox 下中文文件名不乱码。
 */
function triggerBlobDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  try {
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    // RFC5987: 额外设置 download* 属性（仅支持 Safari/Firefox，Chrome 仍读 a.download）
    //   注意：DOM 属性名是 download，但 HTML attribute 可以设置 download*
    //   走 setAttributeRaw —— 下面的属性写法会被 React 吃掉，所以直接 setAttribute
    try {
      a.setAttribute('download*', `UTF-8''${encodeURIComponent(filename)}`)
    } catch { /* noop */ }
    document.body.appendChild(a)
    try { a.click() } finally {
      setTimeout(() => { try { document.body.removeChild(a) } catch { /* noop */ } }, 0)
    }
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 4000)
  }
}

function sanitizeFilenameBase(s: string, maxLen = 60, appendEllipsis = false): string {
  const t = s.replace(/[\\/:*?"<>|]/g, '_').replace(/\s+/g, ' ').trim()
  if (t.length <= maxLen) return t || 'answer'
  const truncated = t.slice(0, maxLen).trim()
  return appendEllipsis ? truncated + '\u2026' : (truncated || 'answer')
}

/** 精确到秒的文件名时间戳（避免 1 分钟内导出两次覆盖） */
function formatTimestampForName(ts: number): string {
  const d = new Date(ts)
  const pad2 = (n: number) => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}${pad2(d.getMonth() + 1)}${pad2(d.getDate())}-` +
    `${pad2(d.getHours())}${pad2(d.getMinutes())}${pad2(d.getSeconds())}`
  )
}

/* —————————————————————————————————— Word（.doc） 导出 —————————————————————————————————— */

/**
 * 导出为真正的 Word 文档（.doc，Word 97-2003 "Web Page, Filtered" 单文件格式）。
 * - 扩展名 = .doc（不是 .html！.html 只是浏览器打开的网页，不是 Word 文件）。
 * - 加 mso 命名空间声明 + Microsoft Word 15 filtered Generator 标记，
 *   让 Word/WPS 识别为原生 Word 文档、按 mso-* 字段处理段间距，打开时不弹"格式与扩展名不匹配"安全警告。
 * - 加 UTF-8 BOM，避免老版本 Word 在中文 Windows 上按 GBK 解析乱码。
 */
async function exportAnswerAsWord(message: Message, questionText?: string | null): Promise<void> {
  const fullHtml = await buildStandaloneAnswerHtml(message, questionText)
  // 替换 <html> 开标签为 MSO 命名空间版本（不影响 HTML 在浏览器里的渲染）
  const msoHtml = fullHtml.replace(
    /^<html>/i,
    `<html xmlns:o="urn:schemas-microsoft-com:office:office" ` +
    `xmlns:w="urn:schemas-microsoft-com:office:word" ` +
    `xmlns="http://www.w3.org/TR/REC-html40">`,
  ).replace(
    /<head>/i,
    `<head><meta name=Generator content="Microsoft Word 15 (filtered)">` +
    // MSO WordDocument 设置：Print 视图 + 100% 缩放，确保 Word 按 A4 页面渲染
    `<!--[if gte mso 9]><xml><w:WordDocument><w:View>Print</w:View><w:Zoom>100</w:Zoom>` +
    `<w:SpellingState>Clean</w:SpellingState><w:GrammarState>Clean</w:GrammarState>` +
    `</w:WordDocument></xml><![endif]-->`,
  )
  const blob = new Blob(['\ufeff' + msoHtml], {
    type: 'application/msword;charset=utf-8',
  })
  const qText = (questionText || '').trim()
  const base = qText
    ? sanitizeFilenameBase(qText, 10, true)
    : sanitizeFilenameBase(
        `${message.role === 'assistant' ? 'Answer' : 'Question'}-${formatTimestampForName(message.timestamp)}`,
      )
  triggerBlobDownload(blob, `${base}.doc`)
}

/* —————————————————————————————————— PDF（jspdf + html2canvas 直接下载） 导出 —————————————————————————————————— */

/**
 * 导出为真正的 PDF 文件（直接触发 .pdf 下载，不再依赖浏览器打印对话框）。
 *
 * 流程：
 *   1. 动态 import jspdf + html2canvas（不拖慢主包）
 *   2. 把 standalone HTML 写进一个隐藏、同源的 iframe（隔离样式，可被 html2canvas 抓取）
 *   3. html2canvas 把去 padding 的 body 渲染成高清整图（scale=2）
 *   4. 按 body 顶层子块的边界分页：每块对齐到页首，块不被从中间切断；
 *      每页统一 marginX/marginY，跨页边距稳定。
 *   5. pdf.save() 直接下载
 *
 * 降级：若 html2canvas/jspdf 加载或渲染失败，退回 iframe 内 window.print()（用户选"另存为 PDF"）。
 */
async function exportAnswerAsPdf(message: Message, questionText?: string | null): Promise<void> {
  const fullHtml = await buildStandaloneAnswerHtml(message, questionText)
  const qText = (questionText || '').trim()
  const base = qText
    ? sanitizeFilenameBase(qText, 10, true)
    : sanitizeFilenameBase(
        `${message.role === 'assistant' ? 'Answer' : 'Question'}-${formatTimestampForName(message.timestamp)}`,
      )

  // 1. 隐藏、同源 iframe 承载 standalone HTML（隔离样式 + 可被 html2canvas 抓取）
  //    宽度设为 A4 在 96DPI 下的像素值（794px），让正文按 A4 页宽自然换行
  const iframe = document.createElement('iframe')
  iframe.setAttribute('aria-hidden', 'true')
  iframe.style.cssText =
    'position:fixed;left:-10000px;top:0;width:794px;height:1123px;border:0;pointer-events:none;'
  document.body.appendChild(iframe)
  const idoc = iframe.contentDocument
  if (!idoc) {
    document.body.removeChild(iframe)
    throw new Error('无法创建导出预览窗口')
  }
  idoc.open()
  idoc.write(fullHtml)
  idoc.close()

  try {
    // 等 iframe 加载 + 字体/图片就绪
    await new Promise<void>((resolve) => {
      const done = () => resolve()
      if (idoc.readyState === 'complete') done()
      else iframe.onload = done
      // 兜底：最迟 1.2s 后强制继续，避免 onload 已错过导致卡死
      setTimeout(done, 1200)
    })

    // 2. 动态加载 PDF 依赖
    const [{ default: jsPDF }, { default: html2canvas }] = await Promise.all([
      import('jspdf'),
      import('html2canvas'),
    ])

    // 3. 渲染正文为高清图片
    const canvas = await html2canvas(idoc.body, {
      scale: 2,
      backgroundColor: '#ffffff',
      useCORS: true,
      logging: false,
      windowWidth: 794,
    })

    // 4. 按顶层子块边界分页拼装 PDF（块不被切断，跨页边距一致，每段互不重叠）
    const pdf = new jsPDF({ unit: 'mm', format: 'a4', orientation: 'portrait' })
    const pageW = pdf.internal.pageSize.getWidth()
    const pageH = pdf.internal.pageSize.getHeight()
    const marginX = 20
    const marginY = 20
    const contentW = pageW - marginX * 2
    const contentH = pageH - marginY * 2
    const mmPerPx = contentW / canvas.width // mm→canvas px 换算（分页判定 + drawImage 尺寸共用）

    // 每个顶层子块在 canvas 中的纵向位置（pxScale = canvas 像素 / 布局宽度 794）
    const pxScale = canvas.width / 794
    // 正文被 WordSection1 包裹，分页按其子块边界切分（块不被从中间切断）
    const wordSection = idoc.querySelector('.WordSection1')
    const sectionEl = (wordSection as HTMLElement) || idoc.body
    const sectionRect = sectionEl.getBoundingClientRect()
    const blocks = Array.from(sectionEl.children) as HTMLElement[]

    // 计算每页正文在 canvas 中的起始 y（= 该页首个块的顶部）；块放不下剩余空间时起新页
    const pageStartsCanvas: number[] = [0]
    let curTop = 0
    let curBottom = 0
    for (const el of blocks) {
      const r = el.getBoundingClientRect()
      const top = (r.top - sectionRect.top) * pxScale
      const h = r.height * pxScale
      if (curBottom > 0 && top > curTop && top + h > curTop + contentH / mmPerPx) {
        pageStartsCanvas.push(top)
        curTop = top
        curBottom = top + h
      } else {
        curBottom = Math.max(curBottom, top + h)
      }
    }
    pageStartsCanvas.push(canvas.height) // 末尾哨兵：最后一段到 canvas 底部

    // 逐段把大图裁切成独立小图，每段对应一页 → 块不被切断、页间不重叠
    for (let i = 0; i < pageStartsCanvas.length - 1; i++) {
      const y0 = pageStartsCanvas[i]
      const y1 = pageStartsCanvas[i + 1]
      const segH = y1 - y0
      if (segH <= 0) continue
      if (i > 0) pdf.addPage()
      const slice = document.createElement('canvas')
      slice.width = canvas.width
      slice.height = Math.ceil(segH)
      const sctx = slice.getContext('2d')
      if (!sctx) continue
      sctx.fillStyle = '#ffffff'
      sctx.fillRect(0, 0, slice.width, slice.height)
      sctx.drawImage(canvas, 0, y0, canvas.width, segH, 0, 0, canvas.width, segH)
      const sliceData = slice.toDataURL('image/jpeg', 0.92)
      pdf.addImage(sliceData, 'JPEG', marginX, marginY, contentW, segH * mmPerPx, undefined, 'FAST')
    }
    pdf.save(`${base}.pdf`)
  } catch (err) {
    // 降级：iframe 内直接打印（用户选"另存为 PDF"）
    console.error('[ExportPDF] 渲染失败，降级为打印：', err)
    try {
      iframe.focus()
      iframe.contentWindow?.print()
    } catch (e2) {
      console.error('[ExportPDF] 打印降级也失败：', e2)
      throw err
    }
  } finally {
    // 打印降级分支用得到 iframe，延迟到下一帧再移除
    setTimeout(() => {
      try { document.body.removeChild(iframe) } catch { /* noop */ }
    }, 500)
  }
}

function ImageGrid({
  images,
  alignRight,
  onPreviewImage,
  workspaceId,
  token,
}: {
  images: ReturnType<typeof splitAttachmentContent>['images']
  alignRight: boolean
  onPreviewImage?: (path: string) => void
  workspaceId?: string | null
  token?: string | null
}) {
  // 构造从 source_path 拉原图的后端 URL —— file_url 缺失时的回退显示。
  const rawFileUrl = (path: string) => {
    const params = new URLSearchParams()
    params.set('workspace_id', workspaceId || 'default')
    params.set('path', path)
    if (token) params.set('_token', token)
    return apiUrl(`/api/files/raw?${params.toString()}`)
  }

  // 优先用后端产出的 file_url（已含 workspace_id + path 编码，存储无关），
  // 缺失时回退到前端用 source_path 拼装；_token 鉴权按需补在查询串末尾。
  const attachToken = (url: string) => {
    if (!token) return url
    const sep = url.includes('?') ? '&' : '?'
    return `${url}${sep}_token=${encodeURIComponent(token)}`
  }
  const resolvedFileUrl = (path?: string | null, fileUrl?: string | null) => {
    const base = fileUrl && fileUrl.trim() ? fileUrl : path ? rawFileUrl(path) : ''
    return base ? attachToken(base) : ''
  }

  return (
    <div className={`flex flex-wrap gap-2 mb-2 ${alignRight ? 'justify-end' : 'justify-start'}`}>
      {images.map((img, i) => {
        const srcPath = img.source_path
        const fileUrl = img.file_url
        const hasData = Boolean(img.data && img.data.trim())
        // 预览走 onPreviewImage(path)，preview 控制器按 source_path 自行拼 URL，
        // 故 clickable 仍以 source_path 是否存在为准（后端附加 file_url 时 source_path 必存在）。
        const clickable = Boolean(onPreviewImage && srcPath)
        const isImageType = (img.mime_type || '').startsWith('image/')
        const ext = (img.mime_type || '').split('/').pop()?.toUpperCase() || 'IMG'

        // 渲染图片有三条路径：
        //   1) 有内联 data（base64） — 直接用 dataURL，实时上传场景
        //   2) 无 data 但有后端 file_url — 优先用（云端部署唯一可靠地址）
        //   3) 无 data/file_url 但有 source_path — 前端拼 /api/files/raw，刷新重放兼容
        //      （后端 _sanitize_content 故意丢弃 data 省带宽，只留 source_path）
        const canShowImage = isImageType && (hasData || Boolean(srcPath || fileUrl))
        const imgSrc = hasData
          ? imageBlockToDataUrl(img)
          : (srcPath || fileUrl)
            ? resolvedFileUrl(srcPath, fileUrl)
            : ''

        return (
          <div
            key={`img-${i}`}
            role={clickable ? 'button' : undefined}
            tabIndex={clickable ? 0 : undefined}
            onClick={clickable ? () => onPreviewImage!(srcPath!) : undefined}
            onKeyDown={clickable ? (e) => { if (e.key === 'Enter' || e.key === ' ') onPreviewImage!(srcPath!) } : undefined}
            className={`block max-w-[140px] rounded-[var(--radius-control)] overflow-hidden border border-[var(--c-border)] bg-[var(--c-bg)]
                       hover:opacity-90 transition-opacity ${clickable ? 'cursor-pointer' : ''}`}
          >
            {canShowImage && imgSrc ? (
              <img
                src={imgSrc}
                alt={`图片 ${i + 1}`}
                loading="lazy"
                className="block max-h-30 object-contain"
                onError={(e) => {
                  // raw URL 拉取失败（文件已被清理等）→ 回退到文件卡占位
                  const target = e.currentTarget
                  target.style.display = 'none'
                  const fallback = document.createElement('div')
                  fallback.className = 'flex flex-col items-center justify-center gap-2 w-[80px] h-[60px] bg-[var(--c-surface)]'
                  fallback.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="shrink-0 opacity-60"><rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/></svg><div class="text-[10px] font-medium text-[var(--c-text-secondary)] tracking-wide opacity-60 uppercase">${ext}</div>`
                  target.parentElement?.appendChild(fallback)
                }}
              />
            ) : (
              <div className="flex flex-col items-center justify-center gap-2 w-[80px] h-[60px] bg-[var(--c-surface)]">
                <FileIcon filename={srcPath || `image.${ext.toLowerCase()}`} size={32} className="shrink-0" />
                <div className="text-[length:calc(var(--fs-sm)*0.85)] font-medium text-[var(--c-text-secondary)] tracking-wide opacity-60 uppercase">
                  {ext}
                </div>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

export default function MessageBubble({
  message,
  isStreaming,
  phase,
  runGate,
  elapsedS,
  followupQuestions,
  showSupplementalSections = true,
  questionText,
  onRetry,
  onQuickReply,
  onOpenFile,
  onOpenFilePreview,
  onCopy,
  onFeedback,
  onShare,
  workspaceId,
  token,
  conversationId: _conversationId,
  onOpenReviewInPanel: _onOpenReviewInPanel,
}: Props) {
  if (message.role === 'system') {
    return <SystemMessage message={message} />
  }

  if (message.role === 'error') {
    return <ErrorBubble message={message} onRetry={onRetry} />
  }

  const isUser = message.role === 'user'
  const [copyOk, setCopyOk] = useState(false)
  const [shareOk, setShareOk] = useState(false)
  const [isDownloadingAll, setIsDownloadingAll] = useState(false)
  const [downloadAllError, setDownloadAllError] = useState<string | null>(null)

  const { text, attachments, files } = splitAttachmentContent(message.content)
  const uploaded = useMemo(() => (isUser ? extractLegacyUploadedAttachmentText(text) : { text, attachments: [] as UploadedAttachmentDisplay[] }), [isUser, text])
  const displayText = isUser ? uploaded.text : text
  const uploadAttachments = isUser ? uploaded.attachments : []

  const fileAttachments = useMemo(() => mergeDisplayedFileAttachments(files, uploadAttachments), [files, uploadAttachments])
  const renderedAttachments = useMemo<Array<UploadedAttachmentDisplay | (typeof attachments)[number]>>(() => {
    if (!isUser) return attachments
    const normalizedFiles = new Map(fileAttachments.map((file) => [file.path, file] as const))
    const ordered: Array<UploadedAttachmentDisplay | Extract<(typeof attachments)[number], { type: 'image' }>> = []
    for (const attachment of attachments) {
      if (attachment.type === 'image') {
        ordered.push(attachment)
        continue
      }
      const normalized = normalizedFiles.get(attachment.path)
      if (normalized) ordered.push(normalized)
    }
    const seenPaths = new Set(ordered.filter((item) => 'path' in item).map((item) => item.path))
    for (const legacy of uploadAttachments) {
      if (!seenPaths.has(legacy.path)) ordered.push(legacy)
    }
    return ordered
  }, [attachments, fileAttachments, isUser, uploadAttachments])

  // assistant 消息结构（F7 后）：
  // - content（displayText）：流式期间来自 streamingText，done 时由 useChatSocket
  //   finalizeStreaming 落定；reload 时由后端 _build_display_messages 累积全迭代文本。
  //   不再读 final_message.content。
  // - timeline：承载执行过程（步骤 + 工具调用），toolCalls/subagents/fileChanges
  //   统一归于此，不再单独展示。渲染权重按 isReadOnlyTimeline 自适应（见渲染处）。

  const displayOutputFiles: string[] = useMemo(() => {
    if (message.role !== 'assistant') return []
    const files = message.metadata?.outputFiles ?? []
    return files.filter((p): p is string => typeof p === 'string' && p.trim().length > 0)
  }, [message.metadata?.outputFiles, message.role])

  const feedback = message.metadata?.feedback ?? null
  const upActive = feedback === 'up'
  const downActive = feedback === 'down'

  // ─── 导出菜单（三点点 MoreHorizontal） ───────────────────────────────
  const [exportMenuOpen, setExportMenuOpen] = useState(false)
  const [isPreparingWord, setIsPreparingWord] = useState(false)
  const [isPreparingPdf, setIsPreparingPdf] = useState(false)
  const exportMenuRef = useRef<HTMLDivElement | null>(null)

  // 外部点击关闭 + Esc 关闭（只有菜单打开时注册，减少全局监听器数量）
  useEffect(() => {
    if (!exportMenuOpen) return
    const onDocMouseDown = (e: MouseEvent) => {
      if (!exportMenuRef.current?.contains(e.target as Node)) setExportMenuOpen(false)
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setExportMenuOpen(false)
    }
    // 下一帧注册，避免触发本次鼠标点击立刻又被 document 捕获造成 self-close
    const t = window.setTimeout(() => {
      document.addEventListener('mousedown', onDocMouseDown)
      document.addEventListener('keydown', onKeyDown)
    }, 0)
    return () => {
      window.clearTimeout(t)
      document.removeEventListener('mousedown', onDocMouseDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [exportMenuOpen])

  const handleExportWord = useCallback(async () => {
    try {
      setIsPreparingWord(true)
      await exportAnswerAsWord(message, questionText)
    } finally {
      setIsPreparingWord(false)
      setExportMenuOpen(false)
    }
  }, [message, questionText])

  const handleExportPdf = useCallback(async () => {
    try {
      setIsPreparingPdf(true)
      await exportAnswerAsPdf(message, questionText)
    } finally {
      setIsPreparingPdf(false)
      setExportMenuOpen(false)
    }
  }, [message, questionText])

  const handleDownloadAll = async () => {
    // 无交付物时什么都不做：此按钮只对本条消息产出的交付物生效。此前这里会退化成
    // 不传 file_paths 的调用，后端于是把**整个工作区** outputs/ 打包下载（实测
    // 47 个文件 / 883KB），与该轮产出无关。
    if (!workspaceId || !token || displayOutputFiles.length === 0) return
    setIsDownloadingAll(true)
    setDownloadAllError(null)

    // 只导出本条消息（当前轮次）产出的交付物，而非整个工作区 outputs/。
    // 后端 /api/files/export 支持 file_paths 参数（传了就只打包指定文件）；
    // 单个交付物走 /api/files/download 直下（免解压），多个则打包为 zip。
    if (displayOutputFiles.length === 1) {
      // a.href 是浏览器导航，无法携带 Authorization 头，必须把 token 放进查询串
      // 才能过后端鉴权（与 useFilePreviewController.rawFileUrl、下方 fallback 同口径）。
      // 此前漏传 _token，/api/files/download 直接 401，单文件「下载全部」静默失败。
      const singleUrl = `${outputDownloadUrl(workspaceId, displayOutputFiles[0])}&_token=${encodeURIComponent(token)}`
      const a = document.createElement('a')
      a.href = singleUrl
      a.download = ''
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      setIsDownloadingAll(false)
      return
    }

    const params = new URLSearchParams({ workspace_id: workspaceId })
    for (const p of displayOutputFiles) params.append('file_paths', p)
    const path = `/api/files/export?${params.toString()}`
    const url = apiUrl(path)

    try {
      const res = await fetch(url, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) {
        const text = await res.text()
        let detail = `导出失败 (${res.status})`
        if (res.status === 404) {
          detail = '工作区暂无输出文件'
        } else if (text) {
          try {
            const json = JSON.parse(text)
            detail = json.detail || detail
          } catch {
            detail = text || detail
          }
        }
        setDownloadAllError(detail)
        return
      }
      const blob = await res.blob()
      const blobUrl = URL.createObjectURL(blob)
      // 名字体现本轮交付物（数量 + 时间），与后端 Content-Disposition 的命名规则一致。
      // blob: 下载只能靠 a.download 定名，服务端 header 在这一支不生效。
      const now = new Date()
      const pad = (n: number) => String(n).padStart(2, '0')
      const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`
      const a = document.createElement('a')
      a.href = blobUrl
      a.download = `交付物-${displayOutputFiles.length}个-${stamp}.zip`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(blobUrl)
    } catch (err) {
      // 网络级错误（CORS、连接拒绝、DNS 失败等）— 尝试 fallback：新窗口直接打开
      console.error('[DownloadAll] fetch failed', { url, err })
      try {
        // 将 token 附加到 URL 作为 fallback 认证方式
        const fallbackUrl = url.includes('?')
          ? `${url}&_token=${encodeURIComponent(token)}`
          : `${url}?_token=${encodeURIComponent(token)}`
        window.open(fallbackUrl, '_blank', 'noopener,noreferrer')
        setDownloadAllError(null)
        return
      } catch {
        // 如果连 window.open 都失败
      }
      const msg = err instanceof Error ? err.message : '网络错误，请重试'
      setDownloadAllError(`下载失败: ${msg}`)
    } finally {
      setIsDownloadingAll(false)
    }
  }

  return (
    <div className={`flex w-full mb-6 ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div className={`flex flex-col ${isUser ? 'max-w-[80%] items-end' : 'w-full items-start'} min-w-0`}>
        {isUser && renderedAttachments.length > 0 && (() => {
          // 把附件分成两组：图片 → 整组给 ImageGrid（横向 flex-wrap），
          // 非图片 → 逐个渲染（文件卡片较高，保持纵向堆叠）。
          // 之前每张图单独 <ImageGrid images={[img]}> 导致 ImageGrid 的横向布局
          // 完全没发挥作用；外层容器 flex-col 又强制纵向排列 → 多图必然上下堆叠。
          const images: ImageBlock[] = []
          // 源文档卡（被分解展示的 Word/PDF 本身）单独成组，渲染在图片之前作表头；
          // 普通附件卡仍按原逻辑排在图片之后。
          const sourceCards: Array<UploadedAttachmentDisplay | FileBlock> = []
          const files: Array<UploadedAttachmentDisplay | FileBlock> = []
          for (const att of renderedAttachments) {
            if ('type' in att && att.type === 'image') {
              images.push(att as ImageBlock)
            } else if ('is_source_document' in att && att.is_source_document) {
              sourceCards.push(att as UploadedAttachmentDisplay | FileBlock)
            } else {
              files.push(att as UploadedAttachmentDisplay | FileBlock)
            }
          }
          const renderFileCard = (a: any, index: number) => {
            const ext = ('ext' in a ? a.ext : ((a.name || a.path).split('.').pop() || 'FILE')).toUpperCase()
            const clickable = Boolean(onOpenFilePreview || onOpenFile)
            const handlePreview = () => {
              if (onOpenFilePreview) onOpenFilePreview(a.path)
              else onOpenFile?.(a.path)
            }
            return (
              <div
                key={`${a.path}:${a.name}:${index}`}
                role={clickable ? 'button' : undefined}
                tabIndex={clickable ? 0 : undefined}
                onClick={clickable ? handlePreview : undefined}
                onKeyDown={(e) => {
                  if (!clickable) return
                  if (e.key === 'Enter' || e.key === ' ') handlePreview()
                }}
                className={`w-[315px] max-w-full rounded-[var(--radius-card)] border border-[var(--c-border)] bg-[var(--c-surface)]
                           px-3.5 py-3 flex items-center gap-3 ${clickable ? 'hover:border-[var(--c-border)]/70 cursor-pointer transition-colors' : ''}`}
                title={a.name}
              >
                <FileIcon filename={a.name} size={32} className="shrink-0 rounded-[var(--radius-md)]" />
                <div className="min-w-0 flex-1">
                  <div className="text-[length:var(--fs-sm)] font-semibold text-[var(--c-text)] truncate leading-tight" title={a.name}>
                    {a.name}
                  </div>
                  <div className="mt-1 text-[length:var(--fs-sm)] font-medium text-[var(--c-text-secondary)] tracking-wide opacity-60 uppercase">
                    {ext}
                  </div>
                </div>
              </div>
            )
          }
          return (
            <div className={`flex flex-col gap-2 mb-2 ${isUser ? 'items-end' : 'items-start'}`}>
              {sourceCards.map((a, index) => renderFileCard(a, index))}
              {images.length > 0 && (
                <ImageGrid
                  images={images}
                  alignRight={isUser}
                  workspaceId={workspaceId}
                  token={token}
                  onPreviewImage={images.every((img) => Boolean(onOpenFilePreview && img.source_path))
                    ? (path: string) => onOpenFilePreview!(path)
                    : undefined}
                />
              )}
              {files.map((a, index) => renderFileCard(a, index))}
            </div>
          )
        })()}

        {(displayText.length > 0 || (!isUser && isStreaming)) && (
          <>
            <div
              className={
                isUser
                  ? 'rounded-[var(--radius-card)] rounded-tr-sm px-4.5 py-3 bg-[var(--c-accent)]/10'
                  : 'w-full'
              }
            >
              {isUser ? (
                hasMarkers(displayText) ? (
                  // 正文含内联卡片（技能/专家/文件）：按标记分段渲染成卡片 + 文字，
                  // 普通文本消息仍走 Markdown 渲染，避免丢失原有排版。
                  <div className="whitespace-pre-wrap break-words text-[length:var(--fs-md)] font-medium leading-[1.75] text-[var(--c-text)]">
                    <InlineChips text={displayText} />
                  </div>
                ) : (
                  <MarkdownRenderer key={`user-${message.id}`} content={displayText} className="break-words text-[length:var(--fs-md)] font-medium leading-[1.75] text-[var(--c-text)]" />
                )
              ) : (
                <div className="text-[length:var(--fs-md)] leading-[1.9] text-[var(--c-text)]">
                  {/* timeline 与正文共存（流式吐字 + 执行过程同时可见）。
                      渲染权重 = isReadOnlyTimeline(message.timeline)：纯 read/search
                      → light（紧凑内联）；含思考/写入/技能/失败 → heavy（含完成行与
                      连接线）。权重只是 ExecutionTimeline 的入参，不再切换组件——
                      组件对调会替换 DOM 根节点、重播入场动画，正是「页面突然切换」
                      的根因。 */}
                  {/* isNarrating = 正文已吐字 且 当前无工具在执行。
                      中途草稿文本（单遍流式）也会进 streamingText——若只看
                      displayText.length>0，工具执行阶段（phase='executing'，
                      可能持续数分钟）会误显「正在组织回答...」，让用户以为任务
                      快结束。后端在工具执行期间发 HeartbeatEvent(phase=
                      'executing')（agent_loop.py），以此为准降级回
                      「任务正在执行」。phase 未到时（null）维持原行为。 */}
                  {message.timeline && message.timeline.length > 0 && (
                    <ExecutionTimeline
                      nodes={message.timeline}
                      weight={isReadOnlyTimeline(message.timeline) ? 'light' : 'heavy'}
                      isExecuting={isStreaming}
                      isNarrating={isStreaming && displayText.length > 0 && phase?.state !== 'executing'}
                      runGate={runGate}
                      elapsedS={elapsedS}
                      className="mb-4"
                    />
                  )}
                  {displayText.length > 0 ? (
                    isStreaming ? (
                      <StreamingMarkdown key={`streaming-${message.id}`} content={displayText} className="break-words px-1" />
                    ) : (
                      <MarkdownRenderer key={`final-${message.id}`} content={displayText} className="break-words px-1" />
                    )
                  ) : null}

                {!isStreaming && (renderedAttachments.length > 0 || displayOutputFiles.length > 0) ? (
                  <div className="mt-6 px-3">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-[length:var(--fs-md)] font-medium text-[var(--c-text-secondary)]/70">产物输出</span>
                      {/* 仅当本条消息确有交付物时才提供下载入口：只有内联附件时
                          无可下载的文件，此前按钮仍显示且会误触发整工作区导出。 */}
                      {workspaceId && token && displayOutputFiles.length > 0 && (
                        <div className="flex flex-col items-end gap-1">
                          <button
                            type="button"
                            onClick={handleDownloadAll}
                            disabled={isDownloadingAll}
                            className="btn btn-sm btn-ghost" data-state="accent"
                            title="下载全部输出文件"
                            aria-label="下载全部输出文件"
                          >
                            <Download size={12} />
                            <span>{isDownloadingAll ? '下载中...' : '下载全部'}</span>
                          </button>
                          {downloadAllError && (
                            <span className="text-[length:var(--fs-sm)] text-[var(--c-danger)] animate-in fade-in slide-in-from-top-1 duration-200">
                              {downloadAllError}
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                    <div className="grid grid-cols-5 gap-2">
                      {renderedAttachments.map((attachment, index) => {
                        if ('type' in attachment && attachment.type === 'image') {
                          return (
                            <div key={`out:image:${index}`} className="sm:col-span-2">
                              <ImageGrid images={[attachment]} alignRight={false} workspaceId={workspaceId} token={token} />
                            </div>
                          )
                        }
                        const a = attachment as any
                        const name = (a.name || basename(a.path) || 'file').trim()
                        const extRaw = (name.split('.').pop() || '').trim()
                        const ext = extRaw ? extRaw.toUpperCase() : ''
                        const clickable = Boolean(onOpenFilePreview || onOpenFile)
                        const handlePreview = () => {
                          if (onOpenFilePreview) {
                            onOpenFilePreview(a.path)
                          } else {
                            onOpenFile?.(a.path)
                          }
                        }
                        return (
                          <div
                            key={`out:${a.path}:${a.name}:${index}`}
                            role="button"
                            tabIndex={clickable ? 0 : -1}
                            onClick={handlePreview}
                            onKeyDown={(e) => {
                              if (!clickable) return
                              if (e.key === 'Enter' || e.key === ' ') handlePreview()
                            }}
                            className={`col-span-2 w-full min-w-0 rounded-[var(--radius-card)] border border-[var(--c-divider)] bg-[var(--c-surface-2)]
                                       px-3.5 py-3.5 shadow-sm flex items-center gap-3 text-left group/attach
                                       ${clickable ? 'hover:border-[var(--c-border-strong)] cursor-pointer transition-colors' : 'cursor-default'}`}
                            title={name}
                          >
                            <FileIcon filename={name} size={28} className="shrink-0" />
                            <div className="min-w-0 flex-1">
                              <div className="text-[length:var(--fs-md)] font-medium text-[var(--c-text)] truncate leading-tight">{name}</div>
                              {ext && <div className="mt-0.5 text-[length:var(--fs-sm)] leading-none text-[var(--c-text-secondary)]">{ext}</div>}
                            </div>
                          </div>
                        )
                      })}

                      {displayOutputFiles.map((p) => {
                        const name = basename(p) || p
                        const extRaw = (name.split('.').pop() || '').trim()
                        const ext = extRaw ? extRaw.toUpperCase() : ''
                        const clickable = Boolean(onOpenFilePreview || onOpenFile)
                        const handlePreview = () => {
                          if (onOpenFilePreview) {
                            onOpenFilePreview(p)
                          } else {
                            onOpenFile?.(p)
                          }
                        }
                        return (
                          <div
                          key={`outpath:${p}`}
                          role="button"
                          tabIndex={clickable ? 0 : -1}
                          onClick={handlePreview}
                          onKeyDown={(e) => {
                            if (!clickable) return
                            if (e.key === 'Enter' || e.key === ' ') handlePreview()
                          }}
                          className={`col-span-2 w-full min-w-0 rounded-[var(--radius-card)] border border-[var(--c-divider)] bg-[var(--c-surface-2)]
                                     px-3.5 py-3.5 shadow-sm flex items-center gap-3 text-left group/attach
                                     ${clickable ? 'hover:border-[var(--c-border-strong)] cursor-pointer transition-colors' : 'cursor-default'}`}
                          title={name}
                        >
                          <FileIcon filename={name} size={32} className="shrink-0" />
                          <div className="min-w-0 flex-1">
                            <div className="text-[length:var(--fs-md)] font-medium text-[var(--c-text)] truncate leading-tight">{name}</div>
                            {ext && <div className="mt-0.5 text-[length:var(--fs-sm)] leading-none text-[var(--c-text-secondary)]">{ext}</div>}
                          </div>
                        </div>
                        )
                      })}
                    </div>
                  </div>
                ) : null}

                {!isStreaming ? (
                  <div className="mt-4 flex items-center gap-1">
                    <button
                      type="button"
                      className="btn btn-icon btn-ghost"
                      title={copyOk ? '已复制' : '复制'}
                      onClick={async () => {
                        try {
                          await onCopy?.(message)
                          setCopyOk(true)
                          window.setTimeout(() => setCopyOk(false), 1200)
                        } catch {}
                      }}
                    >
                      {copyOk ? <Check size={14} /> : <Copy size={14} />}
                    </button>

                    <button
                      type="button"
                      className="btn btn-icon btn-ghost"
                      aria-pressed={upActive}
                      title={upActive ? '取消赞同' : '赞同'}
                      onClick={() => {
                        try {
                          void onFeedback?.(message.id, upActive ? null : 'up')
                        } catch {}
                      }}
                    >
                      <ThumbsUp size={14} fill={upActive ? 'currentColor' : 'none'} />
                    </button>

                    <button
                      type="button"
                      className="btn btn-icon btn-ghost"
                      aria-pressed={downActive}
                      data-state={downActive ? 'error' : undefined}
                      title={downActive ? '取消反对' : '反对'}
                      onClick={() => {
                        try {
                          void onFeedback?.(message.id, downActive ? null : 'down')
                        } catch {}
                      }}
                    >
                      <ThumbsDown size={14} fill={downActive ? 'currentColor' : 'none'} />
                    </button>

                    <button
                      type="button"
                      className="btn btn-icon btn-ghost"
                      title={shareOk ? '已准备分享' : '分享'}
                      onClick={async () => {
                        try {
                          await onShare?.(message)
                          setShareOk(true)
                          window.setTimeout(() => setShareOk(false), 1200)
                        } catch {}
                      }}
                    >
                      {shareOk ? <Check size={14} /> : <Share2 size={14} />}
                    </button>

                    {/* ─── 导出菜单（三个点）：放在 Share 之后、Clock 之前 ─── */}
                    <div ref={exportMenuRef} className="relative">
                      <button
                        type="button"
                        aria-label="导出"
                        aria-haspopup="menu"
                        aria-expanded={exportMenuOpen}
                        title="导出"
                        onClick={() => setExportMenuOpen((v) => !v)}
                        className="btn btn-icon btn-ghost"
                      >
                        <MoreHorizontal size={14} />
                      </button>

                      {exportMenuOpen ? (
                        <div
                          role="menu"
                          aria-label="导出菜单"
                          className="overlay-panel absolute right-0 bottom-full z-[60] mb-1 min-w-[170px] overflow-hidden rounded-[var(--radius-card)]"
                        >
                          <button
                            type="button"
                            role="menuitem"
                            disabled={isPreparingWord}
                            onMouseDown={(e) => e.preventDefault()}
                            onClick={handleExportWord}
                            className={`flex w-full items-center gap-2 rounded-t-[7px] px-3 py-1.5 text-left text-[length:var(--fs-sm)] text-[var(--c-text-primary)]/90 hover:bg-[var(--c-surface-2)]/70 transition-colors ${
                              isPreparingWord ? 'opacity-50 cursor-not-allowed' : ''
                            }`}
                          >
                            {isPreparingWord ? (
                              <Loader2 size={14} className="shrink-0 text-[var(--c-accent)] animate-spin" />
                            ) : (
                              <FileText size={14} className="shrink-0 text-[var(--c-text-secondary)]/70" />
                            )}
                            <span>{isPreparingWord ? '准备中…' : '下载为 Word'}</span>
                          </button>
                          <button
                            type="button"
                            role="menuitem"
                            disabled={isPreparingPdf}
                            onMouseDown={(e) => e.preventDefault()}
                            onClick={handleExportPdf}
                            className={`flex w-full items-center gap-2 rounded-b-[7px] px-3 py-1.5 text-left text-[length:var(--fs-sm)] text-[var(--c-text-primary)]/90 hover:bg-[var(--c-surface-2)]/70 transition-colors ${
                              isPreparingPdf ? 'opacity-50 cursor-not-allowed' : ''
                            }`}
                          >
                            {isPreparingPdf ? (
                              <Loader2 size={14} className="shrink-0 text-[var(--c-accent)] animate-spin" />
                            ) : (
                              <Download size={14} className="shrink-0 text-[var(--c-text-secondary)]/70" />
                            )}
                            <span>{isPreparingPdf ? '准备中…' : '下载为 PDF'}</span>
                          </button>
                        </div>
                      ) : null}
                    </div>

                    {/* 执行时长徽标：chat.done 时由 useChatSocket 写入 metadata.durationMs；
                        后端 _build_display_messages 刷新重放时回填同字段。 */}
                    {message.metadata?.durationMs && message.metadata.durationMs > 0 ? (
                      <span
                        className="ml-1 inline-flex items-center gap-1 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/50 select-none"
                        title="运行时长"
                      >
                        <Clock size={12} />
                        <span>{formatDuration(message.metadata.durationMs)}</span>
                      </span>
                    ) : null}
                  </div>
                ) : null}

                {!isStreaming && showSupplementalSections && (followupQuestions?.length ?? 0) > 0 ? (
                  <div className="mt-4 rounded-[var(--radius-card)] bg-[var(--c-accent)]/5 px-4 py-3.5">
                    <div className="flex items-center gap-2 text-[length:var(--fs-sm)] font-medium text-[var(--c-text-secondary)]">
                      <CornerDownRight size={14} className="text-[var(--c-accent)]" />
                      <span>继续问</span>
                    </div>
                    <div className="mt-2 space-y-1">
                      {(followupQuestions ?? []).slice(0, 3).map((q, idx) => (
                        <button
                          key={`${idx}-${q}`}
                          type="button"
                          onClick={() => onQuickReply?.(q)}
                          disabled={!onQuickReply}
                          className="w-full px-4 py-2 text-left flex items-center justify-between gap-3 rounded-[var(--radius-control)]
                                     hover:bg-[var(--c-surface-2)]/60 hover:shadow-[var(--shadow-sm)] active:scale-[0.995] transition-all cursor-pointer
                                     disabled:opacity-60 disabled:cursor-default"
                        >
                          <span className="text-[length:var(--fs-sm)] text-[var(--c-text)]">{q}</span>
                          <ChevronRight size={14} className="text-[var(--c-text-secondary)]/60 shrink-0" />
                        </button>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
            )}

            {isStreaming && (
              <span className="typing-dots inline-flex items-center gap-1.5 mt-2 ml-2 align-baseline">
                <span className="typing-dot inline-block w-1.5 h-1.5 rounded-full bg-[var(--c-text)]/40 animate-[bounce_1s_infinite_0ms]" />
                <span className="typing-dot inline-block w-1.5 h-1.5 rounded-full bg-[var(--c-text)]/40 animate-[bounce_1s_infinite_200ms]" />
                <span className="typing-dot inline-block w-1.5 h-1.5 rounded-full bg-[var(--c-text)]/40 animate-[bounce_1s_infinite_400ms]" />
              </span>
            )}

            </div>

            {isUser && !isStreaming ? (
              <div className="mt-2 flex items-center justify-end gap-3 text-[length:var(--fs-sm)] font-normal text-[var(--c-text-secondary)]/50">
                <span>{formatTimestamp(message.timestamp)}</span>
                <button
                  type="button"
                  className="btn btn-icon btn-ghost"
                  title={copyOk ? '已复制' : '复制'}
                  onClick={async () => {
                    try {
                      await onCopy?.(message)
                      setCopyOk(true)
                      window.setTimeout(() => setCopyOk(false), 1200)
                    } catch {}
                  }}
                >
                  {copyOk ? <Check size={12} /> : <Copy size={12} />}
                </button>
              </div>
            ) : null}
          </>
        )}
      </div>

      </div>
  )
}
