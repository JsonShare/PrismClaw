import { Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useState } from 'react'

import { toast } from '../../utils/toast'
import ExecSheet from '../ExecSheet'
import Icon from '../Icon'

interface ReferenceRowProps {
  sources?: string[]
  /** 运行中显示「检索了」，定稿后显示「参考」 */
  live?: boolean
}

/** 会话里的资料行：一行入口 + 「参考来源」底部弹层 */
export default function ReferenceRow({ sources, live = false }: ReferenceRowProps) {
  const [open, setOpen] = useState(false)
  const list = (sources ?? []).filter((source) => source.trim().length > 0)
  if (!list.length) return null

  const openSource = (url: string) => {
    setOpen(false)
    // 来源条目点击即打开原文；小程序端没有新窗口，退化为复制链接
    if (process.env.TARO_ENV === 'h5') {
      window.open(url, '_blank')
      return
    }
    void Taro.setClipboardData({ data: url })
    toast('链接已复制')
  }

  return (
    <>
      <View
        className={`ref-row${live ? ' is-live' : ''}`}
        onClick={live ? undefined : () => setOpen(true)}
      >
        <Text className='ref-row-text'>
          {live ? '检索了' : '参考'} {list.length} 篇资料
        </Text>
        {live ? null : <Icon name='chevron-right' size={13} color='#A6ADBB' strokeWidth={2} />}
      </View>
      {open ? (
        <ExecSheet title='参考来源' onClose={() => setOpen(false)}>
          {list.map((url) => (
            <View className='exec-item is-link' key={url} onClick={() => openSource(url)}>
              <Icon name='search' size={14} color='#4B5563' strokeWidth={1.8} />
              <Text className='exec-item-text'>{url}</Text>
              <Icon name='chevron-right' size={13} color='#C3C9D5' strokeWidth={2} />
            </View>
          ))}
        </ExecSheet>
      ) : null}
    </>
  )
}
