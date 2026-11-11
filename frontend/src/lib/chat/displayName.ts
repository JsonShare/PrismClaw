// ---------------------------------------------------------------------------
// 上下文文件的展示归一化
//
// 后端路径是工作区根相对路径（如 sessions/<sid>/outputs/报告.docx），直接
// 当标题会把服务器内部结构暴露给用户；部分来源（图片 source_path、自动
// 落盘的文件名）本身就是 UUID/hex，同样不可读。
// 面板列表、搜索结果与输入框 chip 一律经本模块取标题，避免两套规则。
// ---------------------------------------------------------------------------

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const HEX32_RE = /^[0-9a-f]{32}$/i
const SESSION_PREFIX_RE = /^sessions\/[^/]+\//

/** 纯标识符（UUID 或 32 位 hex）不具可读性，不能作为标题。 */
function isOpaqueId(value: string): boolean {
  const s = value.trim()
  return UUID_RE.test(s) || HEX32_RE.test(s)
}

function basename(path: string): string {
  const clean = path.replace(/\\/g, '/').replace(/\/+$/, '')
  const idx = clean.lastIndexOf('/')
  return idx >= 0 ? clean.slice(idx + 1) : clean
}

/** 标题本身或其主干（去扩展名）是标识符时，同样视为不可读。 */
function isOpaqueTitle(title: string): boolean {
  if (isOpaqueId(title)) return true
  const dot = title.lastIndexOf('.')
  return dot > 0 && isOpaqueId(title.slice(0, dot))
}

export interface DisplayNameSource {
  name?: string | null
  path?: string | null
  type?: 'file' | 'image'
}

/**
 * 主标题：优先路径末段，其次名称末段；命中标识符时回退到名称；
 * 仍不可读时给中文兜底，绝不把 UUID 或服务器路径抛给用户。
 */
export function displayTitle(source: DisplayNameSource): string {
  const fromPath = source.path ? basename(source.path) : ''
  const fromName = source.name ? basename(source.name) : ''
  if (fromPath && !isOpaqueTitle(fromPath)) return fromPath
  if (fromName && !isOpaqueTitle(fromName)) return fromName
  return source.type === 'image' ? '图片' : '未命名文件'
}

/**
 * 副标题：相对目录，剥掉 sessions/<sid>/ 前缀。
 * 无目录（文件就在工作区根）时返回空串，由调用方决定是否渲染。
 */
export function relativeDir(path?: string | null): string {
  if (!path) return ''
  let clean = path.replace(/\\/g, '/')
  if (clean.startsWith('/')) {
    // 历史绝对路径：只保留工作区内的 sessions/ 之后部分；工作区外的目录不展示
    const marker = clean.indexOf('/sessions/')
    if (marker < 0) return ''
    clean = clean.slice(marker + 1)
  } else {
    clean = clean.replace(/^\.\//, '')
  }
  const withoutSession = clean.replace(SESSION_PREFIX_RE, '')
  const idx = withoutSession.lastIndexOf('/')
  return idx > 0 ? withoutSession.slice(0, idx) : ''
}

/** 单行中间省略：长目录保留首尾，尾段（最能区分文件的层级）不被截断。 */
export function middleEllipsis(value: string, max: number): string {
  if (value.length <= max) return value
  if (max <= 1) return value.slice(0, max)
  const keep = max - 1
  const head = Math.ceil(keep / 2)
  const tail = keep - head
  return `${value.slice(0, head)}…${value.slice(value.length - tail)}`
}
