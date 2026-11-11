import classIcon from '../../images/file_icon/WEIZHI.png'
import docIcon from '../../images/file_icon/WORD.png'
import defaultFileIcon from '../../images/file_icon/WENJIAN.png'
import dockerfileIcon from '../../images/file_icon/WENDANG.png'
import emlIcon from '../../images/file_icon/EML.png'
import exeIcon from '../../images/file_icon/WEIZHI.png'
import gifIcon from '../../images/file_icon/GIF.png'
import gitignoreIcon from '../../images/file_icon/WENDANG.png'
import htmlIcon from '../../images/file_icon/HTML.png'
import isoIcon from '../../images/file_icon/WEIZHI.png'
import jarIcon from '../../images/file_icon/WEIZHI.png'
import jpgIcon from '../../images/file_icon/TUPIAN.png'
import jsonIcon from '../../images/file_icon/JSON.png'
import mdIcon from '../../images/file_icon/MARKDOWN.png'
import mp3Icon from '../../images/file_icon/YINLE.png'
import mp4Icon from '../../images/file_icon/SHIPIN.png'
import movIcon from '../../images/file_icon/MOV.png'
import pdfIcon from '../../images/file_icon/PDF.png'
import pngIcon from '../../images/file_icon/PNG.png'
import pptIcon from '../../images/file_icon/PPT.png'
import rarIcon from '../../images/file_icon/RAR.png'
import svgIcon from '../../images/file_icon/SVG.png'
import txtIcon from '../../images/file_icon/WENDANG.png'
import xlsIcon from '../../images/file_icon/EXCEL.png'
import xmlIcon from '../../images/file_icon/WENDANG.png'
import wendangIcon from '../../images/file_icon/WENDANG.png'
import zipIcon from '../../images/file_icon/ZIP.png'
import codeIcon from '../../images/file_icon/CODE.png'
import configIcon from '../../images/file_icon/CONFIG.png'

function normalizeExt(ext: string): string {
  return (ext || '').trim().replace(/^\./, '').toLowerCase()
}

function extFromFilename(filename: string): string {
  const name = (filename || '').trim()
  const dot = name.lastIndexOf('.')
  if (dot < 0) return ''
  return normalizeExt(name.slice(dot + 1))
}

function resolveIconByExt(ext: string, filename?: string): string {
  const e = normalizeExt(ext)
  const name = (filename || '').trim().toLowerCase()

  if (name === 'dockerfile' || name.startsWith('dockerfile.')) return dockerfileIcon
  if (name === '.gitignore') return gitignoreIcon
  if (e === 'eml') return emlIcon

  if (e === 'gitignore') return gitignoreIcon
  if (e === 'dockerfile') return dockerfileIcon

  // 文档类
  if (e === 'doc' || e === 'docx' || e === 'rtf') return docIcon
  if (e === 'xls' || e === 'xlsx' || e === 'csv' || e === 'tsv' || e === 'gsheet') return xlsIcon
  if (e === 'ppt' || e === 'pptx' || e === 'key') return pptIcon
  if (e === 'pdf') return pdfIcon

  if (e === 'html' || e === 'htm') return htmlIcon
  if (e === 'xml') return xmlIcon
  if (e === 'zip') return zipIcon
  if (e === 'rar') return rarIcon
  if (e === '7z' || e === 'tar' || e === 'gz' || e === 'tgz') return zipIcon

  // 图片类
  if (e === 'png') return pngIcon
  if (e === 'jpg' || e === 'jpeg') return jpgIcon
  if (e === 'gif') return gifIcon
  if (e === 'svg') return svgIcon
  if (e === 'webp' || e === 'bmp' || e === 'ico' || e === 'tiff' || e === 'tif') return jpgIcon

  // 代码类 → CODE.png
  if (['js','jsx','ts','tsx','mjs','cjs','py','java','cs','css','sql','c','h','cc','cpp','cxx','hpp','hxx','hh','go','rs','php','rb','pl','sh','bash','zsh','ps1','bat','cmd','coffee','asm','s','scala','swift','kt','kts','dart','lua','r','groovy','gradle','mk','diff','patch','bundle','bdl','ipynb','proto','hcl','tf','tex','gv','http','rest','graphql','feature'].includes(e)) return codeIcon

  // 配置类 → CONFIG.png
  if (['ini','toml','cfg','conf','env','properties','lock','editorconfig','jsonc','json5','yml','yaml'].includes(e)) return configIcon

  // 编译产物 / 字节码（非源码，复用占位图标）
  if (e === 'jar') return jarIcon
  if (e === 'class') return classIcon

  if (e === 'json') return jsonIcon
  if (e === 'md' || e === 'markdown') return mdIcon
  if (e === 'txt' || e === 'log') return txtIcon

  // 音视频
  if (e === 'mp3' || e === 'wav' || e === 'flac' || e === 'm4a' || e === 'aac' || e === 'ogg') return mp3Icon
  if (e === 'mp4' || e === 'avi' || e === 'mkv' || e === 'webm') return mp4Icon
  if (e === 'mov') return movIcon

  // 可执行/镜像
  if (e === 'exe' || e === 'msi' || e === 'dmg' || e === 'pkg') return exeIcon
  if (e === 'iso') return isoIcon

  // ── 预览器支持但未显式覆盖的扩展名：复用既有图标，避免掉到默认空白图标 ──
  // 办公文档家族其余变体与开放格式（Word / Excel / PPT 三系）
  if (['docm','dotx','dotm','dot','odt','wpd','wp','wp5','wp6','ofd','hwp','hwpx','typ','typst','chm','epub','fb2','umd','pages','msg','mbox'].includes(e)) return docIcon
  if (['xltx','xlsm','xlsb','xls','xlt','xla','xlam','xltm','ods','fods','numbers'].includes(e)) return xlsIcon
  if (['pptm','potx','potm','ppsx','ppsm','pot','odp'].includes(e)) return pptIcon
  // 图片格式扩展
  if (['avif','heic','heif','jxl'].includes(e)) return jpgIcon
  // 压缩包扩展
  if (['zipx','bz2','bzip2','tbz','tbz2','xz','txz','lzma','zst','tzst','cab','ar','cpio','xar','lha','lzh','war','ear','apk','cbz','cbr'].includes(e)) return zipIcon
  // 音视频扩展
  if (['mpeg','wmv','m4v','m3u8'].includes(e)) return mp4Icon
  if (['oga','opus','midi','mid','weba'].includes(e)) return mp3Icon
  // 图表 / 绘图 / 思维导图 / 3D / CAD / GIS / EDA / 设计 / 医疗 / 加密：
  // 暂复用文档类图标占位，这些类型形态差异大，待补专用图标（需美术资源 / 图像工具）
  if (['excalidraw','drawio','dio','mermaid','mmd','plantuml','puml','bpmn','xmind','dxf','dwg','dwf','dwfx','xps','glb','gltf','obj','stl','ply','fbx','dae','3ds','3mf','amf','usd','usda','usdc','usdz','kmz','step','stp','iges','igs','ifc','3dm','brep','geojson','kml','gpx','shp','gds','oas','oasis','dcm','dicom','psb','ait','idml','icml','fla','xfl','p7m','p7s','p7c','p7b','pgp','gpg','sig','asc'].includes(e)) return wendangIcon

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
