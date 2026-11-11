import { Text, View } from '@tarojs/components'
import { createElement, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import Icon from '../Icon'

interface HtmlRenderProps {
  code: string
}

const MAX_HEIGHT = 1200
const MIN_HEIGHT = 80

function isDark(): boolean {
  if (typeof document === 'undefined') return false
  return document.documentElement.classList.contains('theme-dark')
}

/** 把模型输出的 HTML 片段包成完整文档：注入设计令牌 + 高度上报脚本 */
function wrapDocument(code: string): string {
  const dark = isDark()
  const palette = dark
    ? `--card-bg:#0b0d10;--card-fg:#e9edf4;--card-sub:#98a2b5;--card-line:rgba(255,255,255,.14);--card-soft:rgba(255,255,255,.06);`
    : `--card-bg:#ffffff;--card-fg:#17191c;--card-sub:#5b6472;--card-line:rgba(23,25,28,.12);--card-soft:rgba(23,25,28,.04);`
  const head = `<style>
:root{${palette}--card-primary:#2c59bc;color-scheme:${dark ? 'dark' : 'light'};}
html,body{margin:0;padding:0;background:transparent;}
body{padding:2px;font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Helvetica Neue',Arial,sans-serif;color:var(--card-fg);font-size:14px;line-height:1.6;-webkit-text-size-adjust:100%;}
img,svg,canvas,video{max-width:100%;}
table{border-collapse:collapse;width:100%;}
a{color:var(--card-primary);}
</style>`
  const reporter = `<script>
(function(){
  var t=0;
  function r(){clearTimeout(t);t=setTimeout(function(){
    try{
      var h=Math.ceil(document.body.getBoundingClientRect().height)+4;
      parent.postMessage({type:'html-render-height',height:h},'*');
    }catch(e){}
  },80)}
  r();
  if(typeof MutationObserver!=='undefined'){new MutationObserver(r).observe(document.body,{childList:true,subtree:true,attributes:true});}
  window.addEventListener('resize',r);
  window.addEventListener('load',r);
})();
</script>`
  const body = /<html[\s>]/i.test(code) ? code : `<!DOCTYPE html><html><head>${head}</head><body>${code}${reporter}</body></html>`
  return body
}

/** 富内容卡片：沙箱 iframe 内渲染模型产出的 HTML，高度跟随内容 */
export default function HtmlRender({ code }: HtmlRenderProps) {
  const frameRef = useRef<HTMLIFrameElement | null>(null)
  const [height, setHeight] = useState(140)
  const [fullscreen, setFullscreen] = useState(false)
  const srcdoc = useMemo(() => wrapDocument(code), [code])

  useEffect(() => {
    const handler = (event: MessageEvent) => {
      const data = event.data as { type?: string; height?: number } | null
      if (!data || data.type !== 'html-render-height') return
      if (frameRef.current && event.source !== frameRef.current.contentWindow) return
      const next = Number(data.height) || 0
      if (next <= 0) return
      setHeight(Math.max(MIN_HEIGHT, Math.min(next, MAX_HEIGHT)))
    }
    window.addEventListener('message', handler)
    return () => window.removeEventListener('message', handler)
  }, [])

  const frame = createElement('iframe', {
    ref: frameRef,
    srcDoc: srcdoc,
    sandbox: 'allow-scripts allow-popups',
    title: '富内容卡片',
    className: 'rich-html-frame',
    style: { width: '100%', height: `${height}px`, border: 0, display: 'block' },
  })

  return (
    <>
      <View className='rich-frame'>
        <View className='rich-frame-head'>
          <Text className='rich-frame-title'>卡片</Text>
          <View className='rich-frame-action' onClick={() => setFullscreen(true)}>
            <Icon name='expand' size={14} color='#6B7280' />
          </View>
        </View>
        <View className='rich-frame-body'>{frame}</View>
      </View>
      {fullscreen && typeof document !== 'undefined'
        ? createPortal(
            <View className='rich-fullscreen'>
              <View className='rich-fullscreen-head'>
                <Text className='rich-fullscreen-title'>卡片</Text>
                <View className='rich-frame-action' onClick={() => setFullscreen(false)}>
                  <Icon name='x' size={18} color='#6B7280' />
                </View>
              </View>
              <View className='rich-fullscreen-body'>
                {createElement('iframe', {
                  srcDoc: srcdoc,
                  sandbox: 'allow-scripts allow-popups',
                  title: '富内容卡片',
                  className: 'rich-html-frame',
                })}
              </View>
            </View>,
            document.body,
          )
        : null}
    </>
  )
}
