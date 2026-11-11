import { Image, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useMemo } from 'react'

import CodeBlock from './CodeBlock'
import { normalizePreviewSpec, resolveAssetUrl } from '../../lib/richText'
import Icon from '../Icon'

type PreviewKind = 'image' | 'pdf' | 'html' | 'markdown'

interface PreviewBlockProps {
  kind: PreviewKind
  code: string
}

const KIND_LABEL: Record<PreviewKind, string> = {
  image: '图片',
  pdf: 'PDF',
  html: '网页',
  markdown: '文档',
}

/** image/pdf/html/markdown 富块：能直接取到的地址就地展示或打开，否则退回代码 */
export default function PreviewBlock({ kind, code }: PreviewBlockProps) {
  const spec = useMemo(() => normalizePreviewSpec(code), [code])
  const items = useMemo(() => {
    if (!spec) return []
    const list = spec.items?.length
      ? spec.items
      : spec.src
        ? [{ src: spec.src, title: spec.title, label: spec.label }]
        : []
    return list
      .map((item) => ({ ...item, url: resolveAssetUrl(item.src) }))
      .filter((item) => item.url)
  }, [spec])

  if (!items.length) return <CodeBlock code={code} lang={`${kind}-preview`} />

  if (kind === 'image') {
    const urls = items.map((item) => item.url)
    return (
      <View className='rich-frame'>
        <View className='rich-frame-body rich-gallery'>
          {items.map((item, index) => (
            <Image
              key={`${item.url}-${index}`}
              className='rich-gallery-image'
              src={item.url}
              mode='widthFix'
              onClick={() => Taro.previewImage({ urls, current: item.url }).catch(() => undefined)}
            />
          ))}
        </View>
      </View>
    )
  }

  return (
    <View className='rich-frame'>
      {items.map((item, index) => (
        <View
          className='rich-file-row'
          key={`${item.url}-${index}`}
          onClick={() => {
            if (process.env.TARO_ENV === 'h5') window.open(item.url, '_blank')
          }}
        >
          <View className='rich-file-icon'>
            <Icon name='file-text' size={18} color='#2C59BC' />
          </View>
          <View className='rich-file-main'>
            <Text className='rich-file-name'>{item.title || item.label || KIND_LABEL[kind]}</Text>
            <Text className='rich-file-sub'>{KIND_LABEL[kind]}</Text>
          </View>
          <Text className='rich-file-action'>打开</Text>
        </View>
      ))}
    </View>
  )
}
