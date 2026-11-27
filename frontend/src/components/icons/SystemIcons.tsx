import type { CSSProperties } from 'react'
import uploadIcon from '../../images/other_icon/upload.png'
import folderIcon from '../../images/other_icon/folder.png'
import fileIcon from '../../images/other_icon/file.png'
import linkIcon from '../../images/other_icon/link.png'
import refreshIcon from '../../images/other_icon/refresh.png'
import workIcon from '../../images/other_icon/work.png'
import skillIcon from '../../images/other_icon/skill.png'

/**
 * 系统统一图标（PNG → mask 渲染）。
 *
 * 用 mask + bg-current 而不是 <img>：图标自动吃掉 currentColor，
 * 普通/悬停/选中/主色按钮上都能跟随文字变色，明暗主题也不必各准备一套图。
 *
 * 各 PNG 画布同为 200×200，但图形留白不同（图形占画布 48%~90%），
 * 直接渲染会「有的满有的小」。这里按每张图的实际包围盒做比例缩放，
 * 让所有图标的视觉外框都约为 84%，与 24 网格、strokeWidth=2 的 lucide 图标观感一致。
 */
interface SystemIconProps {
  size?: number
  className?: string
}

/** 每张图标的缩放系数：0.84 / (图形最大边 / 画布) */
const ICON_SCALE: Record<string, number> = {
  upload: 1.06,
  folder: 0.99,
  file: 0.91,
  link: 0.93,
  refresh: 1.01,
  work: 1.06,
  skill: 1.15,
}

function MaskIcon({
  src,
  scale = 1,
  size = 16,
  className = '',
}: SystemIconProps & { src: string; scale?: number }) {
  const style: CSSProperties = {
    width: size,
    height: size,
    WebkitMaskImage: `url("${src}")`,
    maskImage: `url("${src}")`,
    WebkitMaskRepeat: 'no-repeat',
    maskRepeat: 'no-repeat',
    WebkitMaskPosition: 'center',
    maskPosition: 'center',
    WebkitMaskSize: `${scale * 100}%`,
    maskSize: `${scale * 100}%`,
  }
  return <span aria-hidden className={`inline-block shrink-0 bg-current ${className}`} style={style} />
}

/** 上传（含「上传文件夹」「上传文件」） */
export function UploadIcon(props: SystemIconProps) {
  return <MaskIcon src={uploadIcon} scale={ICON_SCALE.upload} {...props} />
}

/** 文件夹 / 工作区 / 文件库 */
export function FolderIcon(props: SystemIconProps) {
  return <MaskIcon src={folderIcon} scale={ICON_SCALE.folder} {...props} />
}

/** 文件 / 引用文件 */
export function FileIcon(props: SystemIconProps) {
  return <MaskIcon src={fileIcon} scale={ICON_SCALE.file} {...props} />
}

/** 连接器 / 链接 */
export function LinkIcon(props: SystemIconProps) {
  return <MaskIcon src={linkIcon} scale={ICON_SCALE.link} {...props} />
}

/** 刷新 / 重试 / 换一换（转圈动画由调用方的 className 提供） */
export function RefreshIcon(props: SystemIconProps) {
  return <MaskIcon src={refreshIcon} scale={ICON_SCALE.refresh} {...props} />
}

/** 专家 */
export function WorkIcon(props: SystemIconProps) {
  return <MaskIcon src={workIcon} scale={ICON_SCALE.work} {...props} />
}

/** 技能 */
export function SkillIcon(props: SystemIconProps) {
  return <MaskIcon src={skillIcon} scale={ICON_SCALE.skill} {...props} />
}
