import { Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useEffect, useMemo, useState } from 'react'

import Icon from '../Icon'
import { highlightLang, langLabel } from '../../lib/richText'
import { RawHtml } from './RawHtml'

interface CodeBlockProps {
  code: string
  lang: string
}

type HighlightModule = {
  highlight: (code: string, options: { language: string; ignoreIllegals?: boolean }) => { value: string }
}

let highlighterPromise: Promise<HighlightModule | null> | null = null

function loadHighlighter(): Promise<HighlightModule | null> {
  highlighterPromise ??= import('highlight.js/lib/common')
    .then((mod) => ((mod as unknown as { default?: HighlightModule }).default ?? (mod as unknown as HighlightModule)))
    .catch(() => null)
  return highlighterPromise
}

export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function diffHtml(code: string): string {
  return code
    .split('\n')
    .map((line) => {
      const cls = line.startsWith('+')
        ? 'hljs-addition'
        : line.startsWith('-')
          ? 'hljs-deletion'
          : line.startsWith('@@')
            ? 'hljs-section'
            : ''
      return cls ? `<span class="${cls}">${escapeHtml(line) || ' '}</span>` : escapeHtml(line)
    })
    .join('\n')
}

/** 代码块：语言标签 + 复制按钮 + 懒加载高亮（失败自动退回纯文本） */
export default function CodeBlock({ code, lang }: CodeBlockProps) {
  const language = highlightLang(lang)
  const [html, setHtml] = useState(() => (language === 'diff' ? diffHtml(code) : escapeHtml(code)))
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    let alive = true
    if (language === 'diff') {
      setHtml(diffHtml(code))
      return () => {
        alive = false
      }
    }
    if (!language) {
      setHtml(escapeHtml(code))
      return () => {
        alive = false
      }
    }
    setHtml(escapeHtml(code))
    void loadHighlighter().then((hljs) => {
      if (!alive || !hljs) return
      try {
        setHtml(hljs.highlight(code, { language, ignoreIllegals: true }).value)
      } catch {
        setHtml(escapeHtml(code))
      }
    })
    return () => {
      alive = false
    }
  }, [code, language])

  useEffect(() => {
    if (!copied) return
    const timer = setTimeout(() => setCopied(false), 1600)
    return () => clearTimeout(timer)
  }, [copied])

  const copy = () => {
    void Taro.setClipboardData({ data: code })
    setCopied(true)
  }

  const label = useMemo(() => langLabel(lang), [lang])

  return (
    <View className='rich-code'>
      <View className='rich-code-head'>
        <Text className='rich-code-lang'>{label}</Text>
        <View className='rich-code-copy' onClick={copy}>
          <Icon name={copied ? 'check' : 'copy'} size={13} color='#6B7280' />
          <Text className='rich-code-copy-text'>{copied ? '已复制' : '复制'}</Text>
        </View>
      </View>
      <View className='rich-code-body'>
        <RawHtml className='rich-code-text' html={html} />
      </View>
    </View>
  )
}
