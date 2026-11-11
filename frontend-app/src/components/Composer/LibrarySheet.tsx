import { Input, ScrollView, Text, View } from '@tarojs/components'
import { Fragment, useEffect, useRef, useState } from 'react'

import Icon from '../Icon'

export interface LibraryItem {
  id: string
  label: string
  desc?: string
  section?: string
  /** 选中后写入输入框 chip 的类型（专家=plugin，技能=skill） */
  chipKind?: 'plugin' | 'skill'
  emoji?: string | null
}

interface LibrarySheetProps {
  open: boolean
  title: string
  placeholder: string
  emptyText: string
  /** 查询变化时重新加载（空查询返回默认列表） */
  load: (query: string) => Promise<LibraryItem[]>
  onPick: (item: LibraryItem) => void
  onClose: () => void
}

/** 技能与专家列表：分区展示，点击条目写入输入框 */
export default function LibrarySheet({
  open,
  title,
  placeholder,
  emptyText,
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
          <Text className='library-title'>{title}</Text>
          <View className='library-close' onClick={onClose}>
            <Icon name='x' size={20} color='#8A9099' strokeWidth={2} />
          </View>
        </View>

        <View className='library-search'>
          <Icon name='search' size={15} color='#8A9099' />
          <Input
            className='library-input'
            value={keyword}
            placeholder={placeholder}
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
                <Text>{emptyText}</Text>
              </View>
            ) : null}

            {items.map((item, index) => {
              const newSection = item.section && item.section !== items[index - 1]?.section
              const sameSectionAsNext = items[index + 1]?.section === item.section
              return (
                <Fragment key={`${item.section ?? ''}:${item.id}`}>
                  {newSection ? <Text className='library-section'>{item.section}</Text> : null}
                  <View className='library-row' onClick={() => onPick(item)}>
                    <View className='library-icon'>
                      {item.emoji ? (
                        <Text className='library-emoji'>{item.emoji}</Text>
                      ) : (
                        <Icon
                          name={item.chipKind === 'plugin' ? 'puzzle' : 'hammer'}
                          size={22}
                          color='#575B62'
                          strokeWidth={1.7}
                        />
                      )}
                    </View>
                    <View className='library-main'>
                      <Text className='library-label'>{item.label}</Text>
                      {item.desc ? <Text className='library-desc'>{item.desc}</Text> : null}
                    </View>
                  </View>
                  {sameSectionAsNext ? <View className='library-line' /> : null}
                </Fragment>
              )
            })}
          </View>
        </ScrollView>
      </View>
    </View>
  )
}
