import { useCallback, useMemo } from 'react'
import { useChatStore } from '../../stores'
import type { ConversationController } from './useConversationController'
import type { Message, QuestionItem, QuestionOption } from '../../types'

/**
 * AskUserQuestion 澄清流（分页问答）的唯一状态 owner。
 *
 * 此前解析（clarifyRequest/messages 双路径）、选择态（selectedAnswers/
 * currentQuestionIndex）、提交/跳过逻辑散落在 ChatArea 主体与 JSX 内联，
 * 且 ChatArea 的 normal submit 还要跨边界重置选择态。现收口为一个 hook：
 * ChatArea 只消费 isClarifying（输入屏蔽）与 resetSelections（发送后重置），
 * 卡片渲染所需的全部状态/动作经 flow 对象传给 ClarifyCard。
 *
 * 选择态已上提至 store 的 ConvState.clarifySession，刷新/切菜单后可恢复
 * 半完成的选择流程；与 clarifyRequest 同生命周期——新请求到达时由
 * setClarifyRequest 同步初始化为空、runGate 切走时自动清空。
 */

const EMPTY_MESSAGES: Message[] = []

// 把后端归一化后的 questions 数组直读映射为前端提问卡片结构（QuestionItem[]）。
// 两条路径的输入都已在后端契约边界归一成可信 JSON，前端只做直读、不做形状猜测：
//   - 执行中暂停态：conv 级 clarifyRequest.questions（runner._normalize_clarify_questions）
//   - 终态/历史重放：assistant 消息 toolCalls.arguments.questions（routes 序列化边界归一化）
// 因此这里直接读取 {question, header, multi_select, options:[{label, description}]}，
// 仅保留最小类型的防御（输入是 unknown[]），不补偿任何非规范写法。
// 注意字段名与后端契约一致为 snake_case（normalize_clarify_questions 的输出），
// 在此边界一次性映射为内部模型的多选标志。
function extractAskUserQuestions(rawQuestions: unknown[]): QuestionItem[] {
  const questions: QuestionItem[] = []
  const seenQuestions = new Set<string>()

  for (const q of rawQuestions) {
    if (typeof q !== 'object' || q === null) continue
    const item = q as Record<string, unknown>
    const question = typeof item.question === 'string' ? item.question.trim() : ''
    if (!question) continue
    const header = typeof item.header === 'string' ? item.header.trim() : ''
    const multiSelect = item.multi_select === true
    const rawOptions: unknown[] = Array.isArray(item.options) ? item.options : []

    const options: QuestionOption[] = []
    for (const o of rawOptions) {
      if (typeof o !== 'object' || o === null) continue
      const obj = o as Record<string, unknown>
      const label = typeof obj.label === 'string' ? obj.label.trim() : ''
      if (!label) continue
      const description = typeof obj.description === 'string' ? obj.description.trim() : ''
      options.push({ label, description })
    }
    if (!options.length) continue

    const questionKey = question.slice(0, 100)
    if (!seenQuestions.has(questionKey)) {
      seenQuestions.add(questionKey)
      questions.push({ question, header, multiSelect, options })
    }
  }

  return questions
}

export interface ClarifyFlow {
  questions: QuestionItem[]
  isClarifying: boolean
  currentQuestionIndex: number
  selectedAnswers: Record<number, Set<string>>
  toggleOption: (qIdx: number, label: string, multiSelect: boolean) => void
  addFreeAnswer: (qIdx: number, value: string) => void
  advance: () => void
  finalSubmit: () => void
  skipAll: () => void
  resetSelections: () => void
}

export function useClarifyFlow(
  conversationId: string,
  controller: ConversationController,
): ClarifyFlow {
  // AskUserQuestion 人机交互暂停态（执行中）由后端经 tool.clarify_request 注入，
  // 承载提问与提交凭据。必须优先于 messages 解析：执行中该工具调用落在合成
  // STREAMING_ID 流式消息（liveToolCalls）上，持久化 messages 数组里尚无此助手消息。
  // 注意：messages / clarifySession / clarifyRequest 三者的**实时**值在回调内必须用
  // useChatStore.getState() 拿，不能用顶部 store selector capture 的快照——ClarifyCard
  // 的 Enter 处理是 addFreeAnswer() → finalSubmit() 串调，中间没有 re-render 机会，
  // 用快照读会把刚写入 store 的答案判成空集（2026-10-28 bug 根因）。
  const clarifyRequest = useChatStore(
    (s) => s.convStates[conversationId]?.clarifyRequest ?? null,
  )
  // clarifySession: store 级 HITL 选择态。与 clarifyRequest 同生命周期——
  // runGate 切走时由 setClarifyRequest / finalizeStreaming / initStreaming 同步清。
  // 上提到 store 让刷新后能恢复半完成的选择流程。
  const clarifySession = useChatStore(
    (s) => s.convStates[conversationId]?.clarifySession ?? null,
  )
  const setClarifySession = useChatStore((s) => s.setClarifySession)
  const markMsgClarified = useChatStore((s) => s.markMsgClarified)

  // 快照读取：用于渲染（稳定引用）。回调内必须用 getState() 拿最新值，
  // 避免闭包陈旧——用户连续快速点选时不能用上一次 render 的旧值覆盖新值。
  const selectedAnswers = clarifySession?.selectedAnswers ?? {}
  const currentQuestionIndex = clarifySession?.currentQuestionIndex ?? 0

  const questions = useMemo((): QuestionItem[] => {
    // AskUserQuestion 澄清卡片**唯一**来源：conv 级 clarifyRequest。
    //   - 执行中暂停态：由 tool.clarify_request → setClarifyRequest 注入。
    //   - 刷新/重连后的待回答暂停：由 hydrateRun 从 RunProjection.clarification 重建。
    // 绝不能再从历史 assistant 消息的 toolCalls 反推提问卡片——已完成的 run 里，
    // 执行中途已经作答过的 ask_user 工具调用仍会留存在最终消息的 toolCalls 中，
    // 反推会把「已作答的提问」误判成「待回答」，导致任务完成后卡片再次弹出
    // （re-popup，即截图里「执行完成」上叠着「需要回答才能继续」的现象）。已作答
    // 的 ask_user 本就展示在执行时间线的「向用户提问」行里可回看，无需弹卡。
    if (clarifyRequest && Array.isArray(clarifyRequest.questions) && clarifyRequest.questions.length > 0) {
      return extractAskUserQuestions(clarifyRequest.questions)
    }
    return []
  }, [clarifyRequest])

  // AskUserQuestion 暂停期间（用户尚未回答）屏蔽底部普通输入框，
  // 强制用户先通过提问卡片作答，避免开启新一轮对话打断执行流。
  const isClarifying = questions.length > 0

  const resetSelections = useCallback(() => {
    setClarifySession(conversationId, {
      selectedAnswers: {},
      currentQuestionIndex: 0,
    })
  }, [conversationId, setClarifySession])

  // 所有 session 写入回调内部先从 getState() 拿最新值再 set，避免闭包陈旧。
  const toggleOption = useCallback((qIdx: number, label: string, multiSelect: boolean) => {
    const cur = useChatStore.getState().convStates[conversationId]?.clarifySession
    const newSet = new Set(cur?.selectedAnswers[qIdx] ?? [])
    if (multiSelect) {
      if (newSet.has(label)) newSet.delete(label)
      else newSet.add(label)
    } else {
      newSet.clear()
      newSet.add(label)
    }
    setClarifySession(conversationId, {
      selectedAnswers: { ...(cur?.selectedAnswers ?? {}), [qIdx]: newSet },
      currentQuestionIndex: cur?.currentQuestionIndex ?? 0,
    })
  }, [conversationId, setClarifySession])

  const addFreeAnswer = useCallback((qIdx: number, value: string) => {
    const cur = useChatStore.getState().convStates[conversationId]?.clarifySession
    const question = questions[qIdx]
    const newSet = new Set(cur?.selectedAnswers[qIdx] ?? [])
    if (!question?.multiSelect) newSet.clear()
    newSet.add(value)
    setClarifySession(conversationId, {
      selectedAnswers: { ...(cur?.selectedAnswers ?? {}), [qIdx]: newSet },
      currentQuestionIndex: cur?.currentQuestionIndex ?? 0,
    })
  }, [conversationId, questions, setClarifySession])

  /* ── 分页问答：前进到下一题 ── */
  const advance = useCallback(() => {
    const cur = useChatStore.getState().convStates[conversationId]?.clarifySession
    const idx = cur?.currentQuestionIndex ?? 0
    if (idx < questions.length - 1) {
      setClarifySession(conversationId, {
        selectedAnswers: cur?.selectedAnswers ?? {},
        currentQuestionIndex: idx + 1,
      })
    }
  }, [conversationId, questions.length, setClarifySession])

  /* ── 最终提交：收集全部已选答案，按暂停态/历史重放两条路径回传 ── */
  const finalSubmit = useCallback(() => {
    // 内部必须从 getState() 取最新值，避免闭包陈旧：
    // ClarifyCard 的 Enter 处理是 addFreeAnswer() → finalSubmit() 串调，
    // 中间没有 re-render 机会，finalSubmit 若读本次 render 快照会把
    // 刚写入 store 的答案判成空集 → 所有题"（跳过）"（2026-10-28 回归）。
    const state = useChatStore.getState()
    const cur = state.convStates[conversationId]
    const session = cur?.clarifySession
    const selectedAnswersNow = session?.selectedAnswers ?? {}
    const clarifyRequestNow = cur?.clarifyRequest ?? null
    const messagesNow = cur?.messages ?? EMPTY_MESSAGES

    const answerParts: string[] = []
    questions.forEach((q, idx) => {
      const selectedForQ = selectedAnswersNow[idx] || new Set()
      const prefix = q.header || `Q${idx + 1}`
      if (selectedForQ.size > 0) {
        answerParts.push(`${prefix}：${[...selectedForQ].join('；')}`)
      } else {
        // 未答显式标记"（跳过）"，让后端/LLM 能区分"跳过"和"没收到"
        answerParts.push(`${prefix}：（跳过）`)
      }
    })
    const answer = answerParts.join('\n')

    // 执行中暂停态：优先用 conv 级 clarifyRequest 的提交凭据，同一轮断点续跑。
    if (clarifyRequestNow) {
      controller.clarifySubmit(clarifyRequestNow.approvalId, clarifyRequestNow.toolCallId, answer)
    } else {
      // 兜底：非暂停态（如历史消息重放）从消息 metadata 取凭据，否则退化为开启新一轮对话。
      const latestAssistantMsg = messagesNow.filter((m) => m.role === 'assistant').pop()
      const metaClarify = latestAssistantMsg?.metadata?.clarifyRequest ?? null
      if (metaClarify) {
        controller.clarifySubmit(metaClarify.approvalId, metaClarify.toolCallId, answer)
      } else {
        void controller.sendMessage(answer)
      }
    }

    // 关闭面板：仅历史重放路径需要把消息标记为已澄清（执行中走 conv 级 clarifyRequest，
    // 其清除与标记由 useChatSocket 在收到 tool.clarify / chat.done 后统一处理）。
    if (!clarifyRequestNow) {
      const latestAssistantMsg = messagesNow.filter((m) => m.role === 'assistant').pop()
      if (latestAssistantMsg) {
        markMsgClarified(conversationId, latestAssistantMsg.id)
      }
    }
    resetSelections()
  }, [questions, controller, markMsgClarified, conversationId, resetSelections])

  /* ── 跳过全部：执行中回传空答案解除 await；历史重放仅标记已澄清 ── */
  const skipAll = useCallback(() => {
    const state = useChatStore.getState()
    const cur = state.convStates[conversationId]
    const clarifyRequestNow = cur?.clarifyRequest ?? null
    const messagesNow = cur?.messages ?? EMPTY_MESSAGES

    if (clarifyRequestNow) {
      controller.clarifySubmit(
        clarifyRequestNow.approvalId,
        clarifyRequestNow.toolCallId,
        '（用户跳过了所有问题）',
      )
    } else {
      const latestAssistantMsg = messagesNow.filter((m) => m.role === 'assistant').pop()
      if (latestAssistantMsg) {
        markMsgClarified(conversationId, latestAssistantMsg.id)
      }
    }
    resetSelections()
  }, [controller, markMsgClarified, conversationId, resetSelections])

  return {
    questions,
    isClarifying,
    currentQuestionIndex,
    selectedAnswers,
    toggleOption,
    addFreeAnswer,
    advance,
    finalSubmit,
    skipAll,
    resetSelections,
  }
}
