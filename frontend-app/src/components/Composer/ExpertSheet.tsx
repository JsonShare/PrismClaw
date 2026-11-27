import { Input, ScrollView, Text, View } from '@tarojs/components'
import { useMemo, useState } from 'react'

import Icon from '../Icon'
import type { ExpertItem } from '../../services/experts'

interface ExpertSheetProps {
  open: boolean
  experts: ExpertItem[]
  loading: boolean
  error: string
  selectedId: string | null
  onPick: (expertId: string | null) => void
  onClose: () => void
}

/** 会话级专家选择：单选，选中后写入会话头；再次点击已选专家即取消 */
export default function ExpertSheet({
  open,
  experts,
  loading,
  error,
  selectedId,
  onPick,
  onClose,
}: ExpertSheetProps) {
  const [keyword, setKeyword] = useState('')

  const items = useMemo(() => {
    const q = keyword.trim().toLowerCase()
    if (!q) return experts
    return experts.filter(
      (item) =>
        item.label.toLowerCase().includes(q) ||
        item.id.toLowerCase().includes(q) ||
        item.profession.toLowerCase().includes(q) ||
        item.description.toLowerCase().includes(q),
    )
  }, [experts, keyword])

  if (!open) return null

  return (
    <View className='sheet-layer' onClick={onClose}>
      <View className='library-sheet' onClick={(event) => event.stopPropagation()}>
        <View className='library-head'>
          <Text className='library-title'>选择专家</Text>
          <View className='library-close' onClick={onClose}>
            <Icon name='x' size={20} color='#8A9099' strokeWidth={2} />
          </View>
        </View>

        <View className='library-search'>
          <Icon name='search' size={16} color='#8A9099' />
          <Input
            className='library-input'
            value={keyword}
            placeholder='搜索专家或专家团'
            onInput={(event) => setKeyword(event.detail.value)}
          />
        </View>

        <ScrollView scrollY className='library-list'>
          <View className='library-body'>
            {loading ? (
              <View className='library-hint'>
                <Text>正在加载…</Text>
              </View>
            ) : null}
            {!loading && error ? (
              <View className='library-hint'>
                <Text>{error}</Text>
              </View>
            ) : null}
            {!loading && !error && !items.length ? (
              <View className='library-hint'>
                <Text>{keyword.trim() ? '没有匹配的专家' : '没有可用的专家'}</Text>
              </View>
            ) : null}

            {items.map((item) => {
              const selected = item.id === selectedId
              return (
                <View
                  className={`library-row${selected ? ' is-selected' : ''}`}
                  key={item.id}
                  onClick={() => onPick(selected ? null : item.id)}
                >
                  <View className='library-icon'>
                    <Text className='library-emoji'>{item.kind === 'team' ? '👥' : '🧑‍💼'}</Text>
                  </View>
                  <View className='library-main'>
                    <Text className='library-label'>{item.label}</Text>
                    <Text className='library-desc'>
                      {item.profession ? `${item.profession} · ` : ''}
                      {item.description}
                    </Text>
                  </View>
                  {selected ? (
                    <View className='library-check'>
                      <Icon name='check' size={20} color='#111316' strokeWidth={2} />
                    </View>
                  ) : null}
                </View>
              )
            })}
          </View>
        </ScrollView>

        <View className='library-footer'>
          <View
            className='library-collapse'
            onClick={() => (selectedId ? onPick(null) : onClose())}
          >
            <Text>{selectedId ? '取消使用专家' : '收起'}</Text>
          </View>
        </View>
      </View>
    </View>
  )
}
