import { create } from 'zustand'

export type AgentMode = 'chat' | 'work'

interface PermissionState {
  mode: AgentMode
}

interface PermissionActions {
  setMode: (mode: AgentMode) => void
}

export type PermissionStore = PermissionState & PermissionActions

export const usePermissionStore = create<PermissionStore>((set) => ({
  mode: 'work',
  setMode: (mode: AgentMode) => set({ mode }),
}))
