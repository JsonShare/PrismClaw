import type { FileBlock, ImageBlock, Message } from '../../types/chat'

// ---------------------------------------------------------------------------
// 上下文引用（`@` / ➕ 引用文件）的统一数据模型
//
// 只有带真实工作区路径的条目才进入模型：无路径的历史附件（内联图片）不可
// 引用，也不在面板里出现——能选却不能发比不显示更糟。path 因此必填，既是
// 发送给后端的引用路径，也是唯一标识（同一路径不可能重复引用）。
// ---------------------------------------------------------------------------

export interface ContextRef {
  /** 工作区根相对路径：发送参数，兼作去重/选中标识 */
  path: string
  /** 可读名称（路径末段为 UUID 时用于兜底展示） */
  name: string
  type: 'file' | 'image'
}

const IMAGE_EXT = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'])

function fileTypeOf(path: string): 'file' | 'image' {
  const ext = path.split('.').pop()?.toLowerCase() ?? ''
  return IMAGE_EXT.has(ext) ? 'image' : 'file'
}

function refOf(path: string, name: string): ContextRef {
  return { path, name, type: fileTypeOf(path) }
}

/** 工作区文件（搜索命中 / 最近产出物）→ ContextRef */
export function workspaceRef(path: string, name?: string): ContextRef {
  return refOf(path, name || path.split('/').pop() || path)
}

/** 从会话历史中提取可引用的文件与图片附件（仅保留有工作区路径的） */
export function conversationRefs(messages: Message[]): ContextRef[] {
  const refs: ContextRef[] = []
  const seen = new Set<string>()
  const push = (path: string, name: string) => {
    if (!path || seen.has(path)) return
    seen.add(path)
    refs.push(refOf(path, name || path))
  }

  for (const msg of messages) {
    if (!Array.isArray(msg.content)) continue
    for (const block of msg.content) {
      if (block.type === 'file') {
        const fb = block as FileBlock
        const path = (fb.path || '').trim()
        push(path, fb.name || path.split('/').pop() || path)
      } else if (block.type === 'image') {
        const ib = block as ImageBlock
        const path = (ib.source_path || '').trim()
        push(path, path.split('/').pop() || '图片')
      }
    }
  }

  return refs
}
