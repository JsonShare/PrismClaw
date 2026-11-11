import { displayTitle } from './displayName'
import { fileIconSrc } from '../../components/file/FileIcon'

// ---------------------------------------------------------------------------
// 输入框内联卡片 ↔ 正文标记串
//
// 卡片是正文的一部分：DOM 里是 contenteditable=false 的原子节点，序列化后
// 变成 `<|kind|percent-encoded|>`。标记随正文一起发送，后端解析出
// designated_skills / designated_plugins / referenced_files；前端不再单发这几个
// 字段，正文是唯一真相源。
//
// 值用 encodeURIComponent 编码（`|`、`<`、`>` 都会被转义），因此标记内部
// 不会出现分隔符，正则只需匹配非 `|<>` 字符。
// ---------------------------------------------------------------------------

export type ChipKind = 'skill' | 'plugin' | 'file'

export interface ChipRef {
  kind: ChipKind
  /** 技能/专家名，或工作区根相对的文件路径 */
  value: string
}

/** 标记串 → 分段（文本 / 卡片），未知或不完整标记按普通文本保留 */
export type InputSegment =
  | { type: 'text'; text: string }
  | { type: 'chip'; chip: ChipRef }

const MARKER_RE = /<\|(skill|plugin|file)\|([^|<>]*)\|>/g

/** 技能 / 专家卡片图标的内联路径（lucide hammer / blocks），文件卡片走扩展名图标 */
export const CHIP_ICON_MARKUP: Record<'skill' | 'plugin', string> = {
  skill:
    '<path d="m15 12-8.373 8.373a1 1 0 1 1-3-3L12 9"/>'
    + '<path d="m18 15 4-4"/>'
    + '<path d="m21.5 11.5-1.914-1.914A2 2 0 0 1 19 8.172V7l-2.26-2.26a6 6 0 0 0-4.202-1.756L9 2.96l.92.82A6.18 6.18 0 0 1 12 8.4V10l2 2h1.172a2 2 0 0 1 1.414.586L18.5 14.5"/>',
  plugin:
    '<rect width="7" height="7" x="14" y="3" rx="1"/>'
    + '<path d="M10 21V8a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-5a1 1 0 0 0-1-1H3"/>',
}

const CHIP_SVG_ATTRS =
  'viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" '
  + 'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"'

/** 卡片图标 DOM 片段：技能用锤子、专家用积木、文件用扩展名图标（三者不共用） */
export function chipIconHtml(chip: ChipRef): string {
  if (chip.kind === 'file') {
    return `<img class="chip-icon-img" src="${fileIconSrc(chip.value)}" alt="" width="14" height="14" draggable="false" />`
  }
  return `<svg class="chip-icon-svg" ${CHIP_SVG_ATTRS}>${CHIP_ICON_MARKUP[chip.kind]}</svg>`
}

function safeDecode(raw: string): string {
  try {
    return decodeURIComponent(raw)
  } catch {
    return raw
  }
}

/** 卡片在界面上的标题：文件取可读文件名，能力取名称本身 */
export function chipLabel(chip: ChipRef): string {
  return chip.kind === 'file' ? displayTitle({ path: chip.value }) : chip.value
}

export function encodeChip(chip: ChipRef): string {
  return `<|${chip.kind}|${encodeURIComponent(chip.value)}|>`
}

export function splitMarkers(text: string): InputSegment[] {
  const segments: InputSegment[] = []
  let last = 0
  MARKER_RE.lastIndex = 0
  for (let m = MARKER_RE.exec(text); m; m = MARKER_RE.exec(text)) {
    if (m.index > last) segments.push({ type: 'text', text: text.slice(last, m.index) })
    segments.push({
      type: 'chip',
      chip: { kind: m[1] as ChipKind, value: safeDecode(m[2]) },
    })
    last = m.index + m[0].length
  }
  if (last < text.length) segments.push({ type: 'text', text: text.slice(last) })
  return segments
}

/** 正文里是否含卡片标记（纯文本消息仍走 Markdown 渲染） */
export function hasMarkers(text: string): boolean {
  if (!text.includes('<|')) return false
  return splitMarkers(text).some((seg) => seg.type === 'chip')
}

/** 正文里是否有卡片以外的文字：只有卡片不构成有效提问 */
export function hasPlainText(text: string): boolean {
  return splitMarkers(text).some((seg) => seg.type === 'text' && seg.text.trim().length > 0)
}

/** 模型可读文本：卡片 → `@标题`（路径信息由 referenced_files 单独注入） */
export function markersToReadable(text: string): string {
  return splitMarkers(text)
    .map((seg) => (seg.type === 'text' ? seg.text : `@${chipLabel(seg.chip)}`))
    .join('')
}

/** 正文中出现的卡片（按出现顺序去重） */
export function collectChips(text: string): ChipRef[] {
  const seen = new Set<string>()
  const chips: ChipRef[] = []
  for (const seg of splitMarkers(text)) {
    if (seg.type !== 'chip') continue
    const key = `${seg.chip.kind}:${seg.chip.value}`
    if (seen.has(key)) continue
    seen.add(key)
    chips.push(seg.chip)
  }
  return chips
}

/** 当前正文里已激活的技能 / 专家（均可多个，按出现顺序去重） */
export function designationOf(text: string): { skills: string[]; plugins: string[] } {
  const chips = collectChips(text)
  return {
    skills: chips.filter((c) => c.kind === 'skill').map((c) => c.value),
    plugins: chips.filter((c) => c.kind === 'plugin').map((c) => c.value),
  }
}
