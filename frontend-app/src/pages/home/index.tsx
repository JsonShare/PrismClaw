import { Image, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useEffect, useState } from 'react'

import AppDialog from '../../components/AppDialog'
import AppHeader from '../../components/AppHeader'
import ChatHeader from '../../components/ChatHeader'
import Composer from '../../components/Composer'
import type { ComposerPayload } from '../../components/Composer'
import ConversationPane from '../../components/ConversationPane'
import Icon from '../../components/Icon'
import WorkspacePicker from '../../components/WorkspacePicker'
import { composeContent } from '../../lib/chat/markers'
import { useThemeClass } from '../../hooks/useThemeClass'
import { createSession } from '../../services/sessions'
import { socket } from '../../services/socket'
import { uploadAttachments } from '../../services/uploads'
import { useAppStore } from '../../stores/useAppStore'
import { useChatStore } from '../../stores/useChatStore'
import { HOME_COMPOSER_KEY, useComposerStore } from '../../stores/useComposerStore'
import { svgDataUri } from '../../utils/svg'

type CardKind = 'medical' | 'legal' | 'office'

interface TaskCardItem {
  kind: CardKind
  title: string
  desc: string
}

const svg = (body: string) =>
  svgDataUri(
    `<svg xmlns="http://www.w3.org/2000/svg" width="76" height="76" viewBox="0 0 76 76">${body}</svg>`,
  )

/** 卡片图标：设计稿为位图缩略图，这里用内联 SVG 复刻（纯 ASCII，走 base64 Data URI） */
const CARD_ICONS: Record<CardKind, string> = {
  medical: svg(
    '<defs><linearGradient id="m" x1="0" y1="0" x2="1" y2="1">' +
      '<stop offset="0" stop-color="#d7f3e5"/><stop offset=".55" stop-color="#86d8b2"/>' +
      '<stop offset="1" stop-color="#38a87b"/></linearGradient></defs>' +
      '<rect width="76" height="76" rx="18" fill="url(#m)"/>' +
      '<rect x="33" y="19" width="10" height="38" rx="3" fill="#ffffff"/>' +
      '<rect x="19" y="33" width="38" height="10" rx="3" fill="#ffffff"/>' +
      '<rect x="1" y="1" width="74" height="74" rx="17" fill="none" stroke="#ffffff" stroke-width="2"/>',
  ),
  legal: svg(
    '<defs><linearGradient id="l" x1="0" y1="0" x2="1" y2="1">' +
      '<stop offset="0" stop-color="#d3dfff"/><stop offset=".55" stop-color="#93aaf3"/>' +
      '<stop offset="1" stop-color="#5c77da"/></linearGradient></defs>' +
      '<rect width="76" height="76" rx="18" fill="url(#l)"/>' +
      '<g stroke="#ffffff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" fill="none">' +
      '<path d="M38 19v37"/><path d="M22 24h32"/><path d="M21 24l-6 12h12z"/><path d="M55 24l-6 12h12z"/>' +
      '<path d="M27 58h22"/></g>' +
      '<rect x="1" y="1" width="74" height="74" rx="17" fill="none" stroke="#ffffff" stroke-width="2"/>',
  ),
  office: svg(
    '<defs><linearGradient id="o" x1="0" y1="0" x2="1" y2="1">' +
      '<stop offset="0" stop-color="#fbe3a6"/><stop offset=".55" stop-color="#f0bd5c"/>' +
      '<stop offset="1" stop-color="#dc992e"/></linearGradient></defs>' +
      '<rect width="76" height="76" rx="18" fill="url(#o)"/>' +
      '<rect x="19" y="15" width="38" height="46" rx="5" fill="#fffdf6"/>' +
      '<path d="M27 29h4M27 38h4M27 47h4" stroke="#e6a83a" stroke-width="3" stroke-linecap="round"/>' +
      '<path d="M36 29h14M36 38h14M36 47h9" stroke="#ded0b1" stroke-width="3" stroke-linecap="round"/>' +
      '<rect x="1" y="1" width="74" height="74" rx="17" fill="none" stroke="#ffffff" stroke-width="2"/>',
  ),
}

const QUOTE_MARK = svgDataUri(
  '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#0d0d10" stroke-width="3.6" stroke-linecap="round"><path d="M7 18 10 7"/><path d="M14 16 17 5"/></svg>',
)

/** 推荐问题池：医疗 / 法律 / 办公，点击卡片即带入输入框 */
const QUESTION_POOL: Record<CardKind, string[]> = {
  medical: [
    '体检报告显示转氨酶偏高，需要复查哪些项目？',
    '高血压患者日常饮食和运动要注意什么？',
    '空腹血糖 6.3，算糖尿病吗？',
    '孩子发烧 38.5℃，家庭护理该怎么做？',
    '长期久坐颈椎不适，有哪些康复训练？',
  ],
  legal: [
    '劳动合同到期不续签，公司要付补偿金吗？',
    '租房押金被无故克扣，该怎么维权？',
    '婚前房产婚后加名，离婚时如何分割？',
    '被人在网上造谣，可以要求平台删帖并索赔吗？',
    '交通事故对方全责但不赔钱，起诉流程是什么？',
  ],
  office: [
    '帮我写一份周报模板，包含进展、风险和下周计划',
    '把这段会议记录整理成带负责人的待办清单',
    '写一封邮件，婉拒合作方过高的报价',
    '用表格对比三个方案的成本、周期和风险',
    '帮我列一份季度复盘汇报的大纲',
  ],
}

const CARD_LABEL: Record<CardKind, string> = {
  medical: '医疗咨询',
  legal: '法律咨询',
  office: '办公效率',
}

function shuffle<T>(items: T[]): T[] {
  const list = [...items]
  for (let i = list.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[list[i], list[j]] = [list[j], list[i]]
  }
  return list
}

/** 每个领域先各取一条，再从当前卡片数最少的领域补一张，保证 4 张且尽量分散 */
function pickCards(): TaskCardItem[] {
  const kinds = Object.keys(QUESTION_POOL) as CardKind[]
  const picked = shuffle(kinds).map((kind) => {
    const pool = QUESTION_POOL[kind]
    return { kind, title: CARD_LABEL[kind], desc: pool[Math.floor(Math.random() * pool.length)] }
  })
  const used = new Set(picked.map((card) => card.desc))
  const countByKind = picked.reduce<Record<CardKind, number>>(
    (acc, card) => ({ ...acc, [card.kind]: (acc[card.kind] || 0) + 1 }),
    { medical: 0, legal: 0, office: 0 },
  )
  const minCount = Math.min(...Object.values(countByKind))
  const candidates = kinds.filter((kind) => countByKind[kind] === minCount)
  const extraKind = candidates[Math.floor(Math.random() * candidates.length)]
  const pool = QUESTION_POOL[extraKind].filter((desc) => !used.has(desc))
  if (pool.length) {
    picked.push({
      kind: extraKind,
      title: CARD_LABEL[extraKind],
      desc: pool[Math.floor(Math.random() * pool.length)],
    })
  }
  return shuffle(picked)
}

export default function Home() {
  const workspaceId = useAppStore((s) => s.workspaceId)
  const setWorkspaceId = useAppStore((s) => s.setWorkspaceId)
  const workspaces = useAppStore((s) => s.workspaces)
  const loadWorkspaces = useAppStore((s) => s.loadWorkspaces)
  const setComposerDraft = useComposerStore((s) => s.setDraft)
  const homeDraft = useComposerStore((s) => s.drafts[HOME_COMPOSER_KEY] ?? '')
  const primeConversation = useChatStore((s) => s.primeConversation)
  const sendMessage = useChatStore((s) => s.sendMessage)
  const conversations = useChatStore((s) => s.conversations)

  const pageClass = useThemeClass('page')
  // 发送后在本页原地展开对话，不再跳转到独立会话页
  const [inlineSessionId, setInlineSessionId] = useState('')
  const [inlineTitle, setInlineTitle] = useState('')
  const [focused, setFocused] = useState(false)
  const [cards, setCards] = useState<TaskCardItem[]>(pickCards)

  // 聚焦或已有输入时收起推荐卡片（对齐设计稿的键盘展开态）
  const showRecs = !focused && !homeDraft.trim()

  useEffect(() => {
    loadWorkspaces().catch(() => undefined)
    socket.connect()
  }, [])

  const handleSend = async ({ text, chips, files }: ComposerPayload) => {
    let available = workspaces
    if (!available.length) available = await loadWorkspaces(true)
    const targetWorkspaceId = available.some((item) => item.id === workspaceId)
      ? workspaceId
      : available[0]?.id
    if (!targetWorkspaceId) throw new Error('还没有可用的工作区')

    const fallbackTitle = chips[0]?.label || files[0]?.name || '新任务'
    const res = await createSession(targetWorkspaceId, (text || fallbackTitle).slice(0, 30))
    const attachments = files.length
      ? await uploadAttachments(files, {
          workspaceId: targetWorkspaceId,
          sessionId: res.session_id,
        })
      : []
    if (targetWorkspaceId !== workspaceId) setWorkspaceId(targetWorkspaceId)
    // 先把消息放进去，再原地展开，避免首条消息丢失或需要二次拉取
    primeConversation(res.session_id, targetWorkspaceId)
    setInlineTitle((text || fallbackTitle).slice(0, 24))
    sendMessage(res.session_id, composeContent(text, chips), {
      workspaceId: targetWorkspaceId,
      attachments,
    })
    setInlineSessionId(res.session_id)
  }

  if (inlineSessionId) {
    // 列表里的会话标题优先，重命名后页头能立即更新；刚创建时用首条消息兜底
    const inlineTitleText =
      conversations.find((item) => item.id === inlineSessionId)?.title?.trim() ||
      inlineTitle ||
      '新任务'
    return (
      <View className={`${pageClass} chat-page`}>
        <ChatHeader
          sessionId={inlineSessionId}
          title={inlineTitleText}
          onBack={() => setInlineSessionId('')}
          onNew={() => setInlineSessionId('')}
          onRenamed={setInlineTitle}
          onDeleted={() => setInlineSessionId('')}
        />
        <ConversationPane
          sessionId={inlineSessionId}
          placeholder='描述任务或按住说话'
        />
      </View>
    )
  }

  return (
    <View className={`${pageClass} nt-page`}>
      <AppHeader variant='home' onMenu={() => Taro.navigateTo({ url: '/pages/tasks/index' })} />

      <View className='nt-main'>
        {/* 内容区独立滚动：小屏/键盘弹出时输入区保持贴底可见 */}
        <View className='nt-scroll'>
          <View className='nt-hero'>
          <View className='nt-hero-title'>
            <View className='nt-hero-line nt-hero-first'>
              <Text>随时随地</Text>
              <Image className='nt-quote' src={QUOTE_MARK} mode='aspectFit' />
            </View>
            <Text className='nt-hero-line'>帮您高效干活</Text>
          </View>
          <View
            className='nt-shuffle'
            onClick={() => setCards(pickCards())}
          >
            <Icon name='refresh' size={13} color='#7e858f' strokeWidth={1.8} />
            <Text className='nt-shuffle-text'>换一换</Text>
          </View>
        </View>

        {showRecs ? (
          <View className='nt-cards'>
            {cards.map((card) => (
              <View
                className='nt-card'
                key={`${card.kind}-${card.desc}`}
                onClick={() => setComposerDraft(HOME_COMPOSER_KEY, card.desc)}
              >
                <Image className='nt-card-icon' src={CARD_ICONS[card.kind]} mode='aspectFit' />
                <Text className='nt-card-title'>{card.title}</Text>
                <Text className='nt-card-desc'>{card.desc}</Text>
              </View>
            ))}
          </View>
        ) : null}

        <View className='nt-fill' />
        </View>

        <View className='nt-dock'>
          <Composer
            composerKey={HOME_COMPOSER_KEY}
            placeholder='描述任务或按住说话'
            expandedPlaceholder='描述任务'
            flat
            onFocusChange={setFocused}
            onSend={handleSend}
            footer={
              <View className='dock-tools'>
                <WorkspacePicker />
              </View>
            }
          />
        </View>
      </View>

      <AppDialog />
    </View>
  )
}
