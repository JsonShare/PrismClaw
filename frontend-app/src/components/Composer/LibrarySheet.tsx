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
  selectedIds: string[]
  /** 查询变化时重新加载（空查询返回默认列表） */
  load: (query: string) => Promise<LibraryItem[]>
  onPick: (item: LibraryItem) => void
  onClose: () => void
}

/** 专家 / 技能两个页签，各自对应后端返回的 section */
const TABS = ['专家', '技能'] as const
type Tab = (typeof TABS)[number]

/** 技能与专家列表：搜索、页签切换并连续选择，选中结果写入输入框 chips */
export default function LibrarySheet({
  open,
  selectedIds,
  load,
  onPick,
  onClose,
}: LibrarySheetProps) {
  const [keyword, setKeyword] = useState('')
  const [tab, setTab] = useState<Tab>(TABS[0])
  const [items, setItems] = useState<LibraryItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const seqRef = useRef(0)
  const visibleItems = items.filter((item) => item.section === tab)

  useEffect(() => {
    if (!open) {
      setKeyword('')
      setTab(TABS[0])
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
          <Text className='library-title'>选择专家或技能</Text>
          <View className='library-close' onClick={onClose}>
            <Icon name='x' size={20} color='#8A9099' strokeWidth={2} />
          </View>
        </View>

        <View className='library-search'>
          <Icon name='search' size={16} color='#8A9099' />
          <Input
            className='library-input'
            value={keyword}
            placeholder={`搜索${tab}`}
            onInput={(event) => setKeyword(event.detail.value)}
          />
        </View>

        <View className='library-tabs'>
          {TABS.map((item) => (
            <View
              key={item}
              className={`library-tab${item === tab ? ' is-active' : ''}`}
              onClick={() => setTab(item)}
            >
              <Text>{item}</Text>
            </View>
          ))}
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
            {!loading && !error && !visibleItems.length ? (
              <View className='library-hint'>
                <Text>{items.length ? `没有匹配的${tab}` : '没有可用的专家或技能'}</Text>
              </View>
            ) : null}

            {visibleItems.map((item, index) => {
              const sameSectionAsNext = visibleItems[index + 1]?.section === item.section
              const chipId = `${item.chipKind ?? 'skill'}:${item.id}`
              const selected = selectedIds.includes(chipId)
              return (
                <Fragment key={`${item.section ?? ''}:${item.id}`}>
                  <View
                    className={`library-row${selected ? ' is-selected' : ''}`}
                    onClick={() => onPick(item)}
                  >
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
                    {selected ? (
                      <View className='library-check'>
                        <Icon name='check' size={20} color='#111316' strokeWidth={2} />
                      </View>
                    ) : null}
                  </View>
                  {sameSectionAsNext ? <View className='library-line' /> : null}
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
