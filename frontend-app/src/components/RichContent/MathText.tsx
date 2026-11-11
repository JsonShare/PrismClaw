import 'katex/dist/katex.min.css'

import { useEffect, useMemo, useState } from 'react'

import { RawHtml } from './RawHtml'

interface KaTeXModule {
  renderToString: (
    tex: string,
    options: {
      displayMode?: boolean
      throwOnError?: boolean
      strict?: boolean | string
      output?: string
    },
  ) => string
}

let katexModule: KaTeXModule | null = null
let katexPromise: Promise<void> | null = null
const listeners = new Set<() => void>()

function loadKatex(): Promise<void> {
  if (katexModule) return Promise.resolve()
  katexPromise ??= import('katex')
    .then((mod) => {
      katexModule = ((mod as unknown as { default?: KaTeXModule }).default ?? (mod as unknown as KaTeXModule))
      listeners.forEach((notify) => notify())
    })
    .catch(() => {
      katexPromise = null
    })
  return katexPromise
}

function useKatex(): KaTeXModule | null {
  const [module, setModule] = useState<KaTeXModule | null>(katexModule)
  useEffect(() => {
    if (katexModule) {
      setModule(katexModule)
      return
    }
    const notify = () => setModule(katexModule)
    listeners.add(notify)
    void loadKatex()
    return () => {
      listeners.delete(notify)
    }
  }, [])
  return module
}

function render(module: KaTeXModule | null, tex: string, displayMode: boolean): string | null {
  if (!module) return null
  try {
    return module.renderToString(tex, { displayMode, throwOnError: false, strict: false })
  } catch {
    return null
  }
}

export function InlineMath({ tex }: { tex: string }) {
  const module = useKatex()
  const html = useMemo(() => render(module, tex, false), [module, tex])
  if (!html) return <RawHtml tag='span' className='md-math-raw' html={`$${escapeText(tex)}$`} />
  return <RawHtml tag='span' className='md-math-inline' html={html} />
}

export function BlockMath({ tex }: { tex: string }) {
  const module = useKatex()
  const html = useMemo(() => render(module, tex, true), [module, tex])
  if (!html) return <RawHtml className='md-math-raw is-block' html={`$$${escapeText(tex)}$$`} />
  return <RawHtml className='md-math-block' html={html} />
}

function escapeText(value: string): string {
  return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}
