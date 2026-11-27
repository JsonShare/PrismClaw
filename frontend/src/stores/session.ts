import { create } from 'zustand'
import type { Conversation } from '../types'

export const DRAFT_SESSION_KEY = '__draft__'

export interface ExpertState {
  expert_id: string | null
  source: string | null
  selected_skill_ids: string[]
  /** 后端判定的不可用原因码（EXPERT_UNAVAILABLE / MODULE_DISABLED 等）；可用时为 null */
  unavailable_reason: string | null
}

export const EMPTY_EXPERT_STATE: ExpertState = {
  expert_id: null,
  source: null,
  selected_skill_ids: [],
  unavailable_reason: null,
}

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
  expertStates: Record<string, ExpertState>
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
  setExpertState: (id: string, state: ExpertState) => void
  setSelectedSkillIds: (id: string, selectedSkillIds: string[]) => void
}

type SessionStore = SessionState & SessionActions

export const useSessionStore = create<SessionStore>((set, get) => ({
  conversations: [],
  activeConvId: loadInitialActiveConvId(),
  expertStates: {},
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

  setExpertState: (id, expertState) =>
    set((state) => ({ expertStates: { ...state.expertStates, [id]: expertState } })),

  setSelectedSkillIds: (id, selectedSkillIds) =>
    set((state) => ({
      expertStates: {
        ...state.expertStates,
        [id]: {
          ...(state.expertStates[id] ?? EMPTY_EXPERT_STATE),
          selected_skill_ids: selectedSkillIds,
        },
      },
    })),
}))
