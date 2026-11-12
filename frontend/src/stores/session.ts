import { create } from 'zustand'
import type { Conversation } from '../types'

const ACTIVE_CONV_KEY = 'prismclaw-active-conv'

function loadInitialActiveConvId(): string | null {
  if (typeof window === 'undefined') return null
  try {
    const stored = window.localStorage.getItem(ACTIVE_CONV_KEY)
    if (stored && typeof stored === 'string') {
      const s = stored.trim()
      return s ? s : null
    }
  } catch {}
  return null
}

function persistActiveConvId(id: string | null): void {
  if (typeof window === 'undefined') return
  try {
    if (id) {
      window.localStorage.setItem(ACTIVE_CONV_KEY, id)
    } else {
      window.localStorage.removeItem(ACTIVE_CONV_KEY)
    }
  } catch {}
}

interface SessionState {
  conversations: Conversation[]
  activeConvId: string | null
  /** 会话列表是否已至少成功加载过一次（刷新后用于避免误清空恢复的 activeConvId） */
  sessionsLoaded: boolean
  /** 是否仍有未加载的会话分页；为 true 时列表不完整，不能据此判定会话不存在 */
  sessionsHasMore: boolean
}

interface SessionActions {
  setConversations: (conversations: Conversation[]) => void
  markSessionsLoaded: () => void
  setSessionsHasMore: (hasMore: boolean) => void
  updateConversations: (updater: (prev: Conversation[]) => Conversation[]) => void
  prependConversation: (conv: Conversation) => void
  removeConversation: (id: string) => string | null
  setActiveConvId: (id: string | null) => void
}

type SessionStore = SessionState & SessionActions

export const useSessionStore = create<SessionStore>((set, get) => ({
  conversations: [],
  activeConvId: loadInitialActiveConvId(),
  sessionsLoaded: false,
  sessionsHasMore: true,

  setConversations: (conversations) => set({ conversations }),

  markSessionsLoaded: () => set({ sessionsLoaded: true }),

  setSessionsHasMore: (hasMore) => set({ sessionsHasMore: hasMore }),

  updateConversations: (updater) =>
    set((state) => ({ conversations: updater(state.conversations) })),

  prependConversation: (conv) =>
    set((state) => {
      if (state.conversations.some((c) => c.id === conv.id)) {
        return state
      }
      return { conversations: [conv, ...state.conversations] }
    }),

  removeConversation: (id) => {
    const { conversations, activeConvId } = get()
    const remaining = conversations.filter((c) => c.id !== id)
    set({ conversations: remaining })
    if (activeConvId === id) {
      return remaining[0]?.id ?? null
    }
    return activeConvId
  },

  setActiveConvId: (id) => {
    persistActiveConvId(id)
    set({ activeConvId: id })
  },
}))
