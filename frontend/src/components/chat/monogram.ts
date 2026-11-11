// 生成参考设计风格的字母图标字符：取名称首字符并大写。
// 用于技能 / 插件列表项左侧的「蓝底白字」方块图标（替代 emoji / 通用图标占位符）。
export function monogram(name: string): string {
  const trimmed = name.trim()
  if (!trimmed) return '?'
  return trimmed.slice(0, 1).toUpperCase()
}
