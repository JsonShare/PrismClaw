import { marked } from 'marked';

export type RenderMode = 'display' | 'export';

export function renderMarkdownToHtml(src: string, mode: RenderMode = 'display'): string {
  if (!src || typeof src !== 'string') return '';
  if (mode === 'export') {
    return renderExportHtml(src);
  }
  return marked.parse(src) as string;
}

function escapeHtml(str: string): string {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function renderExportHtml(md: string): string {
  const fenceRe = /^```([\w+\-.*]*)\s*\n([\s\S]*?)```\s*$/gm;
  const snippets: { code: string; lang: string }[] = [];
  const placeholderParts: string[] = [];

  let lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = fenceRe.exec(md)) !== null) {
    if (m.index > lastIndex) placeholderParts.push(md.slice(lastIndex, m.index));
    const rawLang = m[1] || '';
    const code = m[2].replace(/\n+$/, '');
    snippets.push({ code, lang: rawLang.toLowerCase() || 'text' });
    placeholderParts.push(`__CODE_BLOCK_${snippets.length - 1}__\n`);
    lastIndex = m.index + m[0].length;
  }
  if (lastIndex < md.length) placeholderParts.push(md.slice(lastIndex));

  const placeholderMarkdown = placeholderParts.join('');
  let html = marked.parse(placeholderMarkdown) as string;

  snippets.forEach((s, idx) => {
    const langLabel = s.lang && s.lang !== 'text' ? s.lang : 'code';
    const bodyHtml = escapeHtml(s.code);
    const blockHtml =
      `<div class="code-block">` +
      `<div class="code-block-header"><span>${escapeHtml(langLabel)}</span></div>` +
      `<div class="code-block-body"><pre><code>${bodyHtml}</code></pre></div>` +
      `</div>`;
    const wrappedRe = new RegExp(`<p>\\s*__CODE_BLOCK_${idx}__\\s*</p>`, 'g');
    if (wrappedRe.test(html)) {
      html = html.replace(wrappedRe, blockHtml);
    } else {
      html = html.replace(new RegExp(`__CODE_BLOCK_${idx}__`, 'g'), blockHtml);
    }
  });
  return html;
}
