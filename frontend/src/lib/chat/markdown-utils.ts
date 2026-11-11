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

export function splitStreamingMarkdown(content: string, streaming = false): string[] {
  if (!streaming) return [content];
  const lines = content.match(/.*(?:\n|$)/g)?.filter(Boolean) || [content];
  const chunks: string[] = [];
  let current = '';
  let fence = '';
  for (const line of lines) {
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
    if (!fence && /^\s*$/.test(line) && current.trim()) {
      chunks.push(current);
      current = '';
    }
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
