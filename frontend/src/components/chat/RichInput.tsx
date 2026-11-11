import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef } from 'react'
import {
  chipLabel,
  chipIconHtml,
  encodeChip,
  splitMarkers,
  type ChipKind,
  type ChipRef,
} from '../../lib/chat/inputMarkers'

// ---------------------------------------------------------------------------
// RichInput - 可内联原子卡片的输入框
//
// 正文由「文本节点 + 卡片节点 + <br>」组成，卡片是 contenteditable=false 的
// 原子节点，可整体删除、不可从中间编辑。React 不接管编辑器子节点（全部由
// 本组件手工构建 DOM），否则浏览器改写 DOM 后与虚拟树不一致会互相打架。
// 对外只暴露标记串（见 lib/chat/inputMarkers），与历史/发送契约保持一致。
// ---------------------------------------------------------------------------

/** 默认预留 1.5 行正文高度，随字号/行高/上下内边距自适应；超过 MAX_HEIGHT 后滚动 */
const MIN_LINES = 1.5
const MAX_HEIGHT = 400

function minHeightOf(el: HTMLElement): number {
  const cs = getComputedStyle(el)
  const fontSize = parseFloat(cs.fontSize) || 15
  const lineHeight = parseFloat(cs.lineHeight) || fontSize * 1.5
  const padding = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0)
  return Math.round(padding + lineHeight * MIN_LINES)
}

export interface RichInputHandle {
  focus: () => void
  focusEnd: () => void
  blur: () => void
  getValue: () => string
  insertChip: (chip: ChipRef) => string
  removeChip: (kind: ChipKind, value: string) => string
  /** 删除光标前 length 个字符（用于吃掉 `/` `@` 触发串） */
  deleteBeforeCaret: (length: number) => string
}

export interface RichInputProps {
  value: string
  placeholder: string
  disabled?: boolean
  className: string
  onChange: (value: string) => void
  onSubmit: () => void
  /** 光标前文本变化（触发面板据此解析 `/` `@`） */
  onCaretChange: (textBeforeCaret: string) => void
  /** 键盘事件优先给宿主处理（`/` `@` 面板导航、Esc）；返回 true 表示已消费 */
  onKeyDown?: (e: React.KeyboardEvent<HTMLDivElement>) => boolean
  /** 剪贴板里的文件交给宿主上传 */
  onPasteFiles: (files: File[]) => void
}

function createChipEl(chip: ChipRef): HTMLSpanElement {
  const el = document.createElement('span')
  el.className = 'input-chip'
  el.contentEditable = 'false'
  el.dataset.chip = ''
  el.dataset.kind = chip.kind
  el.dataset.value = chip.value
  el.setAttribute('title', chip.value)
  // 图标不参与 flex 布局（CSS 里绝对定位居中）：inline-flex 的基线取自第一个
  // flex item，若图标参与布局（无基线）浏览器会用它底边合成基线，卡片文字比
  // 正文高约 1.5px。只留标签作为唯一 flex item，卡片基线即文字基线。
  el.innerHTML = '<span class="input-chip-label"></span>' + chipIconHtml(chip)
  const label = el.querySelector('.input-chip-label')
  if (label) label.textContent = chipLabel(chip)
  return el
}

function serializeNode(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent ?? ''
  if (node.nodeType !== Node.ELEMENT_NODE) return ''
  const el = node as HTMLElement
  if (el.hasAttribute('data-chip')) {
    return encodeChip({
      kind: (el.dataset.kind ?? 'file') as ChipKind,
      value: el.dataset.value ?? '',
    })
  }
  if (el.hasAttribute('data-filler')) return ''
  if (el.tagName === 'BR') return '\n'
  let out = ''
  el.childNodes.forEach((child) => { out += serializeNode(child) })
  return out
}

/**
 * 触发识别口径的光标前文本：卡片按一个空格计。
 *
 * 卡片序列化成标记串会让 `/` `@` 的边界判断失真（`<|skill|x|>@a` 里 `@`
 * 前面没有空白，`@` 面板不会弹出）；浏览器还会在行尾吞掉卡片后的空格，
 * 使这个问题成为常态。把卡片视为空白即可恢复「卡片不参与触发计算」。
 */
function serializeForTrigger(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent ?? ''
  if (node.nodeType !== Node.ELEMENT_NODE) return ''
  const el = node as HTMLElement
  if (el.hasAttribute('data-chip')) return ' '
  if (el.hasAttribute('data-filler')) return ''
  if (el.tagName === 'BR') return '\n'
  let out = ''
  el.childNodes.forEach((child) => { out += serializeForTrigger(child) })
  return out
}

/** 光标偏移口径的文本长度：文本按字符算，<br> 记 1，卡片不占位 */
function textLength(node: Node): number {
  if (node.nodeType === Node.TEXT_NODE) return (node.textContent ?? '').length
  if (node.nodeType !== Node.ELEMENT_NODE) return 0
  const el = node as HTMLElement
  if (el.hasAttribute('data-chip') || el.hasAttribute('data-filler')) return 0
  if (el.tagName === 'BR') return 1
  let len = 0
  el.childNodes.forEach((child) => { len += textLength(child) })
  return len
}

interface TextRun { node: Text; start: number; end: number }

function textRuns(root: HTMLElement): TextRun[] {
  const runs: TextRun[] = []
  let offset = 0
  const walk = (node: Node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node as Text
      const len = text.data.length
      runs.push({ node: text, start: offset, end: offset + len })
      offset += len
      return
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return
    const el = node as HTMLElement
    if (el.hasAttribute('data-chip') || el.hasAttribute('data-filler')) return
    if (el.tagName === 'BR') { offset += 1; return }
    el.childNodes.forEach(walk)
  }
  root.childNodes.forEach(walk)
  return runs
}

function pointAtOffset(root: HTMLElement, target: number): { node: Node; offset: number } | null {
  for (const run of textRuns(root)) {
    if (target <= run.start) return { node: run.node, offset: 0 }
    if (target <= run.end) return { node: run.node, offset: target - run.start }
  }
  return null
}

function textLengthBeforePoint(root: HTMLElement, node: Node, offset: number): number {
  const range = document.createRange()
  range.selectNodeContents(root)
  range.setEnd(node, offset)
  const frag = range.cloneContents()
  const holder = document.createElement('div')
  holder.appendChild(frag)
  return textLength(holder)
}

const RichInput = forwardRef<RichInputHandle, RichInputProps>(function RichInput(
  { value, placeholder, disabled = false, className, onChange, onSubmit, onCaretChange, onKeyDown, onPasteFiles },
  ref,
) {
  const editorRef = useRef<HTMLDivElement>(null)
  const lastValueRef = useRef(value)
  /** 最后一次落在编辑区内的选区（失焦后仍可用于插入/删除） */
  const lastRangeRef = useRef<Range | null>(null)
  const composingRef = useRef(false)
  const onChangeRef = useRef(onChange)
  const onSubmitRef = useRef(onSubmit)
  const onCaretChangeRef = useRef(onCaretChange)
  const onKeyDownRef = useRef(onKeyDown)
  const onPasteFilesRef = useRef(onPasteFiles)
  onChangeRef.current = onChange
  onSubmitRef.current = onSubmit
  onCaretChangeRef.current = onCaretChange
  onKeyDownRef.current = onKeyDown
  onPasteFilesRef.current = onPasteFiles

  const resize = useCallback(() => {
    const el = editorRef.current
    if (!el) return
    el.style.height = 'auto'
    const scrollH = el.scrollHeight
    el.style.height = `${Math.min(Math.max(scrollH, minHeightOf(el)), MAX_HEIGHT)}px`
    el.style.overflowY = scrollH > MAX_HEIGHT ? 'auto' : 'hidden'
  }, [])

  /** 光标前文本（触发识别口径：卡片按空格计，见 serializeForTrigger） */
  const caretTextBefore = useCallback((el: HTMLElement, range: Range): string => {
    const pre = range.cloneRange()
    pre.selectNodeContents(el)
    pre.setEnd(range.startContainer, range.startOffset)
    const holder = document.createElement('div')
    holder.appendChild(pre.cloneContents())
    return serializeForTrigger(holder)
  }, [])

  /** 当前编辑区内选区；焦点在别处（如面板搜索框）时回落到最后一次光标位置 */
  const currentRange = useCallback((el: HTMLElement): Range | null => {
    const sel = window.getSelection()
    if (sel && sel.rangeCount > 0) {
      const range = sel.getRangeAt(0)
      if (el.contains(range.startContainer)) return range
    }
    const saved = lastRangeRef.current
    if (saved && el.contains(saved.startContainer)) return saved
    return null
  }, [])

  /** 选区只在编辑区有焦点时写回，避免抢走面板搜索框的选区 */
  const applyRange = useCallback((el: HTMLElement, range: Range) => {
    if (document.activeElement === el) {
      const sel = window.getSelection()
      sel?.removeAllRanges()
      sel?.addRange(range)
    }
    lastRangeRef.current = range.cloneRange()
  }, [])

  const emitCaret = useCallback(() => {
    const el = editorRef.current
    if (!el) return
    const range = currentRange(el)
    if (!range) return
    lastRangeRef.current = range.cloneRange()
    onCaretChangeRef.current(caretTextBefore(el, range))
  }, [caretTextBefore, currentRange])

  const emit = useCallback(() => {
    const el = editorRef.current
    if (!el) return ''
    resize()
    const next = serializeNode(el)
    lastValueRef.current = next
    el.dataset.empty = next.length === 0 ? 'true' : 'false'
    onChangeRef.current(next)
    emitCaret()
    return next
  }, [resize, emitCaret])

  const ensureFiller = useCallback(() => {
    const el = editorRef.current
    if (!el) return
    el.querySelectorAll('[data-filler]').forEach((node) => node.remove())
    // Range.insertNode 在文本末尾插入时会分裂出空文本节点，它会让「末尾是否 br」
    // 判断失效，先清掉（空节点不承载光标内容，删除无副作用）。
    let last = el.lastChild
    while (last && last.nodeType === Node.TEXT_NODE && !(last.textContent ?? '')) {
      const prev = last.previousSibling
      last.remove()
      last = prev
    }
    if (!last) return
    // 末尾是换行时必须补一个占位 br，否则浏览器把最后一个 br 当作行尾占位符，
    // 继续输入的字符会跑到换行之前。
    if (last.nodeName === 'BR') {
      const filler = document.createElement('br')
      filler.setAttribute('data-filler', '')
      el.appendChild(filler)
    }
  }, [])

  const appendText = useCallback((parent: Node, text: string) => {
    const lines = text.split('\n')
    lines.forEach((line, i) => {
      if (i > 0) parent.appendChild(document.createElement('br'))
      if (line) parent.appendChild(document.createTextNode(line))
    })
  }, [])

  /** 用标记串重建整个编辑器（外部 value 变化时的唯一入口） */
  const rebuild = useCallback((text: string) => {
    const el = editorRef.current
    if (!el) return
    const frag = document.createDocumentFragment()
    for (const seg of splitMarkers(text)) {
      if (seg.type === 'text') appendText(frag, seg.text)
      else frag.appendChild(createChipEl(seg.chip))
    }
    el.textContent = ''
    el.appendChild(frag)
    // 受控值以换行结尾时同理：末尾 <br> 需要占位 br，否则后续输入会跑到换行之前
    ensureFiller()
    lastValueRef.current = text
    lastRangeRef.current = null
    el.dataset.empty = text.length === 0 ? 'true' : 'false'
    resize()
  }, [appendText, ensureFiller, resize])

  const placeCaretAtEnd = useCallback(() => {
    const el = editorRef.current
    if (!el) return
    const range = document.createRange()
    range.selectNodeContents(el)
    range.collapse(false)
    const sel = window.getSelection()
    sel?.removeAllRanges()
    sel?.addRange(range)
  }, [])

  // 外部 value 变化（发送后清空 / 提示词优化 / 语音识别）才重建 DOM；
  // 自身输入触发的 onChange 与 lastValueRef 相等，不重建，光标不跳。
  useEffect(() => {
    if (value === lastValueRef.current) return
    if (composingRef.current) return
    const el = editorRef.current
    const focused = !!el && document.activeElement === el
    rebuild(value)
    if (focused) {
      placeCaretAtEnd()
      emitCaret()
    }
  }, [value, rebuild, placeCaretAtEnd, emitCaret])

  // 首次挂载 / 无历史值时同步一次（避免受控值为初始文本时空白）
  useEffect(() => {
    const el = editorRef.current
    if (!el) return
    if (el.childNodes.length === 0) rebuild(value)
  }, [rebuild, value])

  // 光标移动（方向键、点击、拖选）不触发 input，需单独同步触发面板
  useEffect(() => {
    const handler = () => {
      if (document.activeElement !== editorRef.current) return
      emitCaret()
    }
    document.addEventListener('selectionchange', handler)
    return () => document.removeEventListener('selectionchange', handler)
  }, [emitCaret])

  const insertLineBreak = useCallback(() => {
    const el = editorRef.current
    if (!el) return
    const range = currentRange(el) ?? (() => {
      const r = document.createRange()
      r.selectNodeContents(el)
      r.collapse(false)
      return r
    })()
    range.deleteContents()
    const br = document.createElement('br')
    range.insertNode(br)
    const after = document.createRange()
    after.setStartAfter(br)
    after.collapse(true)
    ensureFiller()
    applyRange(el, after)
    emit()
  }, [applyRange, currentRange, emit, ensureFiller])

  const deleteBeforeCaret = useCallback((length: number) => {
    const el = editorRef.current
    if (!el || length <= 0) return el ? serializeNode(el) : ''
    const range = currentRange(el)
    if (!range) return serializeNode(el)
    if (!range.collapsed) {
      range.deleteContents()
    }
    const caret = textLengthBeforePoint(el, range.startContainer, range.startOffset)
    const target = Math.max(0, caret - length)
    const start = pointAtOffset(el, target)
    if (!start) return serializeNode(el)
    const del = document.createRange()
    del.setStart(start.node, start.offset)
    del.setEnd(range.startContainer, range.startOffset)
    del.deleteContents()
    const after = document.createRange()
    after.setStart(start.node, start.offset)
    after.collapse(true)
    applyRange(el, after)
    return emit()
  }, [applyRange, currentRange, emit])

  const insertChip = useCallback((chip: ChipRef) => {
    const el = editorRef.current
    if (!el) return ''
    // 同一张卡片只保留一个：技能/专家/文件都可叠加，重复插入没有意义。
    const duplicated = Array.from(el.querySelectorAll<HTMLElement>('[data-chip]'))
      .some((node) => node.dataset.kind === chip.kind && node.dataset.value === chip.value)
    if (duplicated) return serializeNode(el)
    let range = currentRange(el)
    if (!range) {
      range = document.createRange()
      range.selectNodeContents(el)
      range.collapse(false)
    }
    range.deleteContents()
    const chipEl = createChipEl(chip)
    range.insertNode(chipEl)
    const space = document.createTextNode(' ')
    chipEl.parentNode?.insertBefore(space, chipEl.nextSibling)
    const after = document.createRange()
    after.setStartAfter(space)
    after.collapse(true)
    applyRange(el, after)
    return emit()
  }, [applyRange, currentRange, emit])

  const removeChip = useCallback((kind: ChipKind, value: string) => {
    const el = editorRef.current
    if (!el) return ''
    const target = Array.from(el.querySelectorAll<HTMLElement>('[data-chip]'))
      .find((node) => node.dataset.kind === kind && node.dataset.value === value)
    if (!target) return serializeNode(el)
    // 卡片插入时带了一个尾随空格，整体移除避免留下双空格。
    // 不动光标：多数调用方（面板取消勾选）焦点不在卡片处，移动光标会打断输入。
    const next = target.nextSibling
    if (next && next.nodeType === Node.TEXT_NODE && (next.textContent ?? '').startsWith(' ')) {
      next.textContent = (next.textContent ?? '').slice(1)
    }
    target.remove()
    return emit()
  }, [emit])

  /** 光标紧邻卡片时整卡删除；返回是否已处理 */
  const removeAdjacentChip = useCallback((direction: 'backward' | 'forward'): boolean => {
    const el = editorRef.current
    const sel = window.getSelection()
    if (!el || !sel || sel.rangeCount === 0 || !sel.isCollapsed) return false
    const range = sel.getRangeAt(0)
    if (!el.contains(range.startContainer)) return false
    const { startContainer, startOffset } = range
    let neighbor: Node | null = null
    if (startContainer.nodeType === Node.ELEMENT_NODE) {
      neighbor = direction === 'backward'
        ? startContainer.childNodes[startOffset - 1] ?? null
        : startContainer.childNodes[startOffset] ?? null
    } else if (direction === 'backward' ? startOffset === 0 : startOffset === (startContainer.textContent ?? '').length) {
      neighbor = direction === 'backward' ? startContainer.previousSibling : startContainer.nextSibling
    }
    if (!neighbor || neighbor.nodeType !== Node.ELEMENT_NODE) return false
    const chipEl = neighbor as HTMLElement
    if (!chipEl.hasAttribute('data-chip')) return false
    const parent = chipEl.parentNode
    if (!parent) return false
    const index = Array.prototype.indexOf.call(parent.childNodes, chipEl)
    const next = chipEl.nextSibling
    if (next && next.nodeType === Node.TEXT_NODE && (next.textContent ?? '').startsWith(' ')) {
      next.textContent = (next.textContent ?? '').slice(1)
    }
    chipEl.remove()
    const after = document.createRange()
    after.setStart(parent, Math.min(Math.max(index, 0), parent.childNodes.length))
    after.collapse(true)
    sel.removeAllRanges()
    sel.addRange(after)
    emit()
    return true
  }, [emit])

  useImperativeHandle(ref, () => ({
    focus: () => {
      const el = editorRef.current
      if (!el) return
      el.focus()
      const saved = lastRangeRef.current
      if (saved && el.contains(saved.startContainer)) {
        const sel = window.getSelection()
        sel?.removeAllRanges()
        sel?.addRange(saved)
      }
    },
    focusEnd: () => {
      const el = editorRef.current
      if (!el) return
      el.focus()
      placeCaretAtEnd()
      emitCaret()
    },
    blur: () => editorRef.current?.blur(),
    getValue: () => (editorRef.current ? serializeNode(editorRef.current) : ''),
    insertChip,
    removeChip,
    deleteBeforeCaret,
  }), [deleteBeforeCaret, emitCaret, insertChip, placeCaretAtEnd, removeChip])

  const handleInput = useCallback(() => {
    if (composingRef.current) return
    emit()
  }, [emit])

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.nativeEvent.isComposing || composingRef.current) return
    if (onKeyDownRef.current?.(e)) {
      e.preventDefault()
      return
    }
    if (e.key === 'Enter') {
      if (e.repeat) return
      e.preventDefault()
      if (e.shiftKey) insertLineBreak()
      else onSubmitRef.current()
      return
    }
    if (e.key === 'Backspace' && removeAdjacentChip('backward')) {
      e.preventDefault()
      return
    }
    if (e.key === 'Delete' && removeAdjacentChip('forward')) {
      e.preventDefault()
    }
  }, [insertLineBreak, removeAdjacentChip])

  const handlePaste = useCallback((e: React.ClipboardEvent<HTMLDivElement>) => {
    const dt = e.clipboardData
    if (!dt) return
    const files: File[] = []
    for (const item of dt.items) {
      if (item.kind === 'file') {
        const file = item.getAsFile()
        if (file) files.push(file)
      }
    }
    // 粘贴内容一律按纯文本写入，杜绝外部 HTML 污染正文结构
    e.preventDefault()
    if (files.length > 0) onPasteFilesRef.current(files)
    const text = dt.getData('text/plain').replace(/\r\n?/g, '\n')
    if (!text) return
    const sel = window.getSelection()
    if (!sel || sel.rangeCount === 0) return
    const range = sel.getRangeAt(0)
    range.deleteContents()
    const frag = document.createDocumentFragment()
    appendText(frag, text)
    const lastNode = frag.lastChild
    range.insertNode(frag)
    if (lastNode) {
      const after = document.createRange()
      after.setStartAfter(lastNode)
      after.collapse(true)
      // 与 Shift+Enter 同理：粘贴内容以换行结尾时，末尾的 <br> 会被浏览器当成
      // 行尾占位符，后续输入的字符会跑到换行之前，必须补一个 filler br。
      ensureFiller()
      sel.removeAllRanges()
      sel.addRange(after)
    }
    emit()
  }, [appendText, emit, ensureFiller])

  return (
    <div
      ref={editorRef}
      className={`rich-input ${className}`}
      contentEditable={!disabled}
      suppressContentEditableWarning
      role="textbox"
      aria-multiline="true"
      aria-label={placeholder}
      data-placeholder={placeholder}
      data-empty={value.length === 0 ? 'true' : 'false'}
      onInput={handleInput}
      onKeyDown={handleKeyDown}
      onPaste={handlePaste}
      // 拖入的文本/HTML 不允许浏览器直接插入（会污染正文结构）；
      // 文件拖拽仍由外层 form 的 onDrop 处理（事件继续冒泡）
      onDrop={(e) => e.preventDefault()}
      onCompositionStart={() => { composingRef.current = true }}
      onCompositionEnd={() => {
        composingRef.current = false
        emit()
      }}
      onFocus={() => emitCaret()}
    />
  )
})

export default RichInput
