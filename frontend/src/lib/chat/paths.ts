/**
 * 工作区路径归一化（纯函数）。
 * 云端模式铁律：只接受工作区相对路径，一律拒绝绝对路径 / URL / 域名形文本。
 * 消费方：ChatPage 文件预览链、右侧栏交付物/上传列表。
 */
export function normalizeWorkspacePath(raw: string): string | null {
  const text = (raw || '').trim()
  if (!text) return null
  if (text.includes('\n') || text.includes('\r')) return null
  if (text.startsWith('http://') || text.startsWith('https://') || text.startsWith('file://')) return null

  let p = text
  if (
    (p.startsWith('"') && p.endsWith('"')) ||
    (p.startsWith("'") && p.endsWith("'")) ||
    (p.startsWith('`') && p.endsWith('`'))
  ) {
    p = p.slice(1, -1).trim()
  }

  p = p.replace(/\\+/g, '/')
  p = p.replace(/^\.\//, '')
  if (/^[a-z0-9.-]+\.(com|cn|net|org|io|ai|co|gov|edu)\//i.test(p) || p.startsWith('www.')) return null

  const isAbs = (value: string): boolean => {
    if (!value) return false
    if (value.startsWith('/')) return true
    if (/^[a-z]:\//i.test(value)) return true
    if (value.startsWith('//')) return true
    return false
  }

  const internalMarker = '/.prismclaw/workspaces/'
  const inboxMarker = '/inbox/'
  const internalIdx = p.indexOf(internalMarker)
  if (internalIdx >= 0) {
    const inboxIdx = p.indexOf(inboxMarker, internalIdx + internalMarker.length)
    if (inboxIdx >= 0) {
      p = p.slice(inboxIdx + inboxMarker.length)
    }
  }

  // 云端模式：始终拒绝绝对路径（不再有本地绑定工作区根目录可相对解析）
  if (isAbs(p)) return null

  p = p.replace(/^\/+/g, '')
  if (!p || p.startsWith('../')) return null
  return p
}
