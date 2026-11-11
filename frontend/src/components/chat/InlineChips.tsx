import { Fragment } from 'react'
import { CHIP_ICON_MARKUP, chipLabel, splitMarkers, type ChipRef } from '../../lib/chat/inputMarkers'
import { fileIconSrc } from '../file/FileIcon'

// ---------------------------------------------------------------------------
// 气泡里的内联卡片：与输入框共用同一套标记契约（lib/chat/inputMarkers），
// 只读渲染。视觉参照已发送气泡的参考图：浅蓝底、主题蓝字、无描边、与正文同行。
// 结构与输入框一致——标签在前（决定基线）、图标绝对定位居中，避免
// inline-flex 用无基线图标合成基线导致文字偏移。
// ---------------------------------------------------------------------------

/** 卡片图标与输入框同源：技能锤子、专家积木、文件按扩展名 */
function ChipIcon({ chip }: { chip: ChipRef }) {
  if (chip.kind === 'file') {
    return (
      <img
        className="chip-icon-img"
        src={fileIconSrc(chip.value)}
        alt=""
        width={14}
        height={14}
        draggable={false}
      />
    )
  }
  return (
    <svg
      className="chip-icon-svg"
      viewBox="0 0 24 24"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      dangerouslySetInnerHTML={{ __html: CHIP_ICON_MARKUP[chip.kind] }}
    />
  )
}

export default function InlineChips({ text }: { text: string }) {
  return (
    <>
      {splitMarkers(text).map((seg, i) =>
        seg.type === 'text' ? (
          <Fragment key={i}>{seg.text}</Fragment>
        ) : (
          <span key={i} className="msg-chip" title={seg.chip.value}>
            <span className="msg-chip-label">{chipLabel(seg.chip)}</span>
            <ChipIcon chip={seg.chip} />
          </span>
        ),
      )}
    </>
  )
}
