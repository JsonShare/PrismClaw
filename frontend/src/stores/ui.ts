import { create } from 'zustand'
import type { Theme, Workspace } from '../types'

const THEME_KEY = 'prismclaw-theme'
const SECTION_KEY = 'prismclaw-section'
const WORKSPACES_LIST_KEY = 'prismclaw-workspaces-list'
const CHAT_WIDTH_KEY = 'prismclaw-chat-width'
const ACTIVE_WORKSPACE_KEY = 'prismclaw-active-workspace'

/** 对话内容区默认宽度（px）：600–1200，100 步长。
 *  仅作为新任务页面中间栏内容区的默认宽度，与右侧预览栏的拖拽宽度相互独立。 */
export type ChatWidth = number

export const CHAT_WIDTH_MIN = 600
export const CHAT_WIDTH_MAX = 1200
export const CHAT_WIDTH_DEFAULT = 1000

/** 内置 section 或动态域 section（domain:xxx 格式） */
export type AppSection = 'chat' | 'experts' | 'files' | 'outputs' | `domain:${string}`

function resolveSystemTheme(): 'light' | 'dark' {
  if (typeof window === 'undefined') return 'light'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function isValidTheme(value: string | null): value is Theme {
  return value === 'dark' || value === 'light' || value === 'system'
}

function loadInitialTheme(): Theme {
  if (typeof window === 'undefined') return 'light'
  const stored = window.localStorage.getItem(THEME_KEY)
  return isValidTheme(stored) ? stored : 'light'
}

/** 吸附到 100 步长并夹紧到 [600, 1200] */
function snapChatWidth(n: number): ChatWidth {
  const snapped = Math.round(n / 100) * 100
  return Math.min(CHAT_WIDTH_MAX, Math.max(CHAT_WIDTH_MIN, snapped))
}

function loadInitialChatWidth(): ChatWidth {
  if (typeof window === 'undefined') return CHAT_WIDTH_DEFAULT
  try {
    const stored = window.localStorage.getItem(CHAT_WIDTH_KEY)
    // 旧枚举值迁移：default→1000 / wide→1200
    if (stored === 'default') return CHAT_WIDTH_DEFAULT
    if (stored === 'wide') return CHAT_WIDTH_MAX
    const n = stored == null ? NaN : Number(stored)
    return Number.isFinite(n) ? snapChatWidth(n) : CHAT_WIDTH_DEFAULT
  } catch {
    return CHAT_WIDTH_DEFAULT
  }
}

function persistChatWidth(w: ChatWidth): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(CHAT_WIDTH_KEY, String(w))
  } catch {}
}

/** 有实际渲染分支的 section（'tasks'/'settings' 已删除，避免 localStorage 残留旧值导致空白） */
const VALID_SECTIONS = new Set(['chat', 'experts', 'files', 'outputs'])

function loadInitialSection(): AppSection {
  if (typeof window === 'undefined') return 'chat'
  try {
    const stored = window.localStorage.getItem(SECTION_KEY)
    if (!stored) return 'chat'
    if (VALID_SECTIONS.has(stored)) return stored as AppSection
    if (stored.startsWith('domain:')) return stored as AppSection
  } catch {}
  return 'chat'
}

function persistSection(section: AppSection): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(SECTION_KEY, section)
  } catch {}
}

function persistWorkspaces(list: Workspace[]): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(WORKSPACES_LIST_KEY, JSON.stringify(list))
  } catch {}
}

/** 工作区 ID 规范化：与后端 clean_workspace_id 正则
 *  ^[a-zA-Z0-9_\-\u4e00-\u9fa5]+$ 对齐。前端 handleCreateServerWorkspace 用
 *  同规则替换非法字符为 '-'，这里在 load/persist 入口做兜底，防止旧版脏值
 *  或 localStorage 中残留非法字符导致前后端 ID 不一致。 */
function sanitizeWorkspaceId(raw: string): string {
  const trim = raw.trim()
  if (!trim) return 'default'
  const cleaned = trim
    .replace(/[^a-zA-Z0-9\u4e00-\u9fa5_-]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-+|-+$/g, '')
  if (!cleaned) return 'default'
  if (cleaned === 'default') return 'default'
  return cleaned
}

/** 选中工作区持久化（2026-10-26）：theme/section/chatWidth/workspaces 之外
 *  最后一个未持久化的 UI 偏好。此前刷新即重置 'default'，造成技能面板/
 *  定时任务/文件上传的工作区语义漂移。无效 id 的回落兜底已有两道：
 *  removeWorkspace 删除时回落 + ChatPage 校验 effect 回 default。 */
function loadInitialActiveWorkspaceId(): string {
  if (typeof window === 'undefined') return 'default'
  try {
    const raw = window.localStorage.getItem(ACTIVE_WORKSPACE_KEY)
    if (!raw) return 'default'
    return sanitizeWorkspaceId(raw)
  } catch {
    return 'default'
  }
}

function persistActiveWorkspaceId(id: string): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(ACTIVE_WORKSPACE_KEY, id)
  } catch {}
}

function normalizeWorkspaces(raw: unknown): Workspace[] {
  const defaultWs: Workspace = { id: 'default', name: '默认工作区' }
  if (!Array.isArray(raw)) return [defaultWs]
  const out: Workspace[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const id = typeof (item as any).id === 'string' ? (item as any).id.trim() : ''
    const name = typeof (item as any).name === 'string' ? (item as any).name.trim() : ''
    if (!id) continue
    const ws: Workspace = { id, name: name || id }
    out.push(ws)
  }
  const byId = new Map<string, Workspace>()
  for (const w of out) byId.set(w.id, w)
  if (!byId.has('default')) byId.set('default', defaultWs)
  return Array.from(byId.values())
}

function loadInitialWorkspaces(): Workspace[] {
  if (typeof window === 'undefined') return [{ id: 'default', name: '默认工作区' }]
  try {
    const raw = window.localStorage.getItem(WORKSPACES_LIST_KEY)
    if (!raw) return [{ id: 'default', name: '默认工作区' }]
    return normalizeWorkspaces(JSON.parse(raw) as unknown)
  } catch {}
  return [{ id: 'default', name: '默认工作区' }]
}

function persistTheme(theme: Theme): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(THEME_KEY, theme)
  } catch {
    // Safari private mode rejects setItem; the DOM attribute is already
    // applied so theme works for the current session, just won't persist.
  }
}

function applyThemeToDOM(theme: Theme): void {
  if (typeof document === 'undefined') return
  const effective = theme === 'system' ? resolveSystemTheme() : theme
  document.documentElement.setAttribute('data-theme', effective)
  persistTheme(theme)
}

/** 当前实际生效的主题（已把 system 解析成 light/dark）。
 *  供需要在 JS 端跟随主题的表现逻辑（如代码高亮主题）使用，避免各自重复解析 system。 */
export function getEffectiveTheme(): 'dark' | 'light' {
  const stored = useUiStore.getState().theme
  return stored === 'system' ? resolveSystemTheme() : stored
}

interface UiState {
  theme: Theme
  chatWidth: ChatWidth
  sidebarCollapsed: boolean
  activeSection: AppSection
  activeWorkspaceId: string
  workspaces: Workspace[]
  /** 由其它页面（如自动化页「通过对话创建」）写入，用于跳转会话页后预填输入框。 */
  pendingComposerPrefill: { text: string; nonce: number } | null
}

interface UiActions {
  setTheme: (theme: Theme) => void
  setChatWidth: (w: ChatWidth) => void
  toggleTheme: () => void
  setSidebarCollapsed: (collapsed: boolean) => void
  toggleSidebar: () => void
  setActiveSection: (section: AppSection) => void
  setActiveWorkspaceId: (workspaceId: string) => void
  setWorkspaces: (list: Workspace[]) => void
  upsertWorkspace: (workspace: Workspace) => void
  removeWorkspace: (id: string) => void
  setPendingComposerPrefill: (value: { text: string; nonce: number } | null) => void
}

type UiStore = UiState & UiActions

const initialTheme = loadInitialTheme()
applyThemeToDOM(initialTheme)
const initialSection = loadInitialSection()
const initialWorkspaces = loadInitialWorkspaces()
const initialChatWidth = loadInitialChatWidth()

export const useUiStore = create<UiStore>((set, get) => ({
  theme: initialTheme,
  chatWidth: initialChatWidth,
  sidebarCollapsed: false,
  activeSection: initialSection,
  activeWorkspaceId: loadInitialActiveWorkspaceId(),
  workspaces: initialWorkspaces,
  pendingComposerPrefill: null,

  setTheme: (theme: Theme) => {
    applyThemeToDOM(theme)
    set({ theme })
  },

  setChatWidth: (w: ChatWidth) => {
    const snapped = snapChatWidth(w)
    persistChatWidth(snapped)
    set({ chatWidth: snapped })
  },

  toggleTheme: () => {
    const cycle: Record<Theme, Theme> = { dark: 'light', light: 'system', system: 'dark' }
    const next = cycle[get().theme]
    applyThemeToDOM(next)
    set({ theme: next })
  },

  setSidebarCollapsed: (collapsed: boolean) => set({ sidebarCollapsed: collapsed }),

  toggleSidebar: () => set((s: UiStore) => ({ sidebarCollapsed: !s.sidebarCollapsed })),

  setActiveSection: (section: AppSection) => {
    persistSection(section)
    set({ activeSection: section })
  },

  setActiveWorkspaceId: (workspaceId: string) => {
    const next = (workspaceId || '').trim() || 'default'
    persistActiveWorkspaceId(next)
    set({ activeWorkspaceId: next })
  },
  setWorkspaces: (list: Workspace[]) => {
    const normalized = normalizeWorkspaces(list)
    persistWorkspaces(normalized)
    set({ workspaces: normalized })
  },
  upsertWorkspace: (workspace: Workspace) => {
    const w = workspace as Workspace
    const id = (w?.id || '').trim()
    if (!id) return
    set((state: UiStore) => {
      const next = [...(state.workspaces || [])]
      const idx = next.findIndex((x) => x.id === id)
      const merged: Workspace = { id, name: (w.name || '').trim() || id }
      if (idx >= 0) next[idx] = merged
      else next.push(merged)
      const normalized = normalizeWorkspaces(next)
      persistWorkspaces(normalized)
      return { workspaces: normalized }
    })
  },
  removeWorkspace: (id: string) => {
    const wid = (id || '').trim()
    if (!wid || wid === 'default') return
    set((state: UiStore) => {
      const next = (state.workspaces || []).filter((w) => w.id !== wid)
      const normalized = normalizeWorkspaces(next)
      persistWorkspaces(normalized)
      const active = state.activeWorkspaceId === wid ? 'default' : state.activeWorkspaceId
      persistActiveWorkspaceId(active)
      return { workspaces: normalized, activeWorkspaceId: active }
    })
  },
  setPendingComposerPrefill: (value: { text: string; nonce: number } | null) => set({ pendingComposerPrefill: value }),
}))

// 当用户选择"跟随系统"时，监听系统主题变化并自动切换 data-theme
if (typeof window !== 'undefined') {
  const media = window.matchMedia('(prefers-color-scheme: dark)')
  media.addEventListener('change', () => {
    if (useUiStore.getState().theme === 'system') {
      applyThemeToDOM('system')
    }
  })
}
