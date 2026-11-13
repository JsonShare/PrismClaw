/**
 * preview.ts — 有界 Markdown→纯文本投影，供轨迹模块共享。
 *
 * 使用 `marked`（项目已有依赖）解析 GFM 语法并遍历 token 树：
 * 链接保留其 label、图片保留 alt 文本、代码块保留源文本、
 * 原始 HTML 保持字面量、分隔线/定义项被丢弃。
 */

import { marked, type Token, type Tokens } from 'marked'

const PREVIEW_SOURCE_CHARACTERS = 2_048
const PREVIEW_OUTPUT_CHARACTERS = 512

function compactInline(text: string): string {
  return text.replace(/\s+/g, ' ').trim()
}

/** 内联 token 的文本投影。 */
function inlineText(token: Token): string {
  switch (token.type) {
    case 'text':
    case 'escape':
    case 'codespan':
    case 'tag':
      // marked 内联 HTML 以字面量 'tag' 到达
      return (token as Tokens.Text | Tokens.Escape | Tokens.Codespan).text ?? ''
    case 'image':
      // marked 图片的 alt 文本在 .text
      return (token as Tokens.Image).text ?? ''
    case 'br':
      return '\n'
    case 'html':
      return (token as Tokens.HTML).text ?? ''
    default: {
      // strong / em / del / link 等 → 拼接子节点
      const children = (token as Tokens.Strong | Tokens.Em | Tokens.Del | Tokens.Link).tokens
      return children?.map(inlineText).join('') ?? ''
    }
  }
}

/** 块级 token 的文本投影。 */
function blockText(token: Token): string {
  switch (token.type) {
    case 'blockquote':
      return (token as Tokens.Blockquote).tokens
        ?.map(blockText).filter(Boolean).join('\n\n') ?? ''
    case 'paragraph':
    case 'heading':
      return compactInline((token as Tokens.Paragraph | Tokens.Heading).tokens
        ?.map(inlineText).join('') ?? '')
    case 'code':
      return (token as Tokens.Code).text?.trim() ?? ''
    case 'list':
      return (token as Tokens.List).items
        ?.map(blockText).filter(Boolean).join('\n') ?? ''
    case 'list_item':
      return (token as Tokens.ListItem).tokens
        ?.map(blockText).filter(Boolean).join(' ') ?? ''
    case 'table':
      // marked 的 table 行是 TableCell[][]（没有 table_row token）：
      // 单元格用 tab 拼接，行之间用换行
      return (token as Tokens.Table).rows
        ?.map(cells => cells.map(cell => blockText(cell as unknown as Token)).join('\t'))
        .filter(Boolean)
        .join('\n') ?? ''
    case 'html':
      return (token as Tokens.HTML).text ?? ''
    case 'hr':
    case 'space':
    case 'def':
      return ''
    default:
      // 块级 'text' token：内联展平
      return compactInline(inlineText(token))
  }
}

/**
 * 解析 GFM Markdown，去掉展示用标记，保留原始 HTML 字面量，
 * 输出整篇文档的纯文本。
 * @param markdown - Markdown 源文本
 * @returns 整篇文档的纯文本
 */
function extractMarkdownPlainText(markdown: string): string {
  const tokens = marked.lexer(markdown)
  return tokens
    .map(blockText)
    .filter(Boolean)
    .join('\n\n')
    .split('\n')
    .map(line => line.trim())
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

/**
 * 构建有界单行预览，不解析完整 Markdown 文档。
 * @param text - 不受信任的消息、推理、payload 或结果文本
 * @returns 紧凑预览，独立于保留的源文本截断
 */
export function trajectoryPreviewText(text: string): string {
  const source = text.slice(0, PREVIEW_SOURCE_CHARACTERS)
  const compact = extractMarkdownPlainText(source).replace(/\s+/g, ' ').trim()
  const preview = compact.slice(0, PREVIEW_OUTPUT_CHARACTERS).trimEnd()
  return source.length < text.length || preview.length < compact.length
    ? `${preview}…`
    : preview
}
