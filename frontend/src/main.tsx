import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './index.css'
import './theme/base.css'
import './theme/dark.css'
import './theme/light.css'

// @file-viewer 可选能力全局启用（副作用注册，一次即可）：
// - pptx-charts：PPTX 内嵌图表
// - mermaid：renderer-text 中 md 代码块的 Mermaid 渲染
// - text-tools：patch/diff 对比与 Git bundle 检查
// - rtf：RTF 富文本（挂在 word 渲染链）
// - midi / streaming-media：媒体渲染器的 MIDI 检查与 HLS 回退
// - pdf-identity-repair：Identity 编码 CJK 字体的 PDF 修复
// - drawio-official：drawio/Excalidraw 官方 viewer（挂在 drawing 渲染链）
import { enableFileViewerPptxCharts } from '@file-viewer/capability-pptx-charts'
import { enableFileViewerMermaid } from '@file-viewer/capability-mermaid'
import { enableFileViewerTextTools } from '@file-viewer/capability-text-tools'
import { enableFileViewerRtf } from '@file-viewer/capability-rtf'
import { enableFileViewerMidi } from '@file-viewer/capability-midi'
import { enableFileViewerStreamingMedia } from '@file-viewer/capability-streaming-media'
import { enableFileViewerPdfIdentityFontRepair } from '@file-viewer/capability-pdf-identity-repair'
import { enableFileViewerDrawioOfficialViewer } from '@file-viewer/capability-drawio-official'

enableFileViewerDrawioOfficialViewer()
enableFileViewerPptxCharts()
enableFileViewerMermaid()
enableFileViewerTextTools()
enableFileViewerRtf()
enableFileViewerMidi()
enableFileViewerStreamingMedia()
enableFileViewerPdfIdentityFontRepair()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
