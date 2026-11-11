import { Image, Text, View } from '@tarojs/components'
import type { ReactNode } from 'react'

import { svgDataUri } from '../../utils/svg'

const ROBOT = svgDataUri(
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 220 190" fill="none">' +
    '<path d="M60 93c0-27 23-47 52-47s52 20 52 47" stroke="#C6CAD1" stroke-width="8" stroke-linecap="round" opacity=".7"/>' +
    '<circle cx="58" cy="97" r="14" stroke="#C6CAD1" stroke-width="7"/>' +
    '<circle cx="162" cy="97" r="14" stroke="#C6CAD1" stroke-width="7"/>' +
    '<rect x="66" y="57" width="88" height="78" rx="36" stroke="#C6CAD1" stroke-width="7"/>' +
    '<rect x="82" y="74" width="56" height="42" rx="19" stroke="#C6CAD1" stroke-width="5" opacity=".75"/>' +
    '<rect x="95" y="86" width="8" height="18" rx="4" fill="#C6CAD1" opacity=".6"/>' +
    '<rect x="117" y="86" width="8" height="18" rx="4" fill="#C6CAD1" opacity=".6"/>' +
    '<path d="M150 28c0-10 8-18 18-18" stroke="#C6CAD1" stroke-width="5" stroke-linecap="round"/>' +
    '<path d="M96 148h28" stroke="#C6CAD1" stroke-width="6" stroke-linecap="round" opacity=".55"/>' +
    '</svg>'
)

export interface EmptyStateProps {
  title: string
  desc?: string
  children?: ReactNode
}

export default function EmptyState({ title, desc, children }: EmptyStateProps) {
  return (
    <View className='empty'>
      <Image className='empty-art' src={ROBOT} mode='aspectFit' />
      <Text className='empty-title'>{title}</Text>
      {desc ? <Text className='empty-desc'>{desc}</Text> : null}
      {children}
    </View>
  )
}
