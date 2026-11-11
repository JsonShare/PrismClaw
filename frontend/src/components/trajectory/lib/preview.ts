/**
 * preview.ts — Bounded Markdown-to-text projection shared by trajectory
 * consumers.
 *
 * Ported from DSH:
 *   - packages/client/ui-trajectory/src/client/trajectory-preview.ts
 *   - packages/client/ui-primitives/src/markdown/plain-text.ts
 *
 * DSH parses with its own streaming GFM grammar (parseGfm); PrismClaw
 * reuses `marked` (already a project dependency) and walks its token tree
 * with DSH's exact extraction semantics: links keep their labels, images
 * keep alt text, code keeps its source text, raw HTML stays literal, and
 * thematic breaks/definitions disappear.
 */

import { marked, type Token, type Tokens } from 'marked'

const PREVIEW_SOURCE_CHARACTERS = 2_048
const PREVIEW_OUTPUT_CHARACTERS = 512

function compactInline(text: string): string {
  return text.replace(/\s+/g, ' ').trim()
}

/** Inline token text — DSH inlineText semantics over marked token shapes. */
function inlineText(token: Token): string {
  switch (token.type) {
    case 'text':
    case 'escape':
    case 'codespan':
    case 'tag':
      // marked inline html arrives as 'tag' with the literal markup.
      return (token as Tokens.Text | Tokens.Escape | Tokens.Codespan).text ?? ''
    case 'image':
      // marked images carry the alt text in .text.
      return (token as Tokens.Image).text ?? ''
    case 'br':
      return '\n'
    case 'html':
      return (token as Tokens.HTML).text ?? ''
    default: {
      // strong / em / del / link etc. → join their children.
      const children = (token as Tokens.Strong | Tokens.Em | Tokens.Del | Tokens.Link).tokens
      return children?.map(inlineText).join('') ?? ''
    }
  }
}

/** Block token text — DSH blockText semantics over marked token shapes. */
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
      // marked rows are TableCell[][] (no table_row token): cells join with
      // tabs, rows with newlines — DSH tableRow/tableCell semantics.
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
      // Block-level 'text' token: flatten inline children when present.
      return compactInline(inlineText(token))
  }
}

/**
 * Parse GFM Markdown, remove its presentation markup, and preserve raw HTML
 * literally (DSH extractMarkdownPlainText, mode 'all').
 * @param markdown - Markdown source.
 * @returns Plain text for the whole document.
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
 * Build a bounded one-line preview without parsing the complete Markdown
 * document (DSH trajectoryPreviewText).
 * @param text - Untrusted message, reasoning, payload, or result text.
 * @returns A compact preview capped independently from the retained source.
 */
export function trajectoryPreviewText(text: string): string {
  const source = text.slice(0, PREVIEW_SOURCE_CHARACTERS)
  const compact = extractMarkdownPlainText(source).replace(/\s+/g, ' ').trim()
  const preview = compact.slice(0, PREVIEW_OUTPUT_CHARACTERS).trimEnd()
  return source.length < text.length || preview.length < compact.length
    ? `${preview}…`
    : preview
}
