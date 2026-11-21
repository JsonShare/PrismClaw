// @ts-ignore - .mjs file without .d.ts
import { segmentStreamGraphemes } from '../../stream-reveal.mjs';

const CJK_AFTER_STRONG = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]/u;
const STREAM_REVEAL_EXCLUDED_TAGS = new Set(['code', 'pre', 'table', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th', 'svg', 'math', 'video', 'audio', 'iframe', 'canvas']);

export type PreviewItem = { src: string; title?: string; label?: string };
export type PreviewSpec = PreviewItem & { items?: PreviewItem[] };
export type DataValue = string | number | boolean | null;
export type ColumnDef = { key: string; label?: string; type?: string };
export type DataSpec = { title?: string; filename?: string; sheetName?: string; columns?: Array<string | ColumnDef>; rows?: Array<DataValue[] | Record<string, DataValue>>; src?: string; groupBy?: string };

function fenceMarker(line: string): string {
  const match = line.match(/^\s*(`{3,}|~{3,})/);
  return match?.[1] || '';
}

export function hasUnclosedMarkdownFence(content: string): boolean {
  let fence = '';
  for (const line of String(content || '').split(/\r?\n/)) {
    const marker = fenceMarker(line);
    if (!marker) continue;
    if (!fence) {
      fence = marker;
      continue;
    }
    if (marker[0] === fence[0] && marker.length >= fence.length) fence = '';
  }
  return Boolean(fence);
}

const LIST_LINE_RE = /^\s{0,3}(?:[-*+]|\d{1,9}[.)])\s/;

export function splitStreamingMarkdown(content: string, streaming = false): string[] {
  if (!streaming) return [content];
  const lines = content.match(/.*(?:\n|$)/g)?.filter(Boolean) || [content];
  const chunks: string[] = [];
  let current = '';
  let fence = '';
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    current += line;
    const marker = fenceMarker(line);
    if (marker) {
      if (!fence) fence = marker;
      else if (marker[0] === fence[0] && marker.length >= fence.length) {
        fence = '';
        chunks.push(current);
        current = '';
      }
      continue;
    }
    if (fence || !/^\s*$/.test(line) || !current.trim()) continue;

    // 空行边界：只有「上一个非空行」和「下一个非空行」都不属于列表语境时才切块。
    // 否则一个松列表（项之间有空行）会被切成多个 <ol>，编号从 1 重新开始。
    const prev = lines.slice(0, index).reverse().find((row) => row.trim()) || '';
    const next = lines.slice(index + 1).find((row) => row.trim()) || '';
    const continuesList = LIST_LINE_RE.test(prev) || LIST_LINE_RE.test(next) || /^\s{2,}\S/.test(next);
    if (continuesList) continue;

    chunks.push(current);
    current = '';
  }
  if (current) chunks.push(current);
  return chunks.length ? chunks : [''];
}

export function markStreamRevealTail(tree: any, appendedGraphemes: number, revision: number): boolean {
  const requestedCount = Math.max(0, Number(appendedGraphemes) || 0);
  if (!tree || requestedCount === 0) return false;
  let lastText: any = null;
  const visit = (node: any, parent: any = null, index = -1, excluded = false) => {
    if (!node || typeof node !== 'object') return;
    const classNames = Array.isArray(node.properties?.className)
      ? node.properties.className
      : typeof node.properties?.className === 'string'
        ? node.properties.className.split(/\s+/)
        : [];
    const nextExcluded = excluded
      || STREAM_REVEAL_EXCLUDED_TAGS.has(String(node.tagName || '').toLowerCase())
      || classNames.some((name: string) => String(name).startsWith('katex') || String(name).startsWith('rich-'));
    if (node.type === 'text' && typeof node.value === 'string' && node.value.trim()) {
      lastText = { node, parent, index, excluded: nextExcluded };
    }
    if (Array.isArray(node.children)) {
      node.children.forEach((child: any, childIndex: number) => visit(child, node, childIndex, nextExcluded));
    }
  };
  visit(tree);
  if (!lastText || lastText.excluded || !lastText.parent || lastText.index < 0) return false;
  const graphemes = segmentStreamGraphemes(lastText.node.value);
  if (!graphemes.length) return false;
  const tailCount = Math.min(requestedCount, graphemes.length);
  const prefix = graphemes.slice(0, graphemes.length - tailCount).join('');
  const tail = graphemes.slice(-tailCount).join('');
  const replacement: any[] = [];
  if (prefix) replacement.push({ type: 'text', value: prefix });
  replacement.push({
    type: 'element',
    tagName: 'span',
    properties: {
      className: ['stream-reveal-tail'],
      dataStreamRevealRevision: String(revision),
    },
    children: [{ type: 'text', value: tail }],
  });
  lastText.parent.children.splice(lastText.index, 1, ...replacement);
  return true;
}

function splitCjkStrongText(value: string): any[] | null {
  const nodes: any[] = [];
  let cursor = 0;
  let index = 0;
  while (index < value.length - 3) {
    const marker = value.startsWith('**', index) ? '**' : value.startsWith('__', index) ? '__' : '';
    if (!marker) {
      index += 1;
      continue;
    }
    const close = value.indexOf(marker, index + 2);
    if (close <= index + 2) {
      index += 2;
      continue;
    }
    const next = value.slice(close + 2).match(/^./u)?.[0] || '';
    if (!CJK_AFTER_STRONG.test(next)) {
      index = close + 2;
      continue;
    }
    if (index > cursor) nodes.push({ type: 'text', value: value.slice(cursor, index) });
    nodes.push({ type: 'strong', children: [{ type: 'text', value: value.slice(index + 2, close) }] });
    cursor = close + 2;
    index = cursor;
  }
  if (!nodes.length) return null;
  if (cursor < value.length) nodes.push({ type: 'text', value: value.slice(cursor) });
  return nodes;
}

export function remarkCjkStrong() {
  const excludedParents = new Set(['code', 'inlineCode', 'link', 'linkReference', 'definition']);
  return (tree: any) => {
    const visit = (node: any) => {
      if (!node || !Array.isArray(node.children) || excludedParents.has(node.type)) return;
      for (let index = 0; index < node.children.length; index += 1) {
        const child = node.children[index];
        if (child?.type === 'text' && typeof child.value === 'string') {
          const replacement = splitCjkStrongText(child.value);
          if (replacement) {
            node.children.splice(index, 1, ...replacement);
            index += replacement.length - 1;
            continue;
          }
        }
        visit(child);
      }
    };
    visit(tree);
  };
}

// ---------------------------------------------------------------------------
// 状态类表情 → 软底线性图标。
// 只替换有明确语义的表情，语气表情（🙂😂🎉）保持原样；代码块、链接内的
// 文本不处理。替换只发生在渲染层，消息原文与复制内容不受影响。
// ---------------------------------------------------------------------------

const EMOJI_ICONS: Record<string, { icon: string; tone: string }> = {
  '✅': { icon: 'check-circle', tone: 'success' },
  '❌': { icon: 'x-circle', tone: 'danger' },
  '⚠': { icon: 'alert-triangle', tone: 'warning' },
  '💡': { icon: 'lightbulb', tone: 'info' },
  '🔍': { icon: 'search', tone: 'info' },
  '📄': { icon: 'file-text', tone: 'violet' },
};

const EMOJI_CHARS = Object.keys(EMOJI_ICONS);
// 非捕获组：所有候选字符都统一接受可选 VS16（\uFE0F）
const EMOJI_TEST_RE = new RegExp(`(?:${EMOJI_CHARS.join('|')})\\uFE0F?`);
// 捕获组用于提取 base emoji 键；g 标志配合 .exec() 循环；[ \\t]? 吞掉表情后一个空格/制表符
const EMOJI_SPLIT_RE = new RegExp(`(${EMOJI_CHARS.join('|')})\\uFE0F?[ \\t]?`, 'g');
const EMOJI_SKIP_TAGS = new Set(['code', 'pre', 'a', 'script', 'style', 'svg', 'math', 'kbd']);

function emojiNodes(value: string): any[] {
  const nodes: any[] = [];
  let cursor = 0;
  EMOJI_SPLIT_RE.lastIndex = 0;
  for (let match = EMOJI_SPLIT_RE.exec(value); match; match = EMOJI_SPLIT_RE.exec(value)) {
    if (match.index > cursor) nodes.push({ type: 'text', value: value.slice(cursor, match.index) });
    nodes.push({
      type: 'element',
      tagName: 'span',
      properties: {
        dataMdIcon: EMOJI_ICONS[match[1]].icon,
        dataMdTone: EMOJI_ICONS[match[1]].tone,
      },
      children: [],
    });
    cursor = match.index + match[0].length;
  }
  if (cursor < value.length) nodes.push({ type: 'text', value: value.slice(cursor) });
  return nodes;
}

export function rehypeEmojiIcons() {
  return (tree: any) => {
    const visit = (node: any, skip: boolean) => {
      if (!Array.isArray(node?.children)) return;
      const tag = String(node.tagName || '').toLowerCase();
      const nextSkip = skip || EMOJI_SKIP_TAGS.has(tag);
      const next: any[] = [];
      for (const child of node.children) {
        if (child?.type === 'text' && !nextSkip && typeof child.value === 'string' && EMOJI_TEST_RE.test(child.value)) {
          next.push(...emojiNodes(child.value));
          continue;
        }
        visit(child, nextSkip);
        next.push(child);
      }
      node.children = next;
    };
    visit(tree, false);
  };
}

// ---------------------------------------------------------------------------
// iframe HTML 路径 emoji 替换：对 HTML 字符串做正则替换，跳过 code/pre/script/style
// ---------------------------------------------------------------------------

// lucide 线性图标的内联 SVG（与 MarkdownEmojiIcon 使用的 lucide-react 组件一一对应）
const EMOJI_HTML_ICONS: Record<string, { icon: string; tone: string; svg: string }> = {
  '✅': {
    icon: 'check-circle',
    tone: 'success',
    svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><path d="m9 12 2 2 4-4"></path></svg>',
  },
  '❌': {
    icon: 'x-circle',
    tone: 'danger',
    svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><path d="m15 9-6 6"></path><path d="m9 9 6 6"></path></svg>',
  },
  '⚠': {
    icon: 'alert-triangle',
    tone: 'warning',
    svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"></path><path d="M12 9v4"></path><path d="M12 17h.01"></path></svg>',
  },
  '💡': {
    icon: 'lightbulb',
    tone: 'info',
    svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"></path><path d="M9 18h6"></path><path d="M10 22h4"></path></svg>',
  },
  '🔍': {
    icon: 'search',
    tone: 'info',
    svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><path d="m21 21-4.3-4.3"></path></svg>',
  },
  '📄': {
    icon: 'file-text',
    tone: 'violet',
    svg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"></path><path d="M14 2v4a2 2 0 0 0 2 2h4"></path><path d="M10 9H8"></path><path d="M16 13H8"></path><path d="M16 17H8"></path></svg>',
  },
};

const EMOJI_HTML_CHARS = Object.keys(EMOJI_HTML_ICONS);
const EMOJI_HTML_RE = new RegExp(`(${EMOJI_HTML_CHARS.join('|')})\\uFE0F?[ \\t]?`, 'g');
// 跳过的标签：code / pre / script / style，匹配开标签（含可能的属性）到对应闭标签
const HTML_SKIP_TAGS = ['code', 'pre', 'script', 'style'];

/**
 * 对 HTML 字符串做 emoji → 软底徽章内联 SVG 的替换。
 * 跳过 <code>、<pre>、<script>、<style> 标签内的内容。
 */
export function replaceEmojiIconsInHtml(html: string): string {
  let result = '';
  let cursor = 0;
  // 按 startIndex 排序所有 skip 区间
  type SkipRange = { start: number; end: number };
  const skipRanges: SkipRange[] = [];
  for (const tag of HTML_SKIP_TAGS) {
    const openRe = new RegExp(`<${tag}[\\s/>]`, 'gi');
    let match;
    openRe.lastIndex = 0;
    while ((match = openRe.exec(html)) !== null) {
      const tagStart = match.index;
      // 找到闭合标签 </tag>
      const closeRe = new RegExp(`</${tag}>`, 'gi');
      closeRe.lastIndex = openRe.lastIndex;
      const closeMatch = closeRe.exec(html);
      if (closeMatch) {
        skipRanges.push({ start: tagStart, end: closeMatch.index + closeMatch[0].length });
      } else {
        // 没有闭合标签，取到字符串末尾
        skipRanges.push({ start: tagStart, end: html.length });
      }
    }
  }
  skipRanges.sort((a, b) => a.start - b.start);

  // 遍历 emoji 匹配，落在 skip 区间里的跳过
  EMOJI_HTML_RE.lastIndex = 0;
  for (let match = EMOJI_HTML_RE.exec(html); match; match = EMOJI_HTML_RE.exec(html)) {
    const emojiStart = match.index;
    const emojiEnd = match.index + match[0].length;
    // 检查是否落在某个 skip 区间内
    const inSkip = skipRanges.some((r) => emojiStart >= r.start && emojiStart < r.end);
    if (inSkip) continue;
    // 追加从上次 cursor 到 emoji 起点的内容
    if (emojiStart > cursor) result += html.slice(cursor, emojiStart);
    const baseEmoji = match[1];
    const spec = EMOJI_HTML_ICONS[baseEmoji];
    if (spec) {
      result += `<span class="md-emoji" data-tone="${spec.tone}">${spec.svg}</span>`;
    } else {
      result += match[0];
    }
    cursor = emojiEnd;
  }
  if (cursor < html.length) result += html.slice(cursor);
  return result;
}

/** iframe 内联 CSS，与外层 .md-emoji 样式对齐 */
export const MD_EMOJI_IFRAME_CSS = `
.md-emoji{--md-emoji-color:#59636e;display:inline-flex;align-items:center;justify-content:center;width:1em;height:1em;margin-right:.25em;border-radius:.22em;background:color-mix(in srgb,var(--md-emoji-color) 16%,transparent);color:var(--md-emoji-color);vertical-align:-.12em;line-height:1}
.md-emoji>svg{width:.7em;height:.7em;display:block}
.md-emoji[data-tone="success"]{--md-emoji-color:var(--color-text-success)}
.md-emoji[data-tone="warning"]{--md-emoji-color:var(--color-text-warning)}
.md-emoji[data-tone="danger"]{--md-emoji-color:var(--color-text-danger)}
.md-emoji[data-tone="info"]{--md-emoji-color:var(--color-text-info)}
.md-emoji[data-tone="violet"]{--md-emoji-color:#7a4dd6}
@media (prefers-color-scheme:dark){.md-emoji[data-tone="violet"]{--md-emoji-color:#a78bfa}}`;

export function normalizePreviewSpec(source: string): PreviewSpec | null {
  let parsed: any;
  try {
    parsed = JSON.parse(source);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== 'object') return null;
  const items = Array.isArray(parsed.items)
    ? parsed.items.filter((item: any) => item && typeof item.src === 'string' && item.src.trim()).map((item: any) => ({ ...item, src: item.src.trim() }))
    : undefined;
  if (items?.length) return { ...parsed, items };
  if (typeof parsed.src !== 'string' || !parsed.src.trim()) return null;
  return { ...parsed, src: parsed.src.trim() };
}

// ---------------------------------------------------------------------------
// Normalize malformed markdown tables before GFM parsing.
// LLMs often emit tables with inconsistent column counts (e.g. 2-column header
// with a 4-column delimiter). remark-gfm strictly follows the GFM spec and
// rejects these blocks, leaving them as plain paragraphs. This function pads
// every row in a table block to the maximum column count so GFM can render it.
// ---------------------------------------------------------------------------

const TABLE_LINE_RE = /^\s*\|/;
const TABLE_DELIMITER_RE = /^\s*\|(?:\s*:?-+:?\s*\|)+\s*$/;

function splitTableRow(row: string): string[] {
  // Strip the optional leading/trailing pipe and surrounding whitespace.
  const inner = row.replace(/^\s*\|/, '').replace(/\|\s*$/, '');
  // Naive split on unescaped pipes. This matches the GFM lexer for the common
  // LLM-output case and intentionally does not try to parse nested code spans.
  return inner.split(/(?<!\\)\|/);
}

function normalizeTableBlock(block: string[]): string[] {
  if (block.length < 2) return block;
  const cellsPerRow = block.map(splitTableRow);
  const maxCols = Math.max(...cellsPerRow.map((c) => c.length));
  if (maxCols <= 0) return block;
  return block.map((row, rowIndex) => {
    const isDelimiter = TABLE_DELIMITER_RE.test(row);
    let cells = cellsPerRow[rowIndex];
    while (cells.length < maxCols) {
      cells = [...cells, isDelimiter ? '---' : ' '];
    }
    if (cells.length > maxCols) {
      if (isDelimiter) {
        cells = cells.slice(0, maxCols);
      } else {
        const head = cells.slice(0, maxCols - 1);
        const tail = cells.slice(maxCols - 1).join('|');
        cells = [...head, tail];
      }
    }
    const built = cells.map((c) => ` ${c.trim()} `).join('|');
    return `|${built}|`;
  });
}

export function normalizeMarkdownTables(content: string): string {
  const lines = content.split(/\r?\n/);
  const out: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (TABLE_LINE_RE.test(line)) {
      let block = [line];
      let j = i + 1;
      while (j < lines.length && TABLE_LINE_RE.test(lines[j])) {
        block.push(lines[j]);
        j++;
      }
      // Only treat consecutive pipe-prefixed lines as a table if at least one
      // delimiter row is present. This avoids touching plain text that happens
      // to start with a pipe.
      const hasDelimiter = block.some((r) => TABLE_DELIMITER_RE.test(r));
      if (hasDelimiter) {
        block = normalizeTableBlock(block);
      }
      out.push(...block);
      i = j;
      continue;
    }
    out.push(line);
    i++;
  }
  return out.join('\n');
}

function hastText(node: any): string {
  if (!node) return '';
  if (node.type === 'text') return String(node.value ?? '');
  return Array.isArray(node.children) ? node.children.map(hastText).join('') : '';
}

// ---------------------------------------------------------------------------
// Annotate table cells with their header text as `data-label`.
// Narrow screens turn multi-column tables into card lists (see index.css), but
// pure CSS cannot know which header a cell belongs to, so the label has to be
// baked into the HTML here.
// ---------------------------------------------------------------------------
export function rehypeTableLabels() {
  return (tree: any) => {
    const annotate = (table: any) => {
      const sections: any[] = table.children || [];
      const headRow = sections.find((section) => section.tagName === 'thead')?.children?.find((child: any) => child.tagName === 'tr');
      const labels: string[] = (headRow?.children || []).map((cell: any) => hastText(cell).trim());
      if (!labels.some(Boolean)) return;
      for (const section of sections) {
        for (const row of section.children || []) {
          if (row.tagName !== 'tr') continue;
          (row.children || []).forEach((cell: any, index: number) => {
            if (cell.tagName !== 'td' && cell.tagName !== 'th') return;
            const label = labels[index];
            if (!label) return;
            cell.properties = { ...(cell.properties || {}), dataLabel: label };
          });
        }
      }
    };
    const visit = (node: any) => {
      if (node?.tagName === 'table') annotate(node);
      if (Array.isArray(node?.children)) node.children.forEach(visit);
    };
    visit(tree);
  };
}

// ---------------------------------------------------------------------------
// 正文内联 HTML 白名单（配合 rehype-raw 使用）
// 模型输出视为不可信内容：只放行排版类标签，属性仅保留 class/title/href
// 和受限的 color；script/style/iframe/img 等直接丢弃，其余未知标签解包保文字。
// ---------------------------------------------------------------------------

// 必须包含所有「Markdown 语法会生成的结构标签」：白名单里没有的标签会被解包成纯文本，
// 导致表格/列表/引用/标题这些由 markdown 本身产生的元素一并失去语义。
const SAFE_HTML_TAGS = new Set([
  'a', 'abbr', 'b', 'br', 'cite', 'code', 'del', 'details', 'div', 'em', 'hr', 'i',
  'ins', 'kbd', 'mark', 'p', 'q', 's', 'small', 'span', 'strong', 'sub', 'summary', 'sup', 'u',
  // 结构标签（由 markdown 生成，不是模型手写 HTML）
  'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'pre',
  'table', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th', 'caption', 'figure', 'figcaption',
]);
const DROP_HTML_TAGS = new Set([
  'script', 'style', 'iframe', 'object', 'embed', 'link', 'meta', 'base', 'form',
  'button', 'textarea', 'select', 'option', 'svg', 'math', 'video', 'audio', 'canvas',
  'template', 'noscript', 'img', 'picture', 'source', 'frame', 'frameset',
]);
const HTML_COLOR_RE = /^(#[0-9a-f]{3,8}|rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(?:,\s*(?:0|1|0?\.\d+)\s*)?\)|[a-z]{3,20})$/i;
const HTML_CLASS_RE = /^[a-z0-9_-]{1,40}$/i;
const HTML_HREF_RE = /^(https?:|mailto:)/i;
const HTML_STYLE_PROPS = new Set(['color', 'background-color']);

function sanitizeHtmlColor(value: unknown): string {
  const color = typeof value === 'string' ? value.trim() : '';
  return color && HTML_COLOR_RE.test(color) ? color : '';
}

function sanitizeHtmlStyle(value: unknown): string {
  if (typeof value !== 'string') return '';
  const kept: string[] = [];
  for (const declaration of value.split(';')) {
    const index = declaration.indexOf(':');
    if (index < 0) continue;
    const prop = declaration.slice(0, index).trim().toLowerCase();
    const val = declaration.slice(index + 1).trim().toLowerCase();
    if (!HTML_STYLE_PROPS.has(prop)) continue;
    if (!HTML_COLOR_RE.test(val)) continue;
    kept.push(`${prop}: ${val}`);
  }
  return kept.join('; ');
}

function sanitizeHtmlProperties(raw: any): Record<string, unknown> {
  const props = raw || {};
  const out: Record<string, unknown> = {};
  const classes = (Array.isArray(props.className) ? props.className : String(props.className || '').split(/\s+/))
    .map((item: unknown) => String(item))
    .filter((item: string) => HTML_CLASS_RE.test(item))
    .slice(0, 4);
  if (classes.length) out.className = classes;
  if (typeof props.title === 'string' && props.title.length <= 200) out.title = props.title;
  const href = typeof props.href === 'string' ? props.href.trim() : '';
  if (href && HTML_HREF_RE.test(href)) out.href = href;
  const style = sanitizeHtmlStyle(props.style);
  const color = sanitizeHtmlColor(props.color);
  const merged = [style, color ? `color: ${color}` : ''].filter(Boolean).join('; ');
  if (merged) out.style = merged;
  // GFM 表格对齐、有序列表起始序号都是语法自带的属性，不能一并剥掉
  if (typeof props.start === 'number' && props.start >= 0) out.start = Math.min(9999, props.start);
  if (typeof props.align === 'string' && /^(left|center|right)$/.test(props.align)) out.align = props.align;
  return out;
}

export function rehypeSafeHtml() {
  return (tree: any) => {
    const visit = (node: any) => {
      if (!Array.isArray(node?.children)) return;
      const next: any[] = [];
      for (const child of node.children) {
        if (child?.type !== 'element') {
          visit(child);
          next.push(child);
          continue;
        }
        const tag = String(child.tagName || '').toLowerCase();
        if (DROP_HTML_TAGS.has(tag)) continue;
        // <font color="red"> → <span style="color: red">
        if (tag === 'font') {
          const color = sanitizeHtmlColor(child.properties?.color);
          child.tagName = 'span';
          child.properties = color ? { style: `color: ${color}` } : {};
          visit(child);
          next.push(child);
          continue;
        }
        if (!SAFE_HTML_TAGS.has(tag)) {
          // GFM 任务列表的勾选框：只放行 checkbox，其余表单控件一律丢弃
          if (tag === 'input') {
            if (String(child.properties?.type || '').toLowerCase() !== 'checkbox') continue;
            child.properties = {
              type: 'checkbox',
              disabled: true,
              ...(child.properties?.checked ? { checked: true } : {}),
            };
            next.push(child);
            continue;
          }
          visit(child);
          next.push(...(child.children || []));
          continue;
        }
        child.properties = sanitizeHtmlProperties(child.properties);
        visit(child);
        next.push(child);
      }
      node.children = next;
    };
    visit(tree);
  };
}

// ---------------------------------------------------------------------------
// 语义提示块：> [!NOTE] / [!TIP] / [!WARNING] … → 带语义色的 callout
// 只在 rehypeSafeHtml 之后运行，避免 data-alert 被白名单过滤掉。
// ---------------------------------------------------------------------------

const ALERT_KINDS: Record<string, 'info' | 'success' | 'warning'> = {
  note: 'info', info: 'info', important: 'info',
  tip: 'success', success: 'success',
  warning: 'warning', caution: 'warning', danger: 'warning',
};

export function rehypeSemanticAlerts() {
  return (tree: any) => {
    const visit = (node: any) => {
      if (!Array.isArray(node?.children)) return;
      for (const child of node.children) {
        if (child?.type === 'element' && String(child.tagName || '').toLowerCase() === 'blockquote') {
          const paragraph = child.children?.find((item: any) => item?.type === 'element' && item.tagName === 'p');
          const text = paragraph?.children?.find((item: any) => item?.type === 'text');
          const matched = typeof text?.value === 'string' ? /^\s*\[!(\w+)\]\s*/.exec(text.value) : null;
          const kind = matched ? ALERT_KINDS[matched[1].toLowerCase()] : undefined;
          if (kind && matched) {
            text.value = text.value.slice(matched[0].length);
            child.properties = { ...(child.properties || {}), 'data-alert': kind };
          }
        }
        visit(child);
      }
    };
    visit(tree);
  };
}

export function normalizeDataSpec(spec: DataSpec | null): { columns: string[]; columnDefs: Array<{ key: string; label: string; type: string }>; rows: DataValue[][] } {
  const rows = Array.isArray(spec?.rows) ? spec.rows : [];
  const objectColumns = rows.flatMap((row: any) => row && !Array.isArray(row) && typeof row === 'object' ? Object.keys(row) : []);
  const rawColumns = Array.isArray(spec?.columns) && spec!.columns!.length ? spec!.columns! : Array.from(new Set(objectColumns));
  const columnDefs: Array<{ key: string; label: string; type: string }> = rawColumns.map((column, index) => {
    if (column && typeof column === 'object' && !Array.isArray(column)) {
      const key = String(column.key || column.label || `column_${index + 1}`);
      return { key, label: String(column.label || key), type: String(column.type || 'text') };
    }
    const key = String(column);
    return { key, label: key, type: 'text' };
  });
  const columns: string[] = columnDefs.map((column) => column.label);
  const normalizedRows = rows.map((row: any) => Array.isArray(row)
    ? columnDefs.map((_, index) => row[index] ?? null)
    : columnDefs.map((column) => row?.[column.key] ?? null));
  return { columns, columnDefs, rows: normalizedRows };
}
