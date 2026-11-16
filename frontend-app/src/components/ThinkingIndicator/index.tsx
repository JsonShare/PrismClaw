import { Image, Text, View } from '@tarojs/components'

import { svgDataUri } from '../../utils/svg'

// 小程序不支持内联 SVG，渐变图标同样走 Data URI；渐变固定为品牌紫→蓝
const SPARK_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none">' +
  '<defs><linearGradient id="sg" x1="4" y1="2" x2="22" y2="22" gradientUnits="userSpaceOnUse">' +
  '<stop stop-color="#C9A5FF"/><stop offset="0.55" stop-color="#A8B6FF"/><stop offset="1" stop-color="#8FCCFF"/>' +
  '</linearGradient></defs>' +
  '<path d="M10 2.4c.9 5.9 3.7 8.7 9.6 9.6-5.9.9-8.7 3.7-9.6 9.6-.9-5.9-3.7-8.7-9.6-9.6 5.9-.9 8.7-3.7 9.6-9.6z" fill="url(#sg)"/>' +
  '<path d="M19.6 1.6c.4 2.6 1.6 3.8 4.2 4.2-2.6.4-3.8 1.6-4.2 4.2-.4-2.6-1.6-3.8-4.2-4.2 2.6-.4 3.8-1.6 4.2-4.2z" fill="url(#sg)"/>' +
  '<path d="M20.4 15.2c.25 1.7 1.05 2.5 2.75 2.75-1.7.25-2.5 1.05-2.75 2.75-.25-1.7-1.05-2.5-2.75-2.75 1.7-.25 2.5-1.05 2.75-2.75z" fill="url(#sg)"/>' +
  '</svg>'

export function ThinkingIndicator({ text }: { text: string }) {
  return (
    <View className='thinking'>
      <Image className='thinking-icon' src={svgDataUri(SPARK_SVG)} mode='aspectFit' />
      <Text className='thinking-text'>{text}</Text>
    </View>
  )
}
