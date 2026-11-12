import { useChatStore } from './chat'
import { useSessionStore } from './session'

export function purgeConversation(convId: string): string | null {
  const nextActive = useSessionStore.getState().removeConversation(convId)
  useChatStore.getState().dropConversation(convId)
  return nextActive
}

export { useChatStore } from './chat'
export { useSessionStore } from './session'
export { useUiStore } from './ui'
export { usePermissionStore, type AgentMode, type PermissionStore } from './permission'
export { useEffortStore, type EffortLevel, type EffortStore } from './effort'
