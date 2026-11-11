import { Image } from '@tarojs/components'

// 与 PC 端 frontend/src/components/file/FileIcon.tsx 共用同一套图标资源
import classIcon from '../../assets/images/file_icon/WEIZHI.png'
import codeIcon from '../../assets/images/file_icon/CODE.png'
import configIcon from '../../assets/images/file_icon/CONFIG.png'
import defaultFileIcon from '../../assets/images/file_icon/WENJIAN.png'
import docIcon from '../../assets/images/file_icon/WORD.png'
import emlIcon from '../../assets/images/file_icon/EML.png'
import gifIcon from '../../assets/images/file_icon/GIF.png'
import htmlIcon from '../../assets/images/file_icon/HTML.png'
import jpgIcon from '../../assets/images/file_icon/TUPIAN.png'
import jsonIcon from '../../assets/images/file_icon/JSON.png'
import mdIcon from '../../assets/images/file_icon/MARKDOWN.png'
import mp3Icon from '../../assets/images/file_icon/YINLE.png'
import mp4Icon from '../../assets/images/file_icon/SHIPIN.png'
import movIcon from '../../assets/images/file_icon/MOV.png'
import pdfIcon from '../../assets/images/file_icon/PDF.png'
import pngIcon from '../../assets/images/file_icon/PNG.png'
import pptIcon from '../../assets/images/file_icon/PPT.png'
import rarIcon from '../../assets/images/file_icon/RAR.png'
import svgIcon from '../../assets/images/file_icon/SVG.png'
import txtIcon from '../../assets/images/file_icon/WENDANG.png'
import xlsIcon from '../../assets/images/file_icon/EXCEL.png'
import zipIcon from '../../assets/images/file_icon/ZIP.png'

const normalizeExt = (ext: string) => (ext || '').trim().replace(/^\./, '').toLowerCase()

const extFromFilename = (filename: string) => {
  const name = (filename || '').trim()
  const dot = name.lastIndexOf('.')
  return dot < 0 ? '' : normalizeExt(name.slice(dot + 1))
}

const CODE_EXT = [
  'js', 'jsx', 'ts', 'tsx', 'mjs', 'cjs', 'py', 'java', 'cs', 'css', 'sql', 'c', 'h', 'cc',
  'cpp', 'cxx', 'hpp', 'hxx', 'hh', 'go', 'rs', 'php', 'rb', 'pl', 'sh', 'bash', 'zsh', 'ps1',
  'bat', 'cmd', 'coffee', 'asm', 's', 'scala', 'swift', 'kt', 'kts', 'dart', 'lua', 'r',
  'groovy', 'gradle', 'mk', 'diff', 'patch', 'bundle', 'bdl', 'ipynb', 'proto', 'hcl', 'tf',
  'tex', 'gv', 'http', 'rest', 'graphql', 'feature'
]

const CONFIG_EXT = ['ini', 'toml', 'cfg', 'conf', 'env', 'properties', 'lock', 'editorconfig', 'jsonc', 'json5', 'yml', 'yaml']

function resolveIcon(ext: string, filename: string): string {
  const e = normalizeExt(ext)
  const name = (filename || '').trim().toLowerCase()

  if (name === 'dockerfile' || name.startsWith('dockerfile.')) return txtIcon
  if (name === '.gitignore' || e === 'gitignore') return txtIcon
  if (e === 'eml') return emlIcon
  if (e === 'doc' || e === 'docx' || e === 'rtf') return docIcon
  if (e === 'xls' || e === 'xlsx' || e === 'csv' || e === 'tsv' || e === 'gsheet') return xlsIcon
  if (e === 'ppt' || e === 'pptx' || e === 'key') return pptIcon
  if (e === 'pdf') return pdfIcon
  if (e === 'html' || e === 'htm') return htmlIcon
  if (['zip', 'rar', '7z', 'tar', 'gz', 'tgz'].includes(e)) return e === 'rar' ? rarIcon : zipIcon
  if (e === 'png') return pngIcon
  if (e === 'jpg' || e === 'jpeg') return jpgIcon
  if (e === 'gif') return gifIcon
  if (e === 'svg') return svgIcon
  if (['webp', 'bmp', 'ico', 'tiff', 'tif'].includes(e)) return jpgIcon
  if (CODE_EXT.includes(e)) return codeIcon
  if (CONFIG_EXT.includes(e)) return configIcon
  if (e === 'jar' || e === 'class') return classIcon
  if (e === 'json') return jsonIcon
  if (e === 'md' || e === 'markdown') return mdIcon
  if (e === 'txt' || e === 'log' || e === 'xml') return txtIcon
  if (['mp3', 'wav', 'flac', 'm4a', 'aac', 'ogg'].includes(e)) return mp3Icon
  if (e === 'mov') return movIcon
  if (['mp4', 'avi', 'mkv', 'webm'].includes(e)) return mp4Icon
  return defaultFileIcon
}

export interface FileIconProps {
  filename?: string
  ext?: string
  size?: number
  className?: string
}

/** 按扩展名映射文件类型图标，规则与 PC 端保持一致。 */
export default function FileIcon({ filename = '', ext, size = 24, className }: FileIconProps) {
  const resolved = ext ? normalizeExt(ext) : extFromFilename(filename)
  return (
    <Image
      className={className}
      src={resolveIcon(resolved, filename)}
      mode='aspectFit'
      style={{ width: `${size}px`, height: `${size}px` }}
    />
  )
}
