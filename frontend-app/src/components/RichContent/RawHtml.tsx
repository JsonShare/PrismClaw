import { createElement } from 'react'

interface RawHtmlProps {
  html: string
  className?: string
  /** 行内公式需要 span 才能留在文字流里 */
  tag?: 'div' | 'span'
}

/**
 * H5 专用：把可信来源的 HTML（KaTeX / Mermaid 由库生成）注入 DOM。
 * 用户或模型输出的原文一律走 iframe sandbox，不进这里。
 */
export function RawHtml({ html, className, tag = 'div' }: RawHtmlProps) {
  return createElement(tag, {
    className,
    dangerouslySetInnerHTML: { __html: html },
  })
}
