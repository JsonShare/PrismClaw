import { Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import type { ReactNode } from 'react'

import CodeBlock from '../RichContent/CodeBlock'
import DataTable from '../RichContent/DataTable'
import HtmlRender from '../RichContent/HtmlRender'
import { BlockMath, InlineMath } from '../RichContent/MathText'
import MermaidBlock from '../RichContent/MermaidBlock'
import PreviewBlock from '../RichContent/PreviewBlock'
import { RICH_LANGUAGES } from '../../lib/richText'

interface MarkdownTextProps {
  text: string
}

type Block =
  | { type: 'paragraph'; lines: string[] }
  | { type: 'heading'; level: number; text: string }
  | { type: 'code'; lang: string; content: string; closed: boolean }
  | { type: 'math'; content: string }
  | { type: 'list'; ordered: boolean; items: string[] }
  | { type: 'quote'; lines: string[] }
  | { type: 'table'; header: string[]; rows: string[][] }
  | { type: 'hr' }

const isTableRow = (line: string) => line.includes('|') && line.trim().length > 0
const isTableDivider = (line: string) => /^\s*\|?[\s:|-]+\|?\s*$/.test(line) && line.includes('-')
const splitRow = (line: string) =>
  line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim())

function parseBlocks(source: string): Block[] {
  const lines = source.replace(/\r\n/g, '\n').split('\n')
  const blocks: Block[] = []
  let paragraph: string[] = []
  let code: string[] | null = null
  let codeLang = ''

  const flushParagraph = () => {
    if (paragraph.length) {
      blocks.push({ type: 'paragraph', lines: paragraph })
      paragraph = []
    }
  }

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i]
    if (code) {
      if (line.trim().startsWith('```')) {
        blocks.push({ type: 'code', lang: codeLang, content: code.join('\n'), closed: true })
        code = null
        codeLang = ''
      } else {
        code.push(line)
      }
      continue
    }
    const trimmed = line.trim()
    if (trimmed.startsWith('```')) {
      flushParagraph()
      code = []
      codeLang = trimmed.slice(3).trim()
      continue
    }
    // 块级公式：$$ ... $$（允许单行）
    if (trimmed.startsWith('$$')) {
      flushParagraph()
      const rest = trimmed.slice(2)
      if (rest.endsWith('$$') && rest.length >= 2) {
        blocks.push({ type: 'math', content: rest.slice(0, -2) })
        continue
      }
      const content = [rest]
      let cursor = i + 1
      while (cursor < lines.length) {
        const current = lines[cursor].trim()
        if (current.endsWith('$$')) {
          content.push(current.slice(0, -2))
          break
        }
        content.push(lines[cursor])
        cursor += 1
      }
      blocks.push({ type: 'math', content: content.join('\n').trim() })
      i = cursor
      continue
    }
    // 表格：表头 + 分隔行 + 若干数据行
    if (isTableRow(line) && i + 1 < lines.length && isTableDivider(lines[i + 1])) {
      flushParagraph()
      const header = splitRow(line)
      const rows: string[][] = []
      let cursor = i + 2
      while (cursor < lines.length && isTableRow(lines[cursor])) {
        rows.push(splitRow(lines[cursor]))
        cursor += 1
      }
      blocks.push({ type: 'table', header, rows })
      i = cursor - 1
      continue
    }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      flushParagraph()
      blocks.push({ type: 'hr' })
      continue
    }
    const heading = /^(#{1,4})\s+(.*)$/.exec(line)
    if (heading) {
      flushParagraph()
      blocks.push({ type: 'heading', level: heading[1].length, text: heading[2] })
      continue
    }
    if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
      flushParagraph()
      const ordered = /^\s*\d+\./.test(line)
      const last = blocks[blocks.length - 1]
      const item = line.replace(/^\s*([-*+]|\d+\.)\s+/, '')
      if (last && last.type === 'list' && last.ordered === ordered) last.items.push(item)
      else blocks.push({ type: 'list', ordered, items: [item] })
      continue
    }
    if (/^\s*>\s?/.test(line)) {
      flushParagraph()
      const last = blocks[blocks.length - 1]
      if (last && last.type === 'quote') last.lines.push(line.replace(/^\s*>\s?/, ''))
      else blocks.push({ type: 'quote', lines: [line.replace(/^\s*>\s?/, '')] })
      continue
    }
    if (!trimmed) {
      flushParagraph()
      continue
    }
    paragraph.push(line)
  }
  flushParagraph()
  if (code) blocks.push({ type: 'code', lang: codeLang, content: code.join('\n'), closed: false })
  return blocks
}

const INLINE_RE = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|\$[^\s$](?:[^$\n]*[^\s$])?\$)/g
const MATH_RE = /^\$([^\s$](?:[^$\n]*[^\s$])?)\$$/

function mathSource(part: string): string | null {
  const match = MATH_RE.exec(part)
  if (!match) return null
  const inner = match[1]
  // 含空格又没有任何 LaTeX 特征时按普通文本处理，避免把 "$100 和 $200" 当成公式
  if (/\s/.test(inner) && !/[\\^_{}]/.test(inner)) return null
  return inner
}

function openLink(href: string) {
  if (process.env.TARO_ENV === 'h5') {
    window.open(href, '_blank')
    return
  }
  void Taro.setClipboardData({ data: href })
  void Taro.showToast({ title: '链接已复制', icon: 'none' })
}

function renderInline(line: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = []
  const parts = line.split(INLINE_RE)
  parts.forEach((part, index) => {
    if (!part) return
    const key = `${keyPrefix}-${index}`
    if (/^\*\*[^*]+\*\*$/.test(part)) {
      nodes.push(
        <Text key={key} className='md-strong'>
          {part.slice(2, -2)}
        </Text>,
      )
      return
    }
    if (/^`[^`]+`$/.test(part)) {
      nodes.push(
        <Text key={key} className='md-code'>
          {part.slice(1, -1)}
        </Text>,
      )
      return
    }
    if (part.startsWith('$')) {
      const tex = mathSource(part)
      if (tex) {
        nodes.push(<InlineMath key={key} tex={tex} />)
        return
      }
    }
    const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(part)
    if (link) {
      nodes.push(
        <Text key={key} className='md-link' onClick={() => openLink(link[2])}>
          {link[1]}
        </Text>,
      )
      return
    }
    nodes.push(<Text key={key}>{part}</Text>)
  })
  return nodes
}

function RichBlock({ code, lang }: { code: string; lang: string }) {
  const language = lang.trim().toLowerCase()
  if (!RICH_LANGUAGES.has(language)) return <CodeBlock code={code} lang={lang} />
  if (language === 'html-render') return <HtmlRender code={code} />
  if (language === 'mermaid') return <MermaidBlock code={code} />
  if (language === 'latex' || language === 'math') return <BlockMath tex={code} />
  if (language === 'datatable' || language === 'spreadsheet') return <DataTable code={code} />
  return <PreviewBlock kind={language.replace('-preview', '') as 'image' | 'pdf' | 'html' | 'markdown'} code={code} />
}

export function MarkdownText({ text }: MarkdownTextProps) {
  const blocks = parseBlocks(text)
  return (
    <View className='md'>
      {blocks.map((block, index) => {
        const key = `block-${index}`
        if (block.type === 'heading') {
          return (
            <Text key={key} className={`md-heading md-h${block.level}`}>
              {renderInline(block.text, key)}
            </Text>
          )
        }
        if (block.type === 'code') {
          const rich = RICH_LANGUAGES.has(block.lang.trim().toLowerCase())
          if (rich && !block.closed) {
            return (
              <View key={key} className='rich-frame rich-pending'>
                <Text className='rich-pending-text'>正在生成内容，完成后自动呈现…</Text>
              </View>
            )
          }
          return (
            <View key={key} className='md-rich'>
              <RichBlock code={block.content} lang={block.lang} />
            </View>
          )
        }
        if (block.type === 'math') {
          return (
            <View key={key} className='md-rich'>
              <BlockMath tex={block.content} />
            </View>
          )
        }
        if (block.type === 'list') {
          return (
            <View key={key} className='md-list'>
              {block.items.map((item, itemIndex) => (
                <View key={`${key}-${itemIndex}`} className='md-list-item'>
                  <Text className='md-list-mark'>{block.ordered ? `${itemIndex + 1}.` : '•'}</Text>
                  <Text className='md-list-text'>{renderInline(item, `${key}-${itemIndex}`)}</Text>
                </View>
              ))}
            </View>
          )
        }
        if (block.type === 'quote') {
          return (
            <View key={key} className='md-quote'>
              <Text>{renderInline(block.lines.join('\n'), key)}</Text>
            </View>
          )
        }
        if (block.type === 'hr') {
          return <View key={key} className='md-hr' />
        }
        if (block.type === 'table') {
          return (
            <View key={key} className='md-table-wrap'>
              <View className='md-table'>
                <View className='md-tr is-head'>
                  {block.header.map((cell, cellIndex) => (
                    <View className='md-td' key={`${key}-h-${cellIndex}`}>
                      <Text>{renderInline(cell, `${key}-h-${cellIndex}`)}</Text>
                    </View>
                  ))}
                </View>
                {block.rows.map((row, rowIndex) => (
                  <View className='md-tr' key={`${key}-r-${rowIndex}`}>
                    {block.header.map((_, cellIndex) => (
                      <View className='md-td' key={`${key}-r-${rowIndex}-${cellIndex}`}>
                        <Text>
                          {renderInline(row[cellIndex] ?? '', `${key}-r-${rowIndex}-${cellIndex}`)}
                        </Text>
                      </View>
                    ))}
                  </View>
                ))}
              </View>
            </View>
          )
        }
        return (
          <Text key={key} className='md-paragraph'>
            {renderInline(block.lines.join('\n'), key)}
          </Text>
        )
      })}
    </View>
  )
}
