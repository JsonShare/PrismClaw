import { Input, ScrollView, Text, View } from '@tarojs/components'
import { Fragment, useEffect, useRef, useState } from 'react'

import Icon from '../Icon'

export interface LibraryItem {
  id: string
  label: string
  desc?: string
  emoji?: string | null
}

interface LibrarySheetProps {
  open: boolean
  selectedIds: string[]
  /** 查询变化时重新加载（空查询返回默认列表） */
  load: (query: string) => Promise<LibraryItem[]>
  onPick: (item: LibraryItem) => void
  onClose: () => void
}

/** 技能列表：搜索并连续选择，选中结果写入输入框 chips */
export default function LibrarySheet({
  open,
  selectedIds,
  load,
  onPick,
  onClose,
}: LibrarySheetProps) {
  const [keyword, setKeyword] = useState('')
  const [items, setItems] = useState<LibraryItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const seqRef = useRef(0)

  useEffect(() => {
    if (!open) {
      setKeyword('')
      setError('')
      return
    }
    const seq = (seqRef.current += 1)
    const timer = setTimeout(
      () => {
        setLoading(true)
        load(keyword.trim())
          .then((list) => {
            if (seq !== seqRef.current) return
            setItems(list)
            setError('')
          })
          .catch((e: Error) => {
            if (seq !== seqRef.current) return
            setItems([])
            setError(e.message || '加载失败')
          })
          .finally(() => {
            if (seq === seqRef.current) setLoading(false)
          })
      },
      keyword ? 250 : 0,
    )
    return () => clearTimeout(timer)
  }, [open, keyword, load])

  if (!open) return null

  return (
    <View className='sheet-layer' onClick={onClose}>
      <View className='library-sheet' onClick={(event) => event.stopPropagation()}>
        <View className='library-head'>
          <Text className='library-title'>选择技能</Text>
          <View className='library-close' onClick={onClose}>
            <Icon name='x' size={20} color='#8A9099' strokeWidth={2} />
          </View>
        </View>

        <View className='library-search'>
          <Icon name='search' size={16} color='#8A9099' />
          <Input
            className='library-input'
            value={keyword}
            placeholder='搜索技能'
            onInput={(event) => setKeyword(event.detail.value)}
          />
        </View>

        <ScrollView scrollY className='library-list'>
          <View className='library-body'>
            {loading && !items.length ? (
              <View className='library-hint'>
                <Text>正在加载…</Text>
              </View>
            ) : null}
            {error ? (
              <View className='library-hint'>
                <Text>{error}</Text>
              </View>
            ) : null}
            {!loading && !error && !items.length ? (
              <View className='library-hint'>
                <Text>{keyword.trim() ? '没有匹配的技能' : '没有可用的技能'}</Text>
              </View>
            ) : null}

            {items.map((item, index) => {
              const chipId = `skill:${item.id}`
              const selected = selectedIds.includes(chipId)
              return (
                <Fragment key={item.id}>
                  <View
                    className={`library-row${selected ? ' is-selected' : ''}`}
                    onClick={() => onPick(item)}
                  >
                    <View className='library-icon'>
                      {item.emoji ? (
                        <Text className='library-emoji'>{item.emoji}</Text>
                      ) : (
                        <Icon name='hammer' size={22} color='#575B62' strokeWidth={1.7} />
                      )}
                    </View>
                    <View className='library-main'>
                      <Text className='library-label'>{item.label}</Text>
                      {item.desc ? <Text className='library-desc'>{item.desc}</Text> : null}
                    </View>
                    {selected ? (
                      <View className='library-check'>
                        <Icon name='check' size={20} color='#111316' strokeWidth={2} />
                      </View>
                    ) : null}
                  </View>
                  {index < items.length - 1 ? <View className='library-line' /> : null}
                </Fragment>
              )
            })}
          </View>
        </ScrollView>

        <View className='library-footer'>
          <View className='library-collapse' onClick={onClose}>
            <Text>收起</Text>
          </View>
        </View>
      </View>
    </View>
  )
}
