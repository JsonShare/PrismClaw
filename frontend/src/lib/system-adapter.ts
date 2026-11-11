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

/** 在新标签页打开外链。 */
export const openExternal = (url: string): void => {
  window.open(url, '_blank', 'noopener,noreferrer')
}
