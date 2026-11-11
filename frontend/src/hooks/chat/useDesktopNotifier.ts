import { useEffect, useRef } from 'react'
import { useSessionStore } from '../../stores/session'
import type { NotificationEvent, NotificationListener } from './useChatSocket'

/**
 * 桌面通知 hook。
 *
 * 设计要点：
 * - 通过 `subscribeNotification` 订阅 useChatSocket 派发的通知事件，不侵入消息流职责；
 * - 触发条件：用户偏好开启 + 权限已授予，不按页面可见性过滤——前台/后台一致推送；
 * - 事件覆盖：任务完成、审批请求、澄清提问、异常终止；
 * - 跳过用户主动中断的回合（aborted=true），避免用户自己停止仍被通知；
 * - 去重：同一轮的重复终态由 useChatSocket 按轮次抑制，本 hook 不再做时间窗节流，
 *   避免同一会话连续的两次任务完成被误吞；
 * - 标题降级：会话列表无此 conv 时回退为各事件的默认标题；
 * - 点击通知 → 聚焦窗口 + 交给调用方按侧栏选择同一路径打开会话（切回对话区、
 *   标记已读、加载历史），审批/提问卡片随会话切换自动出现。
 */
export interface UseDesktopNotifierOptions {
  subscribeNotification: (listener: NotificationListener) => () => void
  /** 用户偏好：是否启用桌面通知。 */
  enabled: boolean
  /** 打开通知对应的会话；与侧栏点击走同一路径，保证已读状态与历史加载口径一致。 */
  onOpenConversation: (convId: string) => void | Promise<void>
}

/** 异常详情过长时截断，避免通知正文被系统裁切成半句话。 */
const ERROR_BODY_MAX = 120

interface NotificationContent {
  title: string
  body: string
  /** 审批/提问需要用户处理，通知常驻直到用户交互。 */
  requireInteraction: boolean
}

function buildContent(event: NotificationEvent, convTitle: string | null): NotificationContent {
  switch (event.kind) {
    case 'done':
      return {
        title: convTitle || '任务完成',
        body: '任务已完成，点击查看',
        requireInteraction: false,
      }
    case 'clarify':
      return {
        title: convTitle || '需要您的回答',
        body:
          event.questionCount > 0
            ? `需要您回答 ${event.questionCount} 个问题`
            : '需要您补充信息，点击查看',
        requireInteraction: true,
      }
    case 'error': {
      const detail = event.message.trim()
      const truncated = detail.length > ERROR_BODY_MAX ? `${detail.slice(0, ERROR_BODY_MAX)}…` : detail
      return {
        title: convTitle || '任务异常终止',
        body: truncated ? `任务异常终止：${truncated}` : '任务异常终止，点击查看',
        requireInteraction: false,
      }
    }
  }
}

export function useDesktopNotifier({
  subscribeNotification,
  enabled,
  onOpenConversation,
}: UseDesktopNotifierOptions) {
  // 用 ref 持有最新 enabled，避免偏好开关变化时重建监听器（subscribeNotification 稳定）。
  const enabledRef = useRef(enabled)
  useEffect(() => {
    enabledRef.current = enabled
  }, [enabled])

  // 打开会话的入口同样用 ref 持有，避免回调身份变化触发重新订阅。
  const openConversationRef = useRef(onOpenConversation)
  useEffect(() => {
    openConversationRef.current = onOpenConversation
  }, [onOpenConversation])

  useEffect(() => {
    if (typeof window === 'undefined') return
    // 不支持 Notification API 的环境（如部分 Safari）静默降级。
    if (typeof Notification === 'undefined') return

    const listener: NotificationListener = (event) => {
      if (!enabledRef.current) return
      if (Notification.permission !== 'granted') return
      // 用户主动中止的回合无需提醒。
      if (event.kind === 'done' && event.aborted) return

      const conv = useSessionStore.getState().conversations.find((c) => c.id === event.convId)
      const content = buildContent(event, conv?.title?.trim() || null)

      let n: Notification
      try {
        n = new Notification(content.title, {
          body: content.body,
          // tag 合并同一会话的重复通知，避免通知中心刷屏。
          tag: event.convId,
          requireInteraction: content.requireInteraction,
        })
      } catch {
        // 创建失败静默：不阻塞主流程。
        return
      }

      n.onclick = () => {
        try {
          window.focus()
        } catch {
          /* ignore */
        }
        void openConversationRef.current(event.convId)
        n.close()
      }
    }

    return subscribeNotification(listener)
  }, [subscribeNotification])
}
