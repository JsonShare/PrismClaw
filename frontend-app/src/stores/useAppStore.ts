import Taro from '@tarojs/taro'
import { create } from 'zustand'

import { getToken } from '../services/request'
import { listWorkspaces } from '../services/workspaces'
import type { Workspace } from '../services/workspaces'
import type { DialogSpec, Mode } from '../types'
import {
  applyThemeToDOM,
  readStoredTheme,
  resolveTheme,
  systemTheme,
  THEME_KEY,
} from '../utils/theme'
import type { ResolvedTheme, Theme } from '../utils/theme'

interface AppStore {
  mode: Mode
  workspaceId: string
  dialog: DialogSpec | null
  theme: Theme
  systemTheme: ResolvedTheme
  workspaces: Workspace[]
  workspacesLoaded: boolean
  /** 工作区同步失败原因；成功同步后清空，用于页面展示「重试」而非一直「同步中」 */
  workspacesError: string

  setMode: (mode: Mode) => void
  setWorkspaceId: (id: string) => void
  openDialog: (spec: DialogSpec) => void
  closeDialog: () => void
  setTheme: (theme: Theme) => void
  setSystemTheme: (theme: ResolvedTheme) => void
  loadWorkspaces: (force?: boolean) => Promise<Workspace[]>
}

const WORKSPACE_KEY = 'pc_workspace_id'

const initialTheme = readStoredTheme()
const initialSystemTheme = systemTheme()
applyThemeToDOM(resolveTheme(initialTheme, initialSystemTheme))

function readStoredWorkspace(): string {
  try {
    return String(Taro.getStorageSync(WORKSPACE_KEY) || 'default')
  } catch {
    return 'default'
  }
}

export const useAppStore = create<AppStore>((set, get) => ({
  mode: 'chat',
  workspaceId: readStoredWorkspace(),
  dialog: null,
  theme: initialTheme,
  systemTheme: initialSystemTheme,
  workspaces: [],
  workspacesLoaded: false,
  workspacesError: '',

  setMode: (mode) => set({ mode }),
  setWorkspaceId: (workspaceId) => {
    Taro.setStorageSync(WORKSPACE_KEY, workspaceId)
    set({ workspaceId })
  },
  openDialog: (dialog) => set({ dialog }),
  closeDialog: () => set({ dialog: null }),

  setTheme: (theme) => {
    Taro.setStorageSync(THEME_KEY, theme)
    applyThemeToDOM(resolveTheme(theme, get().systemTheme))
    set({ theme })
  },

  setSystemTheme: (systemThemeValue) => {
    applyThemeToDOM(resolveTheme(get().theme, systemThemeValue))
    set({ systemTheme: systemThemeValue })
  },

  loadWorkspaces: async (force = false) => {
    // 未登录时首页仍会挂载一次，直接跳过，避免冷启动打一个必然 401 的请求
    if (!getToken()) return []
    if (!force && get().workspacesLoaded && get().workspaces.length > 0) {
      return get().workspaces
    }
    try {
      const workspaces = await listWorkspaces()
      set({ workspaces, workspacesLoaded: true, workspacesError: '' })
      if (workspaces.length > 0 && !workspaces.some((w) => w.id === get().workspaceId)) {
        set({ workspaceId: workspaces[0].id })
      }
      return workspaces
    } catch (error) {
      set({ workspacesError: (error as Error).message || '工作区同步失败' })
      throw error
    }
  },
}))
