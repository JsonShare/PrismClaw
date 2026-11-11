import React, { memo, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import { Check, ChevronDown, ChevronRight, Copy, Download, Maximize2, Minus, Plus, RotateCcw, Search, X } from 'lucide-react';
import { createHighlighterCore } from '@shikijs/core';
import { createJavaScriptRegexEngine } from '@shikijs/engine-javascript';
import githubLight from '@shikijs/themes/github-light';
import githubDark from '@shikijs/themes/github-dark';
import bashLanguage from '@shikijs/langs/bash';
import cssLanguage from '@shikijs/langs/css';
import htmlLanguage from '@shikijs/langs/html';
import javascriptLanguage from '@shikijs/langs/javascript';
import jsonLanguage from '@shikijs/langs/json';
import jsxLanguage from '@shikijs/langs/jsx';
import markdownLanguage from '@shikijs/langs/markdown';
import pythonLanguage from '@shikijs/langs/python';
import rubyLanguage from '@shikijs/langs/ruby';
import sqlLanguage from '@shikijs/langs/sql';
import tsxLanguage from '@shikijs/langs/tsx';
import typescriptLanguage from '@shikijs/langs/typescript';
import yamlLanguage from '@shikijs/langs/yaml';
import { hasUnclosedMarkdownFence, markStreamRevealTail, normalizeDataSpec, normalizeMarkdownTables, normalizePreviewSpec, remarkCjkStrong, splitStreamingMarkdown } from '../../lib/chat/markdown-utils';
// @ts-ignore
import { STREAM_REVEAL_ANIMATION_MS } from '../../stream-reveal.mjs';
import 'katex/dist/katex.min.css';

type PreviewContext = { threadId?: string | null; workspaceId?: string | null };
type StreamRevealMeta = { appendedGraphemes: number; revision: number };
type RichMarkdownProps = PreviewContext & { content: string; streaming?: boolean; streamReveal?: StreamRevealMeta; depth?: number };
type PreviewItem = { src: string; title?: string; label?: string };
type PreviewSpec = PreviewItem & { items?: PreviewItem[] };
type DataValue = string | number | boolean | null;
type ColumnDef = { key: string; label?: string; type?: string };
type DataSpec = { title?: string; filename?: string; sheetName?: string; columns?: Array<string | ColumnDef>; rows?: Array<DataValue[] | Record<string, DataValue>>; src?: string; groupBy?: string };

const RICH_LANGUAGES = new Set(['diff', 'json', 'mermaid', 'latex', 'math', 'datatable', 'spreadsheet', 'html-render', 'html-preview', 'image-preview', 'pdf-preview', 'markdown-preview']);
const REPAIRABLE_DIAGRAM_LANGUAGES = new Set(['plantuml', 'puml']);
// @ts-ignore - optional dependency
const LazyPatchDiff = React.lazy(() => import('@pierre/diffs/react').then((module) => ({ default: module.PatchDiff })));
const LazyJsonView = React.lazy(() => import('@uiw/react-json-view').then((module) => ({ default: module.default })));
// @ts-ignore - optional dependency
const LazyPdfDocument = React.lazy(() => import('react-pdf').then((module) => ({ default: module.Document })));
// @ts-ignore - optional dependency
const LazyPdfPage = React.lazy(() => import('react-pdf').then((module) => ({ default: module.Page })));

let shikiHighlighterPromise: Promise<any> | null = null;

const SHIKI_LANGUAGES: Partial<Record<string, any>> = {
  bash: bashLanguage,
  css: cssLanguage,
  html: htmlLanguage,
  javascript: javascriptLanguage,
  json: jsonLanguage,
  jsx: jsxLanguage,
  markdown: markdownLanguage,
  python: pythonLanguage,
  ruby: rubyLanguage,
  sql: sqlLanguage,
  tsx: tsxLanguage,
  typescript: typescriptLanguage,
  yaml: yamlLanguage,
};

async function highlightedCodeHtml(code: string, requestedLanguage: string) {
  if (!shikiHighlighterPromise) {
    shikiHighlighterPromise = createHighlighterCore({
      themes: [githubLight, githubDark],
      langs: Object.values(SHIKI_LANGUAGES),
      engine: createJavaScriptRegexEngine(),
    });
  }
  const highlighter = await shikiHighlighterPromise;
  const aliases: Record<string, string> = { js: 'javascript', jsx: 'jsx', ts: 'typescript', shell: 'bash', sh: 'bash', py: 'python', rb: 'ruby', yml: 'yaml' };
  const language = aliases[requestedLanguage] || requestedLanguage || 'text';
  const grammar = SHIKI_LANGUAGES[language];
  return highlighter.codeToHtml(code, {
    lang: grammar ? language : 'text',
    themes: { light: 'github-light', dark: 'github-dark' },
  });
}

function contentKey(value: string, index: number) {
  let hash = 2166136261;
  for (let offset = 0; offset < value.length; offset += 1) hash = Math.imul(hash ^ value.charCodeAt(offset), 16777619);
  return `${index}-${(hash >>> 0).toString(36)}`;
}

function rehypeStreamRevealTail(options: StreamRevealMeta) {
  return (tree: any) => {
    markStreamRevealTail(tree, options.appendedGraphemes, options.revision);
  };
}

function StreamRevealTail({ children, revision, ...props }: { children: React.ReactNode; revision: string; [key: string]: any }) {
  const ref = useRef<HTMLSpanElement | null>(null);
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof node.animate !== 'function' || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    node.getAnimations().forEach((animation) => animation.cancel());
    node.animate([
      { opacity: .6, transform: 'translateY(1px)', filter: 'blur(.6px)' },
      { opacity: 1, transform: 'translateY(0)', filter: 'blur(0)' },
    ], {
      duration: STREAM_REVEAL_ANIMATION_MS,
      easing: 'cubic-bezier(.2, .8, .2, 1)',
      fill: 'both',
    });
  }, [revision]);
  return <span ref={ref} className="stream-reveal-tail" data-stream-reveal-revision={revision} {...props}>{children}</span>;
}

function previewUrl(src: string, context: PreviewContext) {
  const query = new URLSearchParams({ path: src });
  if (context.threadId) query.set('threadId', context.threadId);
  if (context.workspaceId) query.set('workspaceId', context.workspaceId);
  return `/api/rich-preview?${query.toString()}`;
}

function openSafeMarkdownLink(href: string) {
  if (!/^(https?:|mailto:)/i.test(href)) return;
  const desktop = (window as any).frakioDesktop;
  if (desktop?.openExternal) {
    void desktop.openExternal(href);
    return;
  }
  window.open(href, '_blank', 'noopener,noreferrer');
}

function parseJson<T>(source: string): T | null {
  try {
    return JSON.parse(source) as T;
  } catch {
    return null;
  }
}

function downloadBlob(blob: Blob, fileName: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = fileName;
  anchor.click();
  URL.revokeObjectURL(url);
}

function csvCell(value: DataValue) {
  const text = value == null ? '' : String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function useTextResource(url: string | null) {
  const [state, setState] = useState<{ text: string; error: string; loading: boolean }>({ text: '', error: '', loading: Boolean(url) });
  useEffect(() => {
    if (!url) {
      setState({ text: '', error: '', loading: false });
      return;
    }
    const controller = new AbortController();
    setState({ text: '', error: '', loading: true });
    fetch(url, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error((await response.json().catch(() => null))?.error || `读取失败（${response.status}）`);
        return response.text();
      })
      .then((text) => setState({ text, error: '', loading: false }))
      .catch((error) => {
        if (error?.name !== 'AbortError') setState({ text: '', error: String(error?.message || error), loading: false });
      });
    return () => controller.abort();
  }, [url]);
  return state;
}

class RichBlockBoundary extends React.Component<{ fallback: React.ReactNode; children: React.ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: unknown) { console.warn('Rich content renderer failed:', error); }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

function IconButton({ label, onClick, children, disabled }: { label: string; onClick: () => void; children: React.ReactNode; disabled?: boolean }) {
  return <button className="rich-icon-button" type="button" aria-label={label} title={label} onClick={onClick} disabled={disabled}>{children}</button>;
}

function RichFrame({ title, actions, children, className = '' }: { title: string; actions?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return <section className={`rich-frame ${className}`.trim()}>
    <header className="rich-frame-header"><span>{title}</span><div className="rich-frame-actions">{actions}</div></header>
    <div className="rich-frame-body">{children}</div>
  </section>;
}

function Fullscreen({ open, title, onClose, children }: { open: boolean; title: string; onClose: () => void; children: React.ReactNode }) {
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [open, onClose]);
  if (!open) return null;
  return createPortal(<div className="rich-fullscreen-backdrop" role="presentation" onMouseDown={onClose}>
    <section className="rich-fullscreen" data-appearance={document.querySelector('.app')?.getAttribute('data-appearance') || 'light'} role="dialog" aria-modal="true" aria-label={title} onMouseDown={(event) => event.stopPropagation()}>
      <header><strong>{title}</strong><IconButton label="关闭" onClick={onClose}><X size={17} /></IconButton></header>
      <div className="rich-fullscreen-content">{children}</div>
    </section>
  </div>, document.body);
}

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return <IconButton label={copied ? '已复制' : '复制'} onClick={() => {
    void navigator.clipboard?.writeText(value).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    });
  }}>{copied ? <Check size={15} /> : <Copy size={15} />}</IconButton>;
}

function CodeBlock({ code, language }: { code: string; language: string }) {
  const [html, setHtml] = useState('');
  useEffect(() => {
    let cancelled = false;
    void highlightedCodeHtml(code, language).then((value) => { if (!cancelled) setHtml(value); }).catch((error) => {
      console.warn('Shiki highlighting failed:', error);
      if (!cancelled) setHtml('');
    });
    return () => { cancelled = true; };
  }, [code, language]);
  return <RichFrame title={language || 'text'} actions={<CopyButton value={code} />} className="rich-code-frame">
    {html ? <div className="rich-shiki" dangerouslySetInnerHTML={{ __html: html }} /> : <pre className="rich-code-fallback"><code>{code}</code></pre>}
  </RichFrame>;
}

function DiffBlock({ code }: { code: string }) {
  const PatchDiffComp = LazyPatchDiff as any;
  return <RichFrame title="Diff" actions={<CopyButton value={code} />} className="rich-diff-frame">
    <React.Suspense fallback={<pre className="rich-code-fallback"><code>{code}</code></pre>}><PatchDiffComp patch={code} disableWorkerPool options={{ diffStyle: 'unified', themeType: 'system' }} /></React.Suspense>
  </RichFrame>;
}

function JsonBlock({ code }: { code: string }) {
  const value = parseJson<object>(code);
  if (!value || typeof value !== 'object') return <CodeBlock code={code} language="json" />;
  return <RichFrame title="JSON" actions={<CopyButton value={code} />} className="rich-json-frame">
    <React.Suspense fallback={<pre className="rich-code-fallback"><code>{code}</code></pre>}><LazyJsonView value={value} collapsed={2} displayDataTypes={false} shortenTextAfterLength={120} enableClipboard /></React.Suspense>
  </RichFrame>;
}

function cleanSvg(svg: string) {
  const documentNode = new DOMParser().parseFromString(svg, 'image/svg+xml');
  documentNode.querySelectorAll('script, foreignObject').forEach((node) => node.remove());
  documentNode.querySelectorAll('style').forEach((node) => {
    node.textContent = String(node.textContent || '')
      .replace(/@import\s+url\([^)]*\)\s*;?/gi, '')
      .replace(/font-family\s*:[^;]+;/gi, 'font-family: system-ui, sans-serif;');
  });
  documentNode.querySelectorAll('*').forEach((node) => [...node.attributes].forEach((attribute) => {
    if (/^on/i.test(attribute.name) || /javascript:/i.test(attribute.value)) node.removeAttribute(attribute.name);
  }));
  return new XMLSerializer().serializeToString(documentNode.documentElement);
}

function MermaidCanvas({ svg, zoom = 1 }: { svg: string; zoom?: number }) {
  return <div className="rich-mermaid-canvas"><div className="rich-mermaid-zoom" style={{ zoom } as React.CSSProperties} dangerouslySetInnerHTML={{ __html: svg }} /></div>;
}

function MermaidBlock({ code }: { code: string }) {
  const [fullscreen, setFullscreen] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [result, setResult] = useState({ svg: '', error: '', loading: true });
  useEffect(() => {
    let cancelled = false;
    setResult({ svg: '', error: '', loading: true });
    void import('mermaid').then(async ({ default: mermaid }) => {
      mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', fontFamily: 'Inter, system-ui, sans-serif', fontSize: 14 });
      const id = `mermaid-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const { svg } = await mermaid.render(id, code);
      if (!cancelled) setResult({ svg: cleanSvg(svg), error: '', loading: false });
    }).catch((error) => {
      if (!cancelled) setResult({ svg: '', error: String(error instanceof Error ? error.message : error), loading: false });
    });
    return () => { cancelled = true; };
  }, [code]);
  if (result.loading) return <RichFrame title="Mermaid" actions={<CopyButton value={code} />} className="rich-mermaid-frame"><p className="rich-loading">正在绘制图表…</p></RichFrame>;
  if (!result.svg) return <RichFrame title="Mermaid 无法渲染" actions={<CopyButton value={code} />} className="rich-error-frame"><p>{result.error || '图表语法无效。'}</p><CodeBlock code={code} language="mermaid" /></RichFrame>;
  return <>
    <RichFrame title="Mermaid" actions={<><CopyButton value={code} /><IconButton label="全屏查看" onClick={() => setFullscreen(true)}><Maximize2 size={15} /></IconButton></>} className="rich-mermaid-frame"><MermaidCanvas svg={result.svg} /></RichFrame>
    <Fullscreen open={fullscreen} title="Mermaid" onClose={() => setFullscreen(false)}><div className="rich-mermaid-fullscreen-view">
      <div className="rich-mermaid-fullscreen-tools" aria-label="流程图缩放">
        <IconButton label="缩小" onClick={() => setZoom((value) => Math.max(.6, Math.round((value - .2) * 10) / 10))}><Minus size={15} /></IconButton>
        <IconButton label="重置缩放" onClick={() => setZoom(1)}><RotateCcw size={14} /></IconButton>
        <IconButton label="放大" onClick={() => setZoom((value) => Math.min(2, Math.round((value + .2) * 10) / 10))}><Plus size={15} /></IconButton>
      </div>
      <MermaidCanvas svg={result.svg} zoom={zoom} />
    </div></Fullscreen>
  </>;
}

function MathBlock({ code }: { code: string }) {
  return <RichFrame title="Math" actions={<CopyButton value={code} />} className="rich-math-frame"><ReactMarkdown remarkPlugins={[remarkMath]} rehypePlugins={[rehypeKatex]}>{`$$\n${code}\n$$`}</ReactMarkdown></RichFrame>;
}

function formattedDataValue(value: DataValue, type = 'text') {
  if (value == null) return '';
  if (type === 'number' && typeof value === 'number') return new Intl.NumberFormat().format(value);
  if (type === 'currency' && typeof value === 'number') return new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(value);
  if (type === 'percent' && typeof value === 'number') return new Intl.NumberFormat(undefined, { style: 'percent', maximumFractionDigits: 1, signDisplay: 'exceptZero' }).format(value);
  if (type === 'boolean') return value ? 'Yes' : 'No';
  if (type === 'date') {
    const date = new Date(String(value));
    if (!Number.isNaN(date.getTime())) return new Intl.DateTimeFormat(undefined, { year: 'numeric', month: 'short', day: 'numeric' }).format(date);
  }
  return String(value);
}

function mergeDataSpec(inline: DataSpec | null, fileValue: unknown): DataSpec | null {
  if (!inline) return null;
  const fileSpec: DataSpec | null = Array.isArray(fileValue)
    ? { rows: fileValue as Array<DataValue[] | Record<string, DataValue>> }
    : fileValue && typeof fileValue === 'object' ? fileValue as DataSpec : null;
  if (!fileSpec) return inline;
  return {
    ...fileSpec,
    ...inline,
    columns: inline.columns?.length ? inline.columns : fileSpec.columns,
    rows: inline.rows?.length ? inline.rows : fileSpec.rows,
    title: inline.title || fileSpec.title,
  };
}

function DataGrid({ code, spreadsheet, context }: { code: string; spreadsheet: boolean; context: PreviewContext }) {
  const inline = parseJson<DataSpec>(code);
  const resourceUrl = inline?.src ? previewUrl(inline.src, context) : null;
  const resource = useTextResource(resourceUrl);
  const remote = resource.text ? parseJson<unknown>(resource.text) : null;
  const spec = useMemo(() => mergeDataSpec(inline, remote), [inline, remote]);
  const normalized = useMemo(() => spec ? normalizeDataSpec(spec) : { columns: [], columnDefs: [], rows: [] }, [spec]);
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<{ index: number; direction: 1 | -1 } | null>(null);
  const [filters, setFilters] = useState<Record<number, string>>({});
  const initialGroupIndex = spec?.groupBy ? normalized.columnDefs?.findIndex((column: ColumnDef) => column.key === spec.groupBy || column.label === spec.groupBy) ?? -1 : -1;
  const [groupIndex, setGroupIndex] = useState(initialGroupIndex);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(() => new Set());
  const [fullscreen, setFullscreen] = useState(false);
  useEffect(() => setGroupIndex(initialGroupIndex), [initialGroupIndex]);
  const visibleRows = useMemo(() => {
    const term = query.trim().toLocaleLowerCase();
    const filtered = normalized.rows.filter((row) => {
      if (term && !row.some((value) => String(value ?? '').toLocaleLowerCase().includes(term))) return false;
      return Object.entries(filters).every(([index, value]) => !value.trim() || String(row[Number(index)] ?? '').toLocaleLowerCase().includes(value.trim().toLocaleLowerCase()));
    });
    if (!sort) return filtered;
    return [...filtered].sort((left, right) => String(left[sort.index] ?? '').localeCompare(String(right[sort.index] ?? ''), undefined, { numeric: true }) * sort.direction);
  }, [filters, normalized.rows, query, sort]);
  const groupedRows = useMemo(() => {
    if (groupIndex < 0) return null;
    const groups = new Map<string, DataValue[][]>();
    for (const row of visibleRows) {
      const key = String(row[groupIndex] ?? '—');
      groups.set(key, [...(groups.get(key) || []), row]);
    }
    return [...groups.entries()];
  }, [groupIndex, visibleRows]);
  if (!spec || resource.error || !normalized.columns.length) return <CodeBlock code={code} language={spreadsheet ? 'spreadsheet' : 'datatable'} />;

  const exportData = async (format: 'csv' | 'xlsx') => {
    if (format === 'xlsx') {
      // @ts-ignore - optional dependency
      const { default: writeXlsxFile } = await import('write-excel-file/browser');
      const sheet = [normalized.columns, ...visibleRows].map((row) => row.map((value) => value == null ? '' : value));
      await writeXlsxFile(sheet).toFile(`${spec.filename || spec.title || 'frakio-data'}.xlsx`);
      return;
    }
    const csv = [normalized.columns, ...visibleRows].map((row) => row.map(csvCell).join(',')).join('\n');
    downloadBlob(new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' }), `${spec.title || 'frakio-data'}.csv`);
  };
  const renderRow = (row: DataValue[], rowIndex: number | string) => <tr key={rowIndex}>{row.map((value, cellIndex) => {
    const type = normalized.columnDefs[cellIndex]?.type || 'text';
    return <td key={cellIndex}><span className={type === 'badge' ? 'rich-data-badge' : type === 'percent' && typeof value === 'number' ? value < 0 ? 'rich-data-negative' : 'rich-data-positive' : ''}>{formattedDataValue(value, type)}</span></td>;
  })}</tr>;
  const table = <div className="rich-data-content">
    <div className="rich-data-toolbar">
      <label><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索数据" /></label>
      {!spreadsheet && <label className="rich-select"><select value={groupIndex} onChange={(event) => setGroupIndex(Number(event.target.value))}><option value={-1}>不分组</option>{normalized.columns.map((column, index) => <option value={index} key={normalized.columnDefs[index]?.key || column}>按 {column} 分组</option>)}</select><ChevronDown size={13} /></label>}
      <span>{visibleRows.length} 行</span>
    </div>
    <div className="rich-table-scroll"><table><thead><tr>{normalized.columns.map((column, index) => <th key={normalized.columnDefs[index]?.key || column}><button type="button" onClick={() => setSort((current) => current?.index === index ? { index, direction: current.direction === 1 ? -1 : 1 } : { index, direction: 1 })}>{column}{sort?.index === index ? (sort.direction === 1 ? ' ↑' : ' ↓') : ''}</button></th>)}</tr>{!spreadsheet && <tr className="rich-filter-row">{normalized.columns.map((column, index) => <th key={column}><input value={filters[index] || ''} onChange={(event) => setFilters((current) => ({ ...current, [index]: event.target.value }))} placeholder={`筛选 ${column}`} aria-label={`筛选 ${column}`} /></th>)}</tr>}</thead><tbody>{groupedRows ? groupedRows.flatMap(([group, rows]) => [<tr className="rich-group-row" key={`group-${group}`} onClick={() => setCollapsedGroups((current) => { const next = new Set(current); if (next.has(group)) next.delete(group); else next.add(group); return next; })}><td colSpan={normalized.columns.length}><ChevronRight size={13} className={collapsedGroups.has(group) ? '' : 'expanded'} />{normalized.columns[groupIndex]}: {group} <small>({rows.length})</small></td></tr>, ...(collapsedGroups.has(group) ? [] : rows.map((row, index) => renderRow(row, `${group}-${index}`)))]) : visibleRows.map(renderRow)}</tbody></table></div>
  </div>;
  return <>
    <RichFrame title={spec.title || spec.sheetName || (spreadsheet ? 'Spreadsheet' : 'Data table')} actions={<>{spreadsheet && <IconButton label="导出 XLSX" onClick={() => void exportData('xlsx')}><Download size={15} /></IconButton>}<IconButton label="导出 CSV" onClick={() => void exportData('csv')}><Download size={15} /></IconButton><IconButton label="全屏查看" onClick={() => setFullscreen(true)}><Maximize2 size={15} /></IconButton></>} className="rich-data-frame">{resource.loading ? <p className="rich-loading">正在读取数据…</p> : table}</RichFrame>
    <Fullscreen open={fullscreen} title={spec.title || (spreadsheet ? 'Spreadsheet' : 'Data table')} onClose={() => setFullscreen(false)}>{table}</Fullscreen>
  </>;
}

function previewItems(spec: PreviewSpec | null): PreviewItem[] {
  if (!spec) return [];
  return spec.items?.length ? spec.items : [{ src: spec.src, title: spec.title, label: spec.label }];
}

function PreviewTabs({ items, active, onSelect }: { items: PreviewItem[]; active: number; onSelect: (index: number) => void }) {
  if (items.length < 2) return null;
  return <div className="rich-preview-tabs" role="tablist">{items.map((item, index) => <button type="button" role="tab" aria-selected={index === active} className={index === active ? 'active' : ''} onClick={() => onSelect(index)} key={`${item.src}-${index}`}>{item.label || item.title || item.src.split('/').at(-1)}</button>)}</div>;
}

function ImagePreview({ code, context }: { code: string; context: PreviewContext }) {
  const spec = normalizePreviewSpec(code);
  const items = previewItems(spec);
  const [active, setActive] = useState(0);
  const [fullscreen, setFullscreen] = useState(false);
  const [failed, setFailed] = useState<Record<string, boolean>>({});
  const selected = items[active] || items[0];
  if (!spec || !selected) return <CodeBlock code={code} language="image-preview" />;
  const url = previewUrl(selected.src, context);
  const image = failed[selected.src] ? <p className="rich-preview-error">图片加载失败。</p> : <img src={url} alt={selected.title || selected.label || ''} onError={() => setFailed((current) => ({ ...current, [selected.src]: true }))} />;
  return <>
    <RichFrame title={spec.title || selected.title || selected.label || 'Image'} actions={<IconButton label="全屏查看" onClick={() => setFullscreen(true)} disabled={failed[selected.src]}><Maximize2 size={15} /></IconButton>} className="rich-image-frame"><PreviewTabs items={items} active={active} onSelect={setActive} />{image}</RichFrame>
    <Fullscreen open={fullscreen && !failed[selected.src]} title={spec.title || selected.title || 'Image'} onClose={() => setFullscreen(false)}><div className="rich-preview-fullscreen-stack"><PreviewTabs items={items} active={active} onSelect={setActive} /><img className="rich-fullscreen-image" src={url} alt={selected.title || ''} /></div></Fullscreen>
  </>;
}

function HtmlFrame({ item, context, fullscreen = false }: { item: PreviewItem; context: PreviewContext; fullscreen?: boolean }) {
  const resource = useTextResource(previewUrl(item.src, context));
  if (resource.error) return <p className="rich-preview-error">{resource.error}</p>;
  if (resource.loading) return <p className="rich-loading">正在读取 HTML…</p>;
  return <iframe className={fullscreen ? 'rich-fullscreen-iframe' : undefined} title={item.title || item.label || 'HTML preview'} sandbox="" srcDoc={resource.text} />;
}

function wrapHtmlForRender(fragment: string): string {
  // 防御：如果 LLM 输出了完整 HTML 文档，提取 <body> 内容作为片段
  let content = fragment.trim();
  const bodyMatch = content.match(/<body[^>]*>([\s\S]*)<\/body>/i);
  if (bodyMatch) content = bodyMatch[1].trim();
  else if (/<!doctype|<html/i.test(content)) content = content.replace(/<!doctype[^>]*>/gi, '').replace(/<\/?html[^>]*>/gi, '').replace(/<head[\s\S]*<\/head>/gi, '').replace(/<\/?body[^>]*>/gi, '').trim();

  const tokens = `<style>
:root {
  color-scheme: light dark;
  --color-background-primary: #fff;
  --color-background-secondary: #f6f8fa;
  --color-background-tertiary: #eaeef2;
  --color-text-primary: #1f2328;
  --color-text-secondary: #59636e;
  --color-text-tertiary: #818b98;
  --color-border-primary: rgba(31,35,40,.20);
  --color-border-secondary: rgba(31,35,40,.12);
  --color-border-tertiary: rgba(31,35,40,.08);
  --color-background-info: rgba(9,105,218,.10);
  --color-text-info: #0969da;
  --color-background-danger: rgba(207,34,46,.10);
  --color-text-danger: #cf222e;
  --color-background-success: rgba(26,127,55,.10);
  --color-text-success: #1a7f37;
  --color-background-warning: rgba(154,103,0,.10);
  --color-text-warning: #9a6700;
  --font-sans: -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei UI', 'Microsoft YaHei', system-ui, sans-serif;
  --font-mono: 'SF Mono', ui-monospace, 'JetBrains Mono', 'Cascadia Code', 'Fira Code', 'Menlo', 'Consolas', monospace;
  --border-radius-md: 8px;
  --border-radius-lg: 12px;
  --border-radius-xl: 16px;
  --font-weight-normal: 400;
  --font-weight-medium: 500;
  --font-weight-semibold: 600;
  --font-weight-bold: 600;
  --shadow-hairline: 0 0 0 1px rgba(31,35,40,.08);
  --shadow-sm: 0 1px 2px rgba(31,35,40,.06);
  --shadow-md: 0 8px 20px -12px rgba(31,35,40,.12);
}
@media (prefers-color-scheme: dark) {
  :root {
    --color-background-primary: #1a1a1a;
    --color-background-secondary: #262626;
    --color-background-tertiary: #303030;
    --color-text-primary: #f3f4f6;
    --color-text-secondary: #9ca3af;
    --color-text-tertiary: #6b7280;
    --color-border-primary: rgba(255,255,255,.20);
    --color-border-secondary: rgba(255,255,255,.12);
    --color-border-tertiary: rgba(255,255,255,.08);
    --color-background-info: rgba(9,105,218,.15);
    --color-text-info: #58a6ff;
    --color-background-danger: rgba(248,81,73,.15);
    --color-text-danger: #f85149;
    --color-background-success: rgba(63,185,80,.15);
    --color-text-success: #3fb950;
    --color-background-warning: rgba(210,153,34,.15);
    --color-text-warning: #d29922;
    --shadow-hairline: 0 0 0 1px rgba(255,255,255,.08);
    --shadow-sm: 0 1px 2px rgba(255,255,255,.06);
    --shadow-md: 0 8px 20px -12px rgba(255,255,255,.12);
  }
}
body { margin: 0; padding: 16px; font-family: var(--font-sans); color: var(--color-text-primary); background: var(--color-background-primary); }
</style>`;
  const reporter = `<script>
(function(){
  var t=0;
  function r(){clearTimeout(t);t=setTimeout(function(){try{parent.postMessage({type:'html-render-height',height:document.documentElement.scrollHeight},'*')}catch(e){}},100)}
  r();
  if(typeof MutationObserver!=='undefined')new MutationObserver(r).observe(document.body,{childList:true,subtree:true,attributes:true});
  window.addEventListener('resize',r);
  window.addEventListener('load',r);
})();
</script>`;
  return `<!DOCTYPE html><html><head>${tokens}</head><body>${content}${reporter}</body></html>`;
}

function HtmlRenderBlock({ code }: { code: string }) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(200);
  const [fullscreen, setFullscreen] = useState(false);
  const srcdoc = useMemo(() => wrapHtmlForRender(code), [code]);
  const maxHeight = 1200;

  useEffect(() => {
    const handler = (e: MessageEvent) => {
      if (e.data?.type === 'html-render-height' && e.source === iframeRef.current?.contentWindow) {
        const h = e.data.height;
        setHeight(Math.max(60, Math.min(h, maxHeight)));
      }
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, []);

  return (
    <>
      <RichFrame
        title="卡片"
        actions={<IconButton label="全屏查看" onClick={() => setFullscreen(true)}><Maximize2 size={15} /></IconButton>}
        className="rich-html-render-frame"
      >
        <iframe
          ref={iframeRef}
          sandbox="allow-scripts"
          srcDoc={srcdoc}
          title="富内容卡片"
          style={{ width: '100%', height: `${height}px`, border: 0, display: 'block' }}
        />
      </RichFrame>
      <Fullscreen open={fullscreen} title="富内容卡片" onClose={() => setFullscreen(false)}>
        <iframe sandbox="allow-scripts" srcDoc={srcdoc} title="富内容卡片" className="rich-fullscreen-iframe" />
      </Fullscreen>
    </>
  );
}

function HtmlPreview({ code, context }: { code: string; context: PreviewContext }) {
  const spec = normalizePreviewSpec(code);
  const items = previewItems(spec);
  const [active, setActive] = useState(0);
  const [fullscreen, setFullscreen] = useState(false);
  const selected = items[active] || items[0];
  if (!spec || !selected) return <CodeBlock code={code} language="html-preview" />;
  return <>
    <RichFrame title={spec.title || selected.title || 'HTML preview'} actions={<IconButton label="全屏查看" onClick={() => setFullscreen(true)}><Maximize2 size={15} /></IconButton>} className="rich-html-frame"><PreviewTabs items={items} active={active} onSelect={setActive} /><HtmlFrame item={selected} context={context} /></RichFrame>
    <Fullscreen open={fullscreen} title={spec.title || selected.title || 'HTML preview'} onClose={() => setFullscreen(false)}><div className="rich-preview-fullscreen-stack"><PreviewTabs items={items} active={active} onSelect={setActive} /><HtmlFrame item={selected} context={context} fullscreen /></div></Fullscreen>
  </>;
}

function PdfDocumentView({ url, fullscreen = false }: { url: string; fullscreen?: boolean }) {
  const [pageCount, setPageCount] = useState(0);
  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [url]);
  const PdfDocComp = LazyPdfDocument as any;
  const PdfPageComp = LazyPdfPage as any;
  return <div className={fullscreen ? 'rich-pdf-reader' : 'rich-pdf-inline'}>
    <React.Suspense fallback={<p className="rich-loading">正在加载 PDF 组件…</p>}>
      {fullscreen && pageCount > 0 && <div className="rich-pdf-navigation"><IconButton label="上一页" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}><ChevronRight className="previous" size={15} /></IconButton><span>{page} / {pageCount}</span><IconButton label="下一页" disabled={page >= pageCount} onClick={() => setPage((value) => Math.min(pageCount, value + 1))}><ChevronRight size={15} /></IconButton></div>}
      {PdfDocComp ? <PdfDocComp file={url} loading={<p className="rich-loading">正在加载 PDF…</p>} error={<p className="rich-preview-error">PDF 加载失败。</p>} onLoadSuccess={(pdf: any) => { setPageCount(pdf.numPages); setPage((value) => Math.min(Math.max(1, value), pdf.numPages)); }}>
        {pageCount > 0 && <PdfPageComp pageNumber={fullscreen ? page : 1} width={fullscreen ? Math.min(900, window.innerWidth - 100) : 720} renderTextLayer={fullscreen} renderAnnotationLayer={fullscreen} />}
      </PdfDocComp> : <p className="rich-preview-error">PDF 组件未安装。</p>}
    </React.Suspense>
  </div>;
}

function PdfPreview({ code, context }: { code: string; context: PreviewContext }) {
  const spec = normalizePreviewSpec(code);
  const items = previewItems(spec);
  const [active, setActive] = useState(0);
  const [fullscreen, setFullscreen] = useState(false);
  const selected = items[active] || items[0];
  if (!spec || !selected) return <CodeBlock code={code} language="pdf-preview" />;
  const url = previewUrl(selected.src, context);
  return <>
    <RichFrame title={spec.title || selected.title || 'PDF'} actions={<IconButton label="全屏查看" onClick={() => setFullscreen(true)}><Maximize2 size={15} /></IconButton>} className="rich-pdf-frame"><PreviewTabs items={items} active={active} onSelect={setActive} /><PdfDocumentView url={url} /></RichFrame>
    <Fullscreen open={fullscreen} title={spec.title || selected.title || 'PDF'} onClose={() => setFullscreen(false)}><div className="rich-preview-fullscreen-stack"><PreviewTabs items={items} active={active} onSelect={setActive} /><PdfDocumentView url={url} fullscreen /></div></Fullscreen>
  </>;
}

function MarkdownDocument({ item, context, depth }: { item: PreviewItem; context: PreviewContext; depth: number }) {
  const resource = useTextResource(previewUrl(item.src, context));
  return resource.error ? <p className="rich-preview-error">{resource.error}</p> : resource.loading ? <p className="rich-loading">正在读取 Markdown…</p> : <RichMarkdown content={resource.text} {...context} depth={depth + 1} />;
}

function MarkdownPreview({ code, context, depth }: { code: string; context: PreviewContext; depth: number }) {
  const spec = normalizePreviewSpec(code);
  const items = previewItems(spec);
  const [active, setActive] = useState(0);
  const [fullscreen, setFullscreen] = useState(false);
  const selected = items[active] || items[0];
  if (!spec || !selected || depth >= 1) return <CodeBlock code={code} language="markdown-preview" />;
  const tabs = <PreviewTabs items={items} active={active} onSelect={setActive} />;
  return <>
    <RichFrame title={spec.title || selected.title || 'Markdown'} actions={<IconButton label="全屏查看" onClick={() => setFullscreen(true)}><Maximize2 size={15} /></IconButton>} className="rich-markdown-preview">{tabs}<MarkdownDocument item={selected} context={context} depth={depth} /></RichFrame>
    <Fullscreen open={fullscreen} title={spec.title || selected.title || 'Markdown'} onClose={() => setFullscreen(false)}><div className="rich-preview-fullscreen-stack">{tabs}<div className="rich-markdown-fullscreen"><MarkdownDocument item={selected} context={context} depth={depth} /></div></div></Fullscreen>
  </>;
}

function RichFence({ language, code, context, depth }: { language: string; code: string; context: PreviewContext; depth: number }) {
  const fallback = <CodeBlock code={code} language={language} />;
  let content: React.ReactNode = fallback;
  if (language === 'diff') content = <DiffBlock code={code} />;
  else if (language === 'json') content = <JsonBlock code={code} />;
  else if (language === 'mermaid') content = <MermaidBlock code={code} />;
  else if (language === 'latex' || language === 'math') content = <MathBlock code={code} />;
  else if (language === 'datatable') content = <DataGrid code={code} spreadsheet={false} context={context} />;
  else if (language === 'spreadsheet') content = <DataGrid code={code} spreadsheet context={context} />;
  else if (language === 'html-render') content = <HtmlRenderBlock code={code} />;
  else if (language === 'html-preview') content = <HtmlPreview code={code} context={context} />;
  else if (language === 'image-preview') content = <ImagePreview code={code} context={context} />;
  else if (language === 'pdf-preview') content = <PdfPreview code={code} context={context} />;
  else if (language === 'markdown-preview') content = <MarkdownPreview code={code} context={context} depth={depth} />;
  return <RichBlockBoundary fallback={fallback}>{content}</RichBlockBoundary>;
}

function PendingRichBlock({ language }: { language: string }) {
  return <RichFrame title={REPAIRABLE_DIAGRAM_LANGUAGES.has(language) || language === 'mermaid' ? '正在生成图表' : '正在生成内容'} className="rich-pending-frame"><p className="rich-loading">内容完成后将自动呈现。</p></RichFrame>;
}

const MarkdownChunk = memo(function MarkdownChunk({ content, context, depth, streaming, streamReveal }: { content: string; context: PreviewContext; depth: number; streaming: boolean; streamReveal?: StreamRevealMeta }) {
  const incompleteFence = streaming && hasUnclosedMarkdownFence(content);
  const rehypePlugins = streamReveal?.appendedGraphemes
    ? [rehypeKatex, [rehypeStreamRevealTail, streamReveal]]
    : [rehypeKatex];
  return <ReactMarkdown
    // remark-gfm 默认 singleTilde: true，会把 "1~2 袋，一日 1~2 次" 里的
    // 两个单波浪号配对当删除线，中间大段文字被划掉。关闭 singleTilde 后
    // 只有标准 GFM 双波浪号 ~~...~~ 才会触发删除线（这才是 GFM 规范）。
    remarkPlugins={[
      [remarkGfm, { singleTilde: false }],
      remarkMath,
      remarkCjkStrong,
    ]}
    rehypePlugins={rehypePlugins as any}
    components={{
      // 安全 HTML 过滤：只允许白名单标签，其余以纯文本渲染
      html: ({ node, children }: any) => {
        const tag: string = node?.tagName?.toLowerCase() ?? ''
        const ALLOWED = ['details', 'summary', 'br', 'hr', 'sub', 'sup', 'kbd', 'mark']
        if (ALLOWED.includes(tag)) {
          return React.createElement(tag, node?.properties ?? {}, children)
        }
        return <>{children}</>
      },
      table: ({ children }) => <div className="markdown-table-scroll"><table>{children}</table></div>,
      a: ({ href, children, ...props }) => {
        const safe = typeof href === 'string' && /^(https?:|mailto:)/i.test(href);
        return safe ? <a href={href} target="_blank" rel="noreferrer" onClick={(event) => { event.preventDefault(); openSafeMarkdownLink(href); }} {...props}>{children}</a> : <span>{children}</span>;
      },
      span: ({ children, ...props }) => {
        const revision = String((props as any)['data-stream-reveal-revision'] || '');
        return revision
          ? <StreamRevealTail revision={revision} {...props}>{children}</StreamRevealTail>
          : <span {...props}>{children}</span>;
      },
      pre: ({ children }) => <>{children}</>,
      code: ({ className, children }) => {
        const match = /language-([^\s]+)/.exec(className || '');
        const language = match?.[1]?.toLowerCase() || '';
        const code = String(children).replace(/\n$/, '');
        if (!match) {
          const isMultiLine = code.includes('\n');
          if (isMultiLine) {
            return <CodeBlock code={code} language="text" />;
          }
          return <code className={className}>{children}</code>;
        }
        if (streaming && (incompleteFence || REPAIRABLE_DIAGRAM_LANGUAGES.has(language)) && (RICH_LANGUAGES.has(language) || REPAIRABLE_DIAGRAM_LANGUAGES.has(language))) {
          return <PendingRichBlock language={language} />;
        }
        return <RichFence language={RICH_LANGUAGES.has(language) ? language : language || 'text'} code={code} context={context} depth={depth} />;
      },
    }}
  >{content}</ReactMarkdown>;
});

export function RichMarkdown({ content, streaming = false, streamReveal, threadId, workspaceId, depth = 0 }: RichMarkdownProps) {
  const normalized = useMemo(() => {
    const base = String(content || '').replace(/^\s*\n+/, '').trimStart();
    return normalizeMarkdownTables(base);
  }, [content]);
  const chunks = useMemo(() => splitStreamingMarkdown(normalized, streaming), [normalized, streaming]);
  const context = useMemo(() => ({ threadId, workspaceId }), [threadId, workspaceId]);

  return <div className="message-text markdown-message rich-markdown">
    {chunks.map((chunk, index) => {
      const isLiveTail = streaming && index === chunks.length - 1;
      return <MarkdownChunk
        content={chunk}
        context={context}
        depth={depth}
        streaming={streaming}
        streamReveal={isLiveTail ? streamReveal : undefined}
        key={isLiveTail ? `streaming-tail-${index}` : contentKey(chunk, index)}
      />;
    })}
  </div>;
}

// PrismClaw compatibility wrappers
export default function MarkdownRenderer(props: { content: string; className?: string; streaming?: boolean }) {
  return <div className={props.className}><RichMarkdown content={props.content} streaming={props.streaming} /></div>;
}

export function StreamingMarkdown(props: { content: string; className?: string }) {
  return <div className={props.className}><RichMarkdown content={props.content} streaming /></div>;
}

