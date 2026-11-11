// ---------------------------------------------------------------------------
// 输入框 `/` `@` 触发解析
//
// `/` 与 `@` 都要求在行首或空白之后：`and/or`、URL、邮箱不触发。区别只在于
// 输入源：由 contenteditable 提供「光标前文本」，不再依赖 selectionStart。
// 卡片在光标前文本里按一个空格计（见 RichInput.serializeForTrigger），因此
// 卡片后继续输入 `/` `@` 同样能触发。
// ---------------------------------------------------------------------------

export interface InputTrigger {
  kind: 'slash' | 'at'
  query: string
  /** 触发串长度（含触发符），选中后从光标前整段删除 */
  length: number
}

const SLASH_RE = /(?:^|\s)\/(\S*)$/
const AT_RE = /(?:^|\s)@(\S*)$/

export function parseTrigger(textBeforeCaret: string): InputTrigger | null {
  const slash = SLASH_RE.exec(textBeforeCaret)
  if (slash) return { kind: 'slash', query: slash[1], length: slash[1].length + 1 }
  const at = AT_RE.exec(textBeforeCaret)
  if (at) return { kind: 'at', query: at[1], length: at[1].length + 1 }
  return null
}
