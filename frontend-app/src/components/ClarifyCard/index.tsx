import { Text, View } from '@tarojs/components'
import { useState } from 'react'

import type { QuestionItem } from '../../types'
import type { ClarifyRequest } from '../../stores/useChatStore'
import Icon from '../Icon'

interface ClarifyCardProps {
  clarify: ClarifyRequest
  onSubmit: (answer: string) => void
  onSkip: () => void
}

/**
 * 澄清操作框：底部浮层卡片，替换输入区。
 * 一题一页，底部为分页器与跳过；选中选项后按钮变为提交。
 */
export function ClarifyCard({ clarify, onSubmit, onSkip }: ClarifyCardProps) {
  const [selections, setSelections] = useState<Record<number, string[]>>({})
  const [page, setPage] = useState(0)
  const [sent, setSent] = useState(false)

  const total = clarify.questions.length
  const question: QuestionItem | undefined = clarify.questions[page]
  if (!question) return null

  const answered = (selections[page] ?? []).length > 0
  const isLast = page === total - 1

  const toggle = (index: number, item: QuestionItem, label: string) => {
    setSelections((prev) => {
      const current = prev[index] ?? []
      if (item.multiSelect) {
        return {
          ...prev,
          [index]: current.includes(label)
            ? current.filter((value) => value !== label)
            : current.concat(label),
        }
      }
      return { ...prev, [index]: current[0] === label ? [] : [label] }
    })
  }

  const skip = () => {
    if (sent) return
    setSent(true)
    onSkip()
  }

  const submit = () => {
    if (sent) return
    setSent(true)
    const parts = clarify.questions.map((item, index) => {
      const header = item.header || `Q${index + 1}`
      const values = (selections[index] ?? []).slice()
      return `${header}：${values.length ? values.join('；') : '（跳过）'}`
    })
    onSubmit(parts.join('\n'))
  }

  // 未答非末题：跳过本题进入下一题；末题未答：直接提交（未答记为跳过）
  const handlePrimary = () => {
    if (sent) return
    if (answered) {
      isLast ? submit() : setPage(page + 1)
    } else {
      isLast ? submit() : setPage(page + 1)
    }
  }

  const primaryText = answered ? (isLast ? '提交' : '下一题') : '跳过'

  return (
    <View className='clarify-card'>
      <View className='clarify-top'>
        <Text className='clarify-question'>
          {question.question}
          {question.multiSelect ? <Text className='clarify-multi'>（可多选）</Text> : null}
        </Text>
        <View className='clarify-close' onClick={skip}>
          <Icon name='x' size={19} color='#9aa0a8' strokeWidth={2} />
        </View>
      </View>

      {question.options.length ? (
        <View className='clarify-options'>
          {question.options.map((option) => {
            const active = (selections[page] ?? []).includes(option.label)
            return (
              <View
                key={option.label}
                className={`clarify-option${active ? ' is-active' : ''}`}
                onClick={() => toggle(page, question, option.label)}
              >
                <View
                  className={`clarify-option-indicator${
                    question.multiSelect ? ' is-checkbox' : ''
                  }${active ? ' is-active' : ''}`}
                >
                  {active && question.multiSelect ? (
                    <Icon name='check' size={12} color='#fff' strokeWidth={3} />
                  ) : null}
                </View>
                <View className='clarify-option-body'>
                  <Text className='clarify-option-label'>{option.label}</Text>
                  {option.description ? (
                    <Text className='clarify-option-desc'>{option.description}</Text>
                  ) : null}
                </View>
              </View>
            )
          })}
        </View>
      ) : null}

      <View className='clarify-foot'>
        {total > 1 ? (
          <View className='clarify-pager'>
            <View
              className={`clarify-pager-arrow${page === 0 ? ' is-disabled' : ''}`}
              onClick={() => page > 0 && setPage(page - 1)}
            >
              <Icon name='chevron-left' size={18} color='#9aa0a8' strokeWidth={2} />
            </View>
            <Text className='clarify-pager-text'>
              {page + 1}/{total}
            </Text>
            <View
              className={`clarify-pager-arrow${page === total - 1 ? ' is-disabled' : ''}`}
              onClick={() => page < total - 1 && setPage(page + 1)}
            >
              <Icon name='chevron-right' size={18} color='#9aa0a8' strokeWidth={2} />
            </View>
          </View>
        ) : (
          <View className='clarify-pager-spacer' />
        )}
        <View
          className={`clarify-skip${answered ? ' is-primary' : ''}${sent ? ' is-disabled' : ''}`}
          onClick={handlePrimary}
        >
          <Text className='clarify-skip-text'>{primaryText}</Text>
        </View>
      </View>
    </View>
  )
}
