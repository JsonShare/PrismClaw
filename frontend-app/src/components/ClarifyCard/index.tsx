import { Input, Text, View } from '@tarojs/components'
import { useState } from 'react'

import type { QuestionItem } from '../../types'
import type { ClarifyRequest } from '../../stores/useChatStore'

interface ClarifyCardProps {
  clarify: ClarifyRequest
  onSubmit: (answer: string) => void
  onSkip: () => void
}

export function ClarifyCard({ clarify, onSubmit, onSkip }: ClarifyCardProps) {
  const [selections, setSelections] = useState<Record<number, string[]>>({})
  const [freeText, setFreeText] = useState<Record<number, string>>({})
  const [sent, setSent] = useState(false)

  const toggle = (index: number, question: QuestionItem, label: string) => {
    setSelections((prev) => {
      const current = prev[index] ?? []
      if (question.multiSelect) {
        return {
          ...prev,
          [index]: current.includes(label) ? current.filter((item) => item !== label) : current.concat(label),
        }
      }
      return { ...prev, [index]: current[0] === label ? [] : [label] }
    })
    setFreeText((prev) => ({ ...prev, [index]: '' }))
  }

  const submit = () => {
    if (sent) return
    setSent(true)
    const parts = clarify.questions.map((question, index) => {
      const header = question.header || `Q${index + 1}`
      const values = (selections[index] ?? []).slice()
      const free = (freeText[index] ?? '').trim()
      if (free) values.push(free)
      return `${header}：${values.length ? values.join('；') : '（跳过）'}`
    })
    onSubmit(parts.join('\n'))
  }

  return (
    <View className='clarify-card'>
      <View className='clarify-head'>
        <View className='clarify-mark'>
          <Text>?</Text>
        </View>
        <Text className='clarify-kicker'>需要你的确认</Text>
      </View>
      {clarify.questions.map((question, index) => (
        <View key={`${question.question}-${index}`} className='clarify-item'>
          {question.header ? <Text className='clarify-risk'>{question.header}</Text> : null}
          <Text className='clarify-title'>
            {question.question}
            {question.multiSelect ? <Text className='clarify-multi'>可多选</Text> : null}
          </Text>
          <View className='clarify-options'>
            {question.options.map((option) => {
              const active = (selections[index] ?? []).includes(option.label)
              return (
                <View
                  key={option.label}
                  className={`clarify-option${active ? ' is-active' : ''}`}
                  onClick={() => toggle(index, question, option.label)}
                >
                  <Text className='clarify-option-label'>{option.label}</Text>
                  {option.description ? (
                    <Text className='clarify-option-desc'>{option.description}</Text>
                  ) : null}
                </View>
              )
            })}
          </View>
          <Input
            className='clarify-input'
            value={freeText[index] ?? ''}
            maxlength={500}
            placeholder='或直接输入你的答案'
            onInput={(event) => {
              const value = event.detail.value
              setFreeText((prev) => ({ ...prev, [index]: value }))
              if (value) setSelections((prev) => ({ ...prev, [index]: [] }))
            }}
          />
        </View>
      ))}
      <View className='clarify-actions'>
        <View
          className={`clarify-btn${sent ? ' is-disabled' : ''}`}
          onClick={() => {
            if (sent) return
            setSent(true)
            onSkip()
          }}
        >
          <Text>跳过</Text>
        </View>
        <View
          className={`clarify-btn is-primary${sent ? ' is-disabled' : ''}`}
          onClick={submit}
        >
          <Text>{sent ? '已提交' : '提交'}</Text>
        </View>
      </View>
    </View>
  )
}
