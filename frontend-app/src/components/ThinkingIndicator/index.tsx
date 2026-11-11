import { Image, Text, View } from '@tarojs/components'

import { svgDataUri } from '../../utils/svg'

// 小程序不支持内联 SVG，渐变图标同样走 Data URI；渐变固定为品牌紫→蓝
const SPARK_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke-linecap="round" stroke-linejoin="round">' +
  '<defs><linearGradient id="sg" x1="3" y1="3" x2="21" y2="21" gradientUnits="userSpaceOnUse">' +
  '<stop stop-color="#8B5CF6"/><stop offset="0.55" stop-color="#6D7BFC"/><stop offset="1" stop-color="#2C59BC"/>' +
  '</linearGradient></defs>' +
  '<path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3z" stroke="url(#sg)" stroke-width="1.8"/>' +
  '</svg>'

export function ThinkingIndicator({ text }: { text: string }) {
  return (
    <View className='thinking'>
      <Image className='thinking-icon' src={svgDataUri(SPARK_SVG)} mode='aspectFit' />
      <Text className='thinking-text'>{text}</Text>
    </View>
  )
}
