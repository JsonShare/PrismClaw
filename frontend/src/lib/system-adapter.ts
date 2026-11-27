/**
 * 纯 Web 适配层。
 *
 * 云端 B/S 架构下前端与后端同源，不再需要 Electron IPC 桥接。
 * 所有本地文件操作（open_default / reveal / pickDirectory）已在
 * L2-L3 移除 —— 云端用户无法触达服务器本地文件系统。
 */

/** 拼接同源 API URL。path 以 / 开头时直接返回（同源），绝对 URL 原样返回。 */
export const apiUrl = (path: string): string => {
  if (!path) return path
  if (path.startsWith('http://') || path.startsWith('https://')) return path
  return path
}

/**
 * 为受保护资源 URL 附加 `_token` 查询参数。
 *
 * `<img>` / `<video>` 等标签无法携带 Authorization 头，只能靠查询参数鉴权
 * （后端 get_current_user 两种方式都接受）。这是前端**唯一**的 token 拼装方式，
 * 避免各组件各写一遍分隔符判断。
 */
export const authedUrl = (path: string, token: string | null | undefined): string => {
  const url = apiUrl(path)
  if (!url || !token) return url
  const sep = url.includes('?') ? '&' : '?'
  return `${url}${sep}_token=${encodeURIComponent(token)}`
}

/** 在新标签页打开外链。 */
export const openExternal = (url: string): void => {
  window.open(url, '_blank', 'noopener,noreferrer')
}
