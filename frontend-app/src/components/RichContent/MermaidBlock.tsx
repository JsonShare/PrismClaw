import { Text, View } from '@tarojs/components'
import { useEffect, useState } from 'react'

import CodeBlock from './CodeBlock'
import { RawHtml } from './RawHtml'

interface MermaidBlockProps {
  code: string
}

interface MermaidModule {
  initialize: (config: Record<string, unknown>) => void
  render: (id: string, code: string) => Promise<{ svg: string }>
}

let mermaidPromise: Promise<MermaidModule | null> | null = null
let renderSeq = 0

function loadMermaid(): Promise<MermaidModule | null> {
  mermaidPromise ??= import('mermaid')
    .then((mod) => ((mod as unknown as { default?: MermaidModule }).default ?? (mod as unknown as MermaidModule)))
    .catch(() => null)
  return mermaidPromise
}

function isDark(): boolean {
  if (typeof document === 'undefined') return false
  return document.documentElement.classList.contains('theme-dark')
}

/** Mermaid 图表：懒加载渲染为 SVG，失败退回代码块 */
export default function MermaidBlock({ code }: MermaidBlockProps) {
  const [svg, setSvg] = useState('')
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let alive = true
    setFailed(false)
    setSvg('')
    void loadMermaid()
      .then(async (mermaid) => {
        if (!mermaid) throw new Error('mermaid unavailable')
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: 'strict',
          theme: isDark() ? 'dark' : 'default',
          fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif',
        })
        renderSeq += 1
        const { svg: output } = await mermaid.render(`prism-mermaid-${renderSeq}`, code)
        if (alive) setSvg(output)
      })
      .catch(() => {
        if (alive) setFailed(true)
      })
    return () => {
      alive = false
    }
  }, [code])

  if (failed) return <CodeBlock code={code} lang='mermaid' />

  if (!svg) {
    return (
      <View className='rich-frame rich-pending'>
        <Text className='rich-pending-text'>正在绘制图表…</Text>
      </View>
    )
  }

  return (
    <View className='rich-frame'>
      <View className='rich-frame-body rich-mermaid-body'>
        <RawHtml className='rich-mermaid' html={svg} />
      </View>
    </View>
  )
}
