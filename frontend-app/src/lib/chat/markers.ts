/**
 * 输入框内联卡片标记协议：`<|kind|percent-encoded-value|>`。
 *
 * 与后端 `chat_mentions.extract_mentions` 一一对应：技能/专家/引用文件
 * 序列化进正文随 content 一起发送，正文是唯一真相源；后端拆出
 * designated_skills / designated_plugins / referenced_files 并把正文
 * 还原成模型可读的 `@标题`。展示历史消息时同样需要还原，否则会看到原始标记。
 */

export type ChipKind = 'skill' | 'plugin' | 'file'

export interface Chip {
  /** `kind:value`，用于去重与列表 key */
  id: string
  kind: ChipKind
  /** 技能/专家名或工作区相对路径 */
  value: string
  /** 卡片上展示的短标题 */
  label: string
}

const MARKER_RE = /<\|(skill|plugin|file)\|([^|<>]*)\|>/g

function safeDecode(raw: string): string {
  try {
    return decodeURIComponent(raw)
  } catch {
    return raw
  }
}

function fileNameOf(path: string): string {
  const name = path.replace(/\\/g, '/').split('/').pop() || path
  return name || path
}

export function makeChip(kind: ChipKind, value: string, label?: string): Chip {
  return {
    id: `${kind}:${value}`,
    kind,
    value,
    label: label || (kind === 'file' ? fileNameOf(value) : value),
  }
}

function encodeChip(chip: Pick<Chip, 'kind' | 'value'>): string {
  return `<|${chip.kind}|${encodeURIComponent(chip.value)}|>`
}

/** 已选卡片放在正文前，空正文时只发卡片 */
export function composeContent(text: string, chips: Chip[]): string {
  if (!chips.length) return text
  const head = chips.map(encodeChip).join('')
  return text ? `${head}\n${text}` : head
}

/** 展示用：把标记还原成 `@标题` */
export function markersToReadable(text: string): string {
  if (!text.includes('<|')) return text
  return text.replace(MARKER_RE, (_match, kind: string, raw: string) => {
    const value = safeDecode(raw)
    return `@${kind === 'file' ? fileNameOf(value) : value}`
  })
}
