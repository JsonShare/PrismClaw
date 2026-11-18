import wordIcon from '../../images/file_icon/WORD.png'
import pdfIcon from '../../images/file_icon/PDF.png'
import excelIcon from '../../images/file_icon/EXCEL.png'
import pptIcon from '../../images/file_icon/PPT.png'
import markdownIcon from '../../images/file_icon/MARKDOWN.png'
import txtIcon from '../../images/file_icon/TXT.png'
import codeIcon from '../../images/file_icon/CODE.png'
import svgIcon from '../../images/file_icon/SVG.png'
import imageIcon from '../../images/file_icon/TUPIAN.png'
import audioIcon from '../../images/file_icon/YINLE.png'
import videoIcon from '../../images/file_icon/SHIPIN.png'
import zipIcon from '../../images/file_icon/ZIP.png'
import defaultFileIcon from '../../images/file_icon/WENJIAN.png'

function normalizeExt(ext: string): string {
  return (ext || '').trim().replace(/^\./, '').toLowerCase()
}

function extFromFilename(filename: string): string {
  const name = (filename || '').trim()
  const dot = name.lastIndexOf('.')
  if (dot < 0) return ''
  return normalizeExt(name.slice(dot + 1))
}

const WORD_EXTS = [
  'doc', 'docx', 'docm', 'dot', 'dotx', 'dotm', 'rtf', 'odt', 'pages',
  'wpd', 'wps', 'ofd', 'hwp', 'hwpx',
]

const EXCEL_EXTS = [
  'xls', 'xlsx', 'xlsm', 'xlsb', 'xlt', 'xltx', 'xltm', 'ods', 'numbers',
  'csv', 'tsv', 'gsheet',
]

const PPT_EXTS = ['ppt', 'pptx', 'pptm', 'pps', 'ppsx', 'ppsm', 'pot', 'potx', 'potm', 'key', 'odp']

const MARKDOWN_EXTS = ['md', 'markdown', 'mdx']
const TXT_EXTS = ['txt', 'text', 'log', 'nfo', 'readme']

const CODE_EXTS = [
  'js', 'jsx', 'ts', 'tsx', 'mjs', 'cjs', 'vue', 'svelte', 'astro',
  'html', 'htm', 'xml', 'xhtml', 'css', 'scss', 'sass', 'less', 'styl',
  'json', 'jsonc', 'json5', 'yml', 'yaml', 'toml', 'ini', 'cfg', 'conf', 'config',
  'env', 'properties', 'lock', 'editorconfig',
  'py', 'ipynb', 'java', 'kt', 'kts', 'groovy', 'gradle', 'scala', 'clj', 'cljs',
  'go', 'rs', 'rb', 'php', 'pl', 'pm', 'lua', 'r', 'dart', 'swift', 'm', 'mm',
  'c', 'h', 'cc', 'cpp', 'cxx', 'hpp', 'hxx', 'hh', 'cs', 'fs', 'vb',
  'sh', 'bash', 'zsh', 'fish', 'ps1', 'bat', 'cmd',
  'sql', 'graphql', 'gql', 'proto', 'thrift', 'tf', 'hcl', 'sol', 'asm', 's',
  'ex', 'exs', 'erl', 'hrl', 'hs', 'lhs', 'ml', 'mli', 'jl', 'tex', 'gv', 'req',
  'http', 'rest', 'diff', 'patch', 'mk', 'cmake', 'bazel', 'nim', 'zig', 'v',
]

const IMAGE_EXTS = [
  'png', 'jpg', 'jpeg', 'jfif', 'gif', 'webp', 'bmp', 'ico', 'cur', 'tif', 'tiff',
  'avif', 'heic', 'heif', 'jxl', 'psd', 'psb', 'ai', 'sketch', 'fig', 'xd', 'afdesign',
  'raw', 'cr2', 'cr3', 'nef', 'arw', 'dng', 'orf', 'rw2',
]

const AUDIO_EXTS = [
  'mp3', 'wav', 'flac', 'm4a', 'aac', 'ogg', 'oga', 'opus', 'wma', 'aiff', 'aif',
  'alac', 'ape', 'amr', 'mid', 'midi', 'weba', 'caf', 'm4b',
]

const VIDEO_EXTS = [
  'mp4', 'mov', 'avi', 'mkv', 'webm', 'm4v', 'wmv', 'flv', 'f4v', 'mpeg', 'mpg',
  'mpe', '3gp', '3g2', 'ogv', 'rm', 'rmvb', 'asf', 'vob', 'm2ts', 'mts', 'm3u8',
]

const ARCHIVE_EXTS = [
  'zip', 'zipx', 'rar', '7z', 'tar', 'gz', 'tgz', 'bz2', 'tbz', 'tbz2', 'xz', 'txz',
  'zst', 'lz', 'lzma', 'cab', 'ar', 'cpio', 'xar', 'lha', 'lzh', 'z',
  'jar', 'war', 'ear', 'apk', 'aab', 'ipa', 'whl', 'crx', 'xpi', 'cbz', 'cbr',
]

const CODE_FILENAMES = ['dockerfile', 'makefile', 'cmakelists.txt', 'gemfile', 'procfile', 'jenkinsfile']

/** 按扩展名（或特殊文件名）解析文件类型图标 */
function resolveIconByExt(ext: string, filename?: string): string {
  const e = normalizeExt(ext)
  const name = (filename || '').trim().toLowerCase()

  if (CODE_FILENAMES.includes(name) || name.startsWith('dockerfile.')) return codeIcon
  if (name.startsWith('.') && CODE_FILENAMES.includes(name.slice(1))) return codeIcon

  if (WORD_EXTS.includes(e)) return wordIcon
  if (e === 'pdf') return pdfIcon
  if (EXCEL_EXTS.includes(e)) return excelIcon
  if (PPT_EXTS.includes(e)) return pptIcon
  if (MARKDOWN_EXTS.includes(e)) return markdownIcon
  if (TXT_EXTS.includes(e)) return txtIcon
  if (e === 'svg') return svgIcon
  if (IMAGE_EXTS.includes(e)) return imageIcon
  if (AUDIO_EXTS.includes(e)) return audioIcon
  if (VIDEO_EXTS.includes(e)) return videoIcon
  if (ARCHIVE_EXTS.includes(e)) return zipIcon
  if (CODE_EXTS.includes(e)) return codeIcon

  return defaultFileIcon
}

/** 按文件名/扩展名解析图标地址（卡片等非 React 场景也复用同一套映射） */
export function fileIconSrc(filename?: string, ext?: string): string {
  const resolvedExt = ext ? normalizeExt(ext) : extFromFilename(filename || '')
  return resolveIconByExt(resolvedExt, filename)
}

export default function FileIcon({
  filename,
  ext,
  size = 40,
  className,
}: {
  filename?: string
  ext?: string
  size?: number
  className?: string
}) {
  const resolvedExt = ext ? normalizeExt(ext) : extFromFilename(filename || '')
  const src = resolveIconByExt(resolvedExt, filename)
  const alt = resolvedExt ? resolvedExt.toUpperCase() : 'FILE'
  return (
    <img
      src={src}
      alt={alt}
      width={size}
      height={size}
      className={`app-icon file-type-icon ${className || ''}`.trim()}
      style={{ width: size, height: size }}
      draggable={false}
    />
  )
}
