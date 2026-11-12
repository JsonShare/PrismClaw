import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useAuth } from '../../context/AuthContext'
import { useChatSocket } from '../../hooks/chat/useChatSocket'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { useSessionLoader } from '../../hooks/chat/useSessionLoader'
import { useConversationController } from '../../hooks/chat/useConversationController'
import { useTaskPanelData } from '../../hooks/chat/useTaskPanelData'
import { useFilePreviewController } from '../../hooks/chat/useFilePreviewController'
import { useDesktopNotifier } from '../../hooks/chat/useDesktopNotifier'
import { useGlobalKeyboard, type ShortcutBinding } from '../../hooks/useGlobalKeyboard'
import {
  useChatStore,
  useSessionStore,
  useUiStore,
} from '../../stores'
import ErrorBanner from '../../components/chat/ErrorBanner'
import CommandPalette, { type PaletteSelection } from '../../components/modals/CommandPalette'
import ShortcutsModal from '../../components/modals/ShortcutsModal'
import ConfirmModal from '../../components/modals/ConfirmModal'
import { TrajectoryPanel } from '../../components/trajectory/TrajectoryPanel'
import { purgeConversation } from '../../stores'
import { fuzzyFilter } from '../../lib/fuzzy'
import { skipCodeToMessage, type SkippedFile } from '../../lib/chat/attachments'
import { normalizeWorkspacePath } from '../../lib/chat/paths'
import { apiUrl } from '../../lib/system-adapter'
import { SessionSearchModal } from '../../components/modals/SessionSearchModal'
import { WorkspaceSelectorModal } from '../../components/modals/WorkspaceSelectorModal'
import { SettingsPage } from './sections/SettingsPage'

import type { Conversation } from '../../types'

import { ChatSidebar } from './ChatSidebar'
import { ChatRightPanel } from './ChatRightPanel'
import { RightPanelResizer } from './RightPanelResizer'
import { ChatSection } from './sections/ChatSection'
import ScheduledTasksPage from './sections/ScheduledTasksPage'
import SkillsPanel from './sections/SkillsPanel'
import OutputsPage from './sections/OutputsPage'
import { DomainSection } from './sections/DomainSection'

/* ---- 三栏布局：预览栏可拖拽宽度 ---- */
/** 左侧历史栏展开宽度（对应 ChatSidebar 的 w-[240px]） */
const SIDEBAR_WIDTH = 240
/** 拖拽命中区宽度，等同咨询栏与预览栏之间的视觉间距 */
const RIGHT_RESIZER_WIDTH = 12
/** 咨询栏最小可视宽度：低于它消息流与输入框会被挤变形 */
const MAIN_COLUMN_MIN_WIDTH = 550
/** 列表态（未选文件）与预览态的最小宽度 */
const RIGHT_LIST_MIN_WIDTH = 300
const RIGHT_PREVIEW_MIN_WIDTH = 460
const RIGHT_LIST_DEFAULT_WIDTH = 320
const RIGHT_PREVIEW_DEFAULT_WIDTH = 960
const RIGHT_LIST_WIDTH_KEY = 'prismclaw.pref.right_panel_list_width'
const RIGHT_PREVIEW_WIDTH_KEY = 'prismclaw.pref.right_panel_preview_width'

function readStoredWidth(key: string, fallback: number): number {
  if (typeof window === 'undefined') return fallback
  const raw = Number(window.localStorage.getItem(key))
  return Number.isFinite(raw) && raw > 0 ? raw : fallback
}

function formatRelativeTime(ts: number): string {
  const now = Date.now()
  const diff = now - ts
  const seconds = Math.floor(diff / 1000)
  if (seconds < 60) return '刚刚'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}小时前`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days}天前`
  const months = Math.floor(days / 30)
  if (months < 12) return `${months}个月前`
  const years = Math.floor(months / 12)
  return `${years}年前`
}

export default function ChatPage() {
  const { token, userId, nickname, logout } = useAuth()
  const activeWorkspaceId = useUiStore((s) => s.activeWorkspaceId)
  const setActiveWorkspaceId = useUiStore((s) => s.setActiveWorkspaceId)
  const workspaces = useUiStore((s) => s.workspaces)
  const setWorkspaces = useUiStore((s) => s.setWorkspaces)
  const chatWidth = useUiStore((s) => s.chatWidth)
  const setChatWidth = useUiStore((s) => s.setChatWidth)
  // Sanitize stale localStorage values from the Electron era.
  // These legacy sentinel strings ('未选择文件夹' / '未命名文件夹') were never
  // valid workspace IDs and are rejected by the backend's clean_workspace_id.
  const backendWorkspaceId =
    activeWorkspaceId && !activeWorkspaceId.startsWith('未')
      ? activeWorkspaceId
      : 'default'
  // hydrateRunning 已改为用户级查询（后端内存权威源），不再需要工作区参数：
  // 刷新瞬间 activeWorkspaceId 尚未恢复，按工作区查会漏掉运行中会话。
  const { wsState, send, forceReconnect, wsDebug, subscribeNotification } = useChatSocket(
    token,
    () => logout('登录已过期，请重新登录'),
  )
  // 侧边栏按工作区分组展示任务：拉取全部工作区的会话。
  const allWorkspaceIds = useMemo(
    () => (workspaces && workspaces.length > 0 ? workspaces.map((w) => w.id) : ['default']),
    [workspaces],
  )
  const { loadMessagesFor } = useSessionLoader(token, wsState, allWorkspaceIds)
  const [commandPaletteOpen, setCommandPaletteOpen] = useState(false)
  const [shortcutsOpen, setShortcutsOpen] = useState(false)
  const [settingsPageOpen, setSettingsPageOpen] = useState(false)
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const [allTasksExpanded, setAllTasksExpanded] = useState(true)
  // 侧边栏工作区分组：标题行折叠的分组 / "查看更多"逐批展开的可见条数
  const [collapsedWsGroups, setCollapsedWsGroups] = useState<Set<string>>(new Set())
  const [wsListVisibleCount, setWsListVisibleCount] = useState<Record<string, number>>({})
  const userMenuRef = useRef<HTMLDivElement>(null)
  const userMenuButtonRef = useRef<HTMLButtonElement>(null)
  const [sessionSearchQuery, setSessionSearchQuery] = useState('')
  const sessionSearchInputRef = useRef<HTMLInputElement>(null)
  const [sessionSearchOpen, setSessionSearchOpen] = useState(false)
  const [trajectoryOpen, setTrajectoryOpen] = useState(false)
  const [sessionSearchActiveIndex, setSessionSearchActiveIndex] = useState(0)
  const [historyGroupBy, setHistoryGroupBy] = useState<'workspace' | 'time'>('workspace')
  const [historySortBy, setHistorySortBy] = useState<'updatedAt' | 'name'>('updatedAt')
  const [historyFilter, setHistoryFilter] = useState<'all' | 'manual' | 'scheduled'>('all')
  const [historySortOpen, setHistorySortOpen] = useState(false)
  const historySortRef = useRef<HTMLDivElement>(null)

  const [pendingInputPrefill, setPendingInputPrefill] = useState<{ text: string; nonce: number } | null>(null)
  // 跨页面跳转预填（如自动化页「通过对话创建」）：优先用会话页本地 prefill，
  // 否则用 UI store 里由其它页面写入的导航预填。
  const navigationPrefill = useUiStore((s) => s.pendingComposerPrefill)
  const setPendingComposerPrefill = useUiStore((s) => s.setPendingComposerPrefill)

  // 其它页面写入的导航预填：复制进会话页本地 prefill（ChatArea 已有消费逻辑），
  // 并立即清空 store 值，避免切走再切回会话页时陈旧文本被重新填入。
  useEffect(() => {
    if (!navigationPrefill) return
    setPendingInputPrefill(navigationPrefill)
    setPendingComposerPrefill(null)
  }, [navigationPrefill, setPendingInputPrefill, setPendingComposerPrefill])
  const [pendingDelete, setPendingDelete] = useState<{ id: string; title: string } | null>(null)
  const handleNewSessionRef = useRef<() => void>(() => {})
  const previousWorkspaceIdRef = useRef<string | null>(null)
  const toggleThemeFn = useUiStore((s) => s.toggleTheme)
  const updateConversations = useSessionStore((s) => s.updateConversations)
  const prependConversation = useSessionStore((s) => s.prependConversation)
  const activeConvId = useSessionStore((s) => s.activeConvId)
  const setActiveConvId = useSessionStore((s) => s.setActiveConvId)
  const sessionsLoaded = useSessionStore((s) => s.sessionsLoaded)
  const sessionsHasMore = useSessionStore((s) => s.sessionsHasMore)

  const convStates = useChatStore((s) => s.convStates)
  const unreadConvIds = useChatStore((s) => s.unreadConvIds)
  const activeConvState = activeConvId ? convStates[activeConvId] : null
  const isStreaming = activeConvState?.isStreaming ?? false
  const isQueued = activeConvState?.isQueued ?? false
  const queuePosition = activeConvState?.queuePosition ?? 0
  const liveTaskRuntime = activeConvState?.activeTaskRuntime ?? null

  // 审阅面板（右侧 aside）仅在 xl(≥1280px) 显示
  const isXl = useMediaQuery('(min-width: 1280px)')
  const clearStreamingState = (convId: string) => useChatStore.getState().finalizeStreaming(convId)

  const conversations = useSessionStore((s) => s.conversations)

  const sessionSearchBase = useMemo(() => {
    return [...conversations].sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))
  }, [conversations])

  const sessionSearchResults = useMemo(() => {
    const q = sessionSearchQuery.trim()
    if (!q) return sessionSearchBase
    return fuzzyFilter(q, sessionSearchBase, (c) => c.title || '').map((m) => m.item)
  }, [sessionSearchBase, sessionSearchQuery])

  // ── 自动化任务侧边栏 ─────────────────────────────────────────────────
  type SchedulerSidebarTask = {
    id: string
    name: string
    enabled: boolean
    workspaceId: string
    schedule: { type: string; [k: string]: any }
    runs: { id: string; fired_at: number; status: string; session_id: string | null }[]
  }
  const [schedulerTasks, setSchedulerTasks] = useState<SchedulerSidebarTask[]>([])
  // 同上：completed 回调只读任务名/工作区，用 ref 让监听保持常驻。
  const schedulerTasksRef = useRef(schedulerTasks)
  useEffect(() => { schedulerTasksRef.current = schedulerTasks }, [schedulerTasks])

  const fetchSchedulerSidebar = useCallback(async () => {
    if (!token) return
    try {
      const res = await fetch(apiUrl('/api/scheduled-tasks'), {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) return
      const tasks: any[] = await res.json()
      const withRuns = await Promise.all(
        tasks.map(async (t) => {
          let runs: any[] = []
          try {
            const rRes = await fetch(apiUrl(`/api/scheduled-tasks/${encodeURIComponent(t.id)}/runs?limit=8`), {
              headers: { Authorization: `Bearer ${token}` },
            })
            if (rRes.ok) runs = await rRes.json()
          } catch {}
          return {
            id: t.id,
            name: t.name,
            enabled: t.enabled,
            workspaceId: t.workspace_id ?? 'default',
            schedule: t.schedule,
            runs,
          }
        }),
      )
      setSchedulerTasks(withRuns)
    } catch {}
  }, [token])

  useEffect(() => { void fetchSchedulerSidebar() }, [fetchSchedulerSidebar])

  useEffect(() => {
    const handler = (e: Event) => {
      void fetchSchedulerSidebar()
      const detail = (e as CustomEvent).detail as { type?: string; data?: any } | undefined
      if (detail?.type !== 'scheduled_task.completed') return
      const sessionId = detail.data?.session_id
      if (typeof sessionId !== 'string' || !sessionId) return
      const task = schedulerTasksRef.current.find((t) => t.id === detail.data?.task_id)
      // 定时执行产生的新会话：实时插入左侧列表，不必等刷新后 ready 帧重下发。
      prependConversation({
        id: sessionId,
        title: task?.name ?? '自动化任务',
        updatedAt: Date.now(),
        active: false,
        type: 'scheduled',
        workspaceId: task?.workspaceId ?? 'default',
      })
    }
    window.addEventListener('prismclaw:scheduler-event', handler)
    return () => window.removeEventListener('prismclaw:scheduler-event', handler)
  }, [fetchSchedulerSidebar, prependConversation])

  // ── 侧边栏任务分组/排序 ──────────────────────────────────────────────
  type HistoryGroup = { id: string; label: string; items: Conversation[] }

  const groupedHistoryConversations = useMemo<HistoryGroup[]>(() => {
    // 所有任务统一展示，按筛选条件过滤
    let base = sessionSearchBase
    if (historyFilter === 'manual') {
      base = base.filter((c) => c.type !== 'scheduled')
    } else if (historyFilter === 'scheduled') {
      base = base.filter((c) => c.type === 'scheduled')
    }

    const sortItems = (items: Conversation[]) => {
      const arr = [...items]
      switch (historySortBy) {
        case 'name':
          arr.sort((a, b) => (a.title || '').localeCompare(b.title || '', 'zh-CN'))
          break
        case 'updatedAt':
        default:
          arr.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))
          break
      }
      return arr
    }

    if (historyGroupBy === 'workspace') {
      const byWs = new Map<string, Conversation[]>()
      for (const c of base) {
        const wsId = c.workspaceId || 'default'
        const arr = byWs.get(wsId)
        if (arr) arr.push(c)
        else byWs.set(wsId, [c])
      }

      const groups: HistoryGroup[] = []
      for (const [wsId, items] of byWs) {
        const ws = workspaces.find((w) => w.id === wsId)
        const label = wsId === 'default' ? '默认' : ws?.name || wsId
        groups.push({ id: wsId, label, items: sortItems(items) })
      }

      // 默认工作区永远置顶；其余按最近更新时间降序。
      groups.sort((a, b) => {
        if (a.id === 'default') return -1
        if (b.id === 'default') return 1
        return (b.items[0]?.updatedAt || 0) - (a.items[0]?.updatedAt || 0)
      })
      return groups
    }

    // 按时间分组：今天 / 昨天 / 更早
    const startOfToday = new Date().setHours(0, 0, 0, 0)
    const startOfYesterday = startOfToday - 24 * 60 * 60 * 1000
    const buckets: Record<string, Conversation[]> = { today: [], yesterday: [], earlier: [] }
    for (const c of base) {
      const ts = c.updatedAt || 0
      if (ts >= startOfToday) buckets.today.push(c)
      else if (ts >= startOfYesterday) buckets.yesterday.push(c)
      else buckets.earlier.push(c)
    }

    const groups: HistoryGroup[] = []
    if (buckets.today.length) groups.push({ id: 'today', label: '今天', items: sortItems(buckets.today) })
    if (buckets.yesterday.length) groups.push({ id: 'yesterday', label: '昨天', items: sortItems(buckets.yesterday) })
    if (buckets.earlier.length) groups.push({ id: 'earlier', label: '更早', items: sortItems(buckets.earlier) })
    return groups
  }, [sessionSearchBase, historyGroupBy, historySortBy, historyFilter, workspaces, schedulerTasks])

  // 默认只展开最近更新的一次目录任务，其余折叠；切换分组方式时重置一次。
  const hasInitializedCollapsed = useRef(false)
  const prevGroupByRef = useRef(historyGroupBy)
  useEffect(() => {
    if (groupedHistoryConversations.length === 0) return
    const shouldReset = !hasInitializedCollapsed.current || prevGroupByRef.current !== historyGroupBy
    if (!shouldReset) return
    hasInitializedCollapsed.current = true
    prevGroupByRef.current = historyGroupBy

    let mostRecentId: string | null = null
    let mostRecentTs = 0
    for (const g of groupedHistoryConversations) {
      const ts = g.items[0]?.updatedAt || 0
      if (ts > mostRecentTs) {
        mostRecentTs = ts
        mostRecentId = g.id
      }
    }
    const next = new Set<string>()
    for (const g of groupedHistoryConversations) {
      if (g.id !== mostRecentId) next.add(g.id)
    }
    setCollapsedWsGroups(next)
    // 切换分组方式时一并清空"查看更多"展开状态，避免旧分组的 id 残留误展开。
    setWsListVisibleCount({})
  }, [groupedHistoryConversations, historyGroupBy])

  useEffect(() => {
    if (!historySortOpen) return
    function onDoc(event: MouseEvent) {
      const target = event.target
      if (!(target instanceof Element)) return
      if (target.closest('[data-history-sort-root]')) return
      setHistorySortOpen(false)
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') setHistorySortOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [historySortOpen])

  const theme = useUiStore((s) => s.theme)
  const setTheme = useUiStore((s) => s.setTheme)
  const toggleSidebar = useUiStore((s) => s.toggleSidebar)
  const setSidebarCollapsed = useUiStore((s) => s.setSidebarCollapsed)
  const activeSection = useUiStore((s) => s.activeSection)
  const setActiveSection = useUiStore((s) => s.setActiveSection)
  const sidebarCollapsed = useUiStore((s) => s.sidebarCollapsed)

  // 兜底：localStorage 残留旧 'tasks'/'settings' 等已删除 section → 自动切回 chat
  useEffect(() => {
    const valid = new Set(['chat', 'experts', 'files', 'outputs'])
    if (!valid.has(activeSection) && !activeSection.startsWith('domain:')) {
      setActiveSection('chat')
    }
  }, [activeSection, setActiveSection])

  const [prefDesktopNotifications, setPrefDesktopNotifications] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false
    if (window.localStorage.getItem('prismclaw.pref.desktop_notifications') !== '1') return false
    // 仅在权限已授予时视为开启，避免开关显示开启但实际无法推送；
    // 未授权时由用户主动打开开关再走 requestPermission。
    return typeof Notification !== 'undefined' && Notification.permission === 'granted'
  })
  // 通知权限状态：granted / denied / default / unsupported / insecure
  const [notificationPermission, setNotificationPermission] = useState<'granted' | 'denied' | 'default' | 'unsupported' | 'insecure'>(() => {
    if (typeof window === 'undefined' || typeof Notification === 'undefined') return 'unsupported'
    // 非安全上下文（如通过局域网 IP 访问 http）下浏览器会直接拒绝通知权限。
    if (!window.isSecureContext) return 'insecure'
    return Notification.permission as 'granted' | 'denied' | 'default'
  })

  useEffect(() => {
    if (typeof window === 'undefined') return
    window.localStorage.setItem('prismclaw.pref.desktop_notifications', prefDesktopNotifications ? '1' : '0')
  }, [prefDesktopNotifications])

  useEffect(() => {
    if (typeof window === 'undefined' || typeof Notification === 'undefined') return
    const syncPermission = () => {
      if (!window.isSecureContext) {
        setNotificationPermission('insecure')
        setPrefDesktopNotifications(false)
        return
      }
      const perm = Notification.permission as 'granted' | 'denied' | 'default'
      setNotificationPermission(perm)
      // 权限被收回（改为拒绝/默认）时同步关闭开关，避免显示「已开启」却推送不出去。
      if (perm !== 'granted') setPrefDesktopNotifications(false)
    }
    // 浏览器不提供 permissionchange 事件，借 visibilitychange 重新读取权限状态。
    document.addEventListener('visibilitychange', syncPermission)
    return () => {
      document.removeEventListener('visibilitychange', syncPermission)
    }
  }, [])

  // 设置页的「发送测试通知」：直接构造一条通知，用于区分是应用链路还是权限/系统层拦截。
  // 通过 onshow/onerror 回传结果：浏览器若静默吞掉（如部分国产壳浏览器），会超时归为「未展示」。
  const handleTestDesktopNotification = useCallback(async (): Promise<{ ok: boolean; message: string }> => {
    if (typeof window === 'undefined' || typeof Notification === 'undefined') {
      return { ok: false, message: '当前浏览器不支持系统通知' }
    }
    if (!window.isSecureContext) {
      return { ok: false, message: '当前访问地址非安全上下文，浏览器不会展示通知（请用 localhost 或 HTTPS）' }
    }
    if (Notification.permission !== 'granted') {
      return { ok: false, message: `通知权限为 ${Notification.permission}，请先在浏览器中允许本站通知` }
    }
    try {
      const n = new Notification('PrismClaw 通知测试', {
        body: '通知已生效，任务完成时会收到提醒。',
        tag: 'prismclaw-test',
      })
      n.onclick = () => {
        window.focus()
        n.close()
      }
      return await new Promise<{ ok: boolean; message: string }>((resolve) => {
        let settled = false
        const finish = (r: { ok: boolean; message: string }) => {
          if (settled) return
          settled = true
          resolve(r)
        }
        n.onshow = () => finish({ ok: true, message: '已发出，请查看屏幕右上角横幅或通知中心' })
        n.onerror = () =>
          finish({ ok: false, message: '浏览器报告展示失败：通知被浏览器或系统拦截' })
        window.setTimeout(
          () =>
            finish({
              ok: false,
              message: '已创建通知但浏览器 2 秒内未回传展示事件：当前浏览器很可能拦截了网页通知，建议改用 Chrome / Safari 验证',
            }),
          2000,
        )
      })
    } catch (err) {
      return { ok: false, message: `创建通知失败：${err instanceof Error ? err.message : String(err)}` }
    }
  }, [])

  const showLandingHome = !activeConvId
  // 发送/队列/历史/确认/澄清/重试/中止：全部收口到会话控制器（唯一管道）。
  const controller = useConversationController({
    token,
    backendWorkspaceId,
    wsState,
    send,
    loadMessagesFor,
  })

  // ── URL 跳转接线：工作台「发起咨询」→ ?section=chat&session={id} ────────────
  // 仅在挂载时读取一次 URL 参数，切换活跃分区和会话后立即 replaceState 清参
  // （防止 session id 泄漏到浏览器历史 / Referer / 网关日志）。
  const urlNavigatedRef = useRef(false)
  useEffect(() => {
    if (urlNavigatedRef.current) return
    const params = new URLSearchParams(location.search)
    const section = params.get('section')
    const session = params.get('session')
    if (!section || !session) return
    urlNavigatedRef.current = true
    // 清参必须在读取之后、异步操作之前——即使后续 selectConversation 失败，
    // URL 也不应残留 session id。
    history.replaceState({}, '', location.pathname)
    // section 值来自工作台跳转，仅 'chat' / 'domain:*' 合法；
    // 不合法的值静默忽略（不跳转），不报错——降级为当前页面。
    const validSections = new Set(['chat', 'files', 'experts', 'outputs'])
    if (validSections.has(section) || section.startsWith('domain:')) {
      setActiveSection(section as Parameters<typeof setActiveSection>[0])
    }
    // selectConversation 需要 WS 就绪；若尚未 ready，activeConvId 已设置，
    // 后续 auto-load effect 会在 wsState 变 ready 后自动触发 ensureHistory。
    void controller.selectConversation(session)
  }, [controller, setActiveSection])

  const handleSelectSession = useCallback(
    async (convId: string) => {
      setActiveSection('chat')
      await controller.selectConversation(convId)
    },
    [controller, setActiveSection],
  )

  const openConversationFromNotification = useCallback(
    async (convId: string) => {
      // 设置页是浮层，不关闭会挡住跳转后的会话。
      setSettingsPageOpen(false)
      await handleSelectSession(convId)
    },
    [handleSelectSession],
  )

  // 桌面通知：任务完成 / 审批请求 / 澄清提问 / 异常终止统一推送，不区分页面是否可见；
  // 点击通知复用侧栏选中会话的同一路径（切回对话区、标记已读、加载历史）。
  useDesktopNotifier({
    subscribeNotification,
    enabled: prefDesktopNotifications,
    onOpenConversation: openConversationFromNotification,
  })

  useEffect(() => {
    if (wsState !== 'ready') return
    if (!activeConvId) return
    // 刷新后 activeConvId 先于会话列表恢复：列表未加载完成前不能判定「会话已不存在」，
    // 否则会把正在进行的会话清空，用户随后的澄清会新建会话，一个问题被拆成两条历史。
    if (!sessionsLoaded) return
    // 列表仍是分页的一部分（还有更早的会话未拉取）：不在当前页 ≠ 不存在，
    // 不能据此清空，否则较早的会话会被误杀。
    if (sessionsHasMore) return
    if (conversations.some((c) => c.id === activeConvId)) return
    // Scheduler sessions may not be in the paginated conversation list
    if (schedulerTasks.some((t) => t.runs.some((r) => r.session_id === activeConvId))) return
    // 本地已知存活的会话（新建/有消息/流式中）豁免：会话列表可能被 ready 帧
    // 或重新拉取整体覆盖，而服务端列表尚未收录刚创建的会话——此时置空会把
    // 用户从正在进行的会话弹回 landing。convStates 条目在删除会话时会被
    // purgeConversation 同步清除，页面重载后为空，不会误豁免陈旧 id。
    if (useChatStore.getState().convStates[activeConvId]) return
    setActiveConvId(null)
  }, [activeConvId, conversations, schedulerTasks, sessionsLoaded, sessionsHasMore, setActiveConvId, wsState])

  // activeConvId 变化（侧栏选择、scheduler 跳转、删除后回落等）→ 确保历史已加载。
  // ensureHistory 内部幂等（loaded/loading/有本地消息均不重取），无需 autoLoadedConvIdRef。
  useEffect(() => {
    if (wsState !== 'ready') return
    if (!activeConvId) return
    void controller.ensureHistory(activeConvId)
  }, [activeConvId, controller, wsState])

  const handleNewSession = useCallback(() => {
    if (activeConvId) clearStreamingState(activeConvId)
    setActiveSection('chat')
    setActiveConvId(null)
    // 保留当前选中的工作区，新会话在当前工作区下创建。
    // 若用户想在其它工作区新建，可从侧边栏对应工作区分组的"新建任务"入口进入。
  }, [activeConvId, clearStreamingState, setActiveConvId, setActiveSection])

  // 工作区标题行"新建任务"快捷入口：与 handleNewSession 一致，
  // 但工作区自动绑定为所在分组
  const handleNewSessionInWorkspace = useCallback(
    (workspaceId: string) => {
      if (activeConvId) clearStreamingState(activeConvId)
      setActiveSection('chat')
      setActiveConvId(null)
      setActiveWorkspaceId(workspaceId)
    },
    [activeConvId, clearStreamingState, setActiveConvId, setActiveSection, setActiveWorkspaceId],
  )

  const handleRenameSession = useCallback(
    async (convId: string, title: string) => {
      const previous =
        useSessionStore.getState().conversations.find((c) => c.id === convId)?.title ?? null
      updateConversations((list) =>
        list.map((c) => (c.id === convId ? { ...c, title } : c)),
      )
      try {
        const res = await fetch(apiUrl(`/api/sessions/${encodeURIComponent(convId)}`), {
          method: 'PATCH',
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ title }),
        })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
      } catch {
        updateConversations((list) =>
          list.map((c) => (c.id === convId ? { ...c, title: previous ?? c.title } : c)),
        )
      }
    },
    [token, updateConversations],
  )

  const handleDeleteRequest = useCallback(
    (convId: string) => {
      const conv = useSessionStore.getState().conversations.find((c) => c.id === convId)
      setPendingDelete({ id: convId, title: conv?.title || '该会话' })
    },
    [],
  )

  const handleDeleteConfirm = useCallback(async () => {
    if (!pendingDelete) return
    const convId = pendingDelete.id
    setPendingDelete(null)
    const wasActive = useSessionStore.getState().activeConvId === convId
    try {
      const res = await fetch(apiUrl(`/api/sessions/${encodeURIComponent(convId)}`), {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status}`)
    } catch {
      return
    }
    const nextActive = purgeConversation(convId)
    if (wasActive) {
      setActiveConvId(nextActive)
      if (nextActive) {
        await loadMessagesFor(nextActive)
      } else {
        if (convId) clearStreamingState(convId)
      }
    }
  }, [clearStreamingState, loadMessagesFor, pendingDelete, setActiveConvId, token])

  useEffect(() => {
    handleNewSessionRef.current = handleNewSession
  }, [handleNewSession])

  useEffect(() => {
    if (!sessionSearchOpen) return
    setSessionSearchActiveIndex(0)
    requestAnimationFrame(() => sessionSearchInputRef.current?.focus())
  }, [sessionSearchOpen])

  useEffect(() => {
    if (!sessionSearchOpen) return
    setSessionSearchActiveIndex(0)
  }, [sessionSearchOpen, sessionSearchQuery])

  useEffect(() => {
    if (!sessionSearchOpen) return
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault()
        setSessionSearchOpen(false)
        setSessionSearchQuery('')
        return
      }
      if (event.key === 'ArrowDown') {
        event.preventDefault()
        setSessionSearchActiveIndex((i) =>
          Math.min(i + 1, Math.max(0, sessionSearchResults.length - 1)),
        )
        return
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault()
        setSessionSearchActiveIndex((i) => Math.max(0, i - 1))
        return
      }
      if (event.key === 'Enter') {
        const picked = sessionSearchResults[sessionSearchActiveIndex]
        if (!picked) return
        event.preventDefault()
        setSessionSearchOpen(false)
        setSessionSearchQuery('')
        setActiveSection('chat')
        void handleSelectSession(picked.id)
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [handleSelectSession, sessionSearchActiveIndex, sessionSearchOpen, sessionSearchResults, setActiveSection])

  const handlePaletteSelect = useCallback(
    (selection: PaletteSelection) => {
      if (selection.kind === 'session') {
        void handleSelectSession(selection.sessionId)
        return
      }
      switch (selection.action.kind) {
        case 'new-session':
          handleNewSessionRef.current()
          break
        case 'toggle-theme':
          toggleThemeFn()
          break
        case 'toggle-sidebar':
          toggleSidebar()
          break
        case 'show-shortcuts':
          setShortcutsOpen(true)
          break
      }
    },
    [handleSelectSession, toggleSidebar, toggleThemeFn],
  )

  const shortcutBindings = useMemo<ShortcutBinding[]>(
    () => [
      {
        modifier: 'mod',
        key: 'k',
        handler: () => {
          setShortcutsOpen(false)
          setCommandPaletteOpen(true)
        },
      },
      {
        modifier: 'mod',
        key: 'n',
        handler: () => handleNewSessionRef.current(),
      },
      {
        modifier: 'mod',
        key: '\\',
        handler: () => toggleSidebar(),
      },
      {
        modifier: 'mod',
        key: '/',
        handler: () => {
          setCommandPaletteOpen(false)
          setShortcutsOpen(true)
        },
      },
    ],
    [toggleSidebar],
  )

  useGlobalKeyboard(shortcutBindings)

  const [workspaceSelectOpen, setWorkspaceSelectOpen] = useState(false)
  const [workspacesLoading, setWorkspacesLoading] = useState(false)
  const [workspacesError, setWorkspacesError] = useState<string | null>(null)

  // 文件预览 + 审阅：状态与动作全部在控制器内（含 taskFilesOpen 面板开关）。
  const filePreview = useFilePreviewController({ token, backendWorkspaceId })
  // 解构给下方窗口缩放自折叠 effect 用（读取/回写面板开关）。
  const { taskFilesOpen, setTaskFilesOpen } = filePreview

  /* ---- 预览栏可拖拽宽度：列表态 / 预览态各自记忆一份 ---- */
  const [rightListWidth, setRightListWidth] = useState<number>(() =>
    readStoredWidth(RIGHT_LIST_WIDTH_KEY, RIGHT_LIST_DEFAULT_WIDTH),
  )
  const [rightPreviewWidth, setRightPreviewWidth] = useState<number>(() =>
    readStoredWidth(RIGHT_PREVIEW_WIDTH_KEY, RIGHT_PREVIEW_DEFAULT_WIDTH),
  )
  // 三栏父容器的实测宽度：窗口缩放时靠它把预览栏重新夹回可视范围
  const layoutRef = useRef<HTMLDivElement>(null)
  const [layoutWidth, setLayoutWidth] = useState<number>(() =>
    typeof window === 'undefined' ? 0 : window.innerWidth,
  )
  useEffect(() => {
    const el = layoutRef.current
    if (el === null || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver((entries) => {
      const next = entries[0]?.contentRect.width
      if (typeof next === 'number' && next > 0) setLayoutWidth(next)
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  const hasFilePreview = Boolean(filePreview.selectedFile)
  const rightPanelMinWidth = hasFilePreview ? RIGHT_PREVIEW_MIN_WIDTH : RIGHT_LIST_MIN_WIDTH
  // 上限 = 容器宽 − 侧栏 − 把手 − 咨询栏最小宽；再宽就会把咨询栏挤变形
  const rightPanelMaxWidth = Math.max(
    rightPanelMinWidth,
    layoutWidth - (sidebarCollapsed ? 0 : SIDEBAR_WIDTH) - RIGHT_RESIZER_WIDTH - MAIN_COLUMN_MIN_WIDTH,
  )
  const rightPanelRawWidth = hasFilePreview ? rightPreviewWidth : rightListWidth
  const rightPanelWidth = Math.round(
    Math.max(rightPanelMinWidth, Math.min(rightPanelRawWidth, rightPanelMaxWidth)),
  )
  const commitRightPanelWidth = useCallback(
    (next: number) => {
      if (hasFilePreview) setRightPreviewWidth(next)
      else setRightListWidth(next)
    },
    [hasFilePreview],
  )
  const resetRightPanelWidth = useCallback(() => {
    commitRightPanelWidth(hasFilePreview ? RIGHT_PREVIEW_DEFAULT_WIDTH : RIGHT_LIST_DEFAULT_WIDTH)
  }, [hasFilePreview, commitRightPanelWidth])
  // 宽度通过 CSS 变量下发给预览栏：拖动时由 ResizeHandle 命令式覆写，
  // 这里只负责在 state 变化（松手提交 / 窗口夹取 / 切换模式）后同步一次，
  // 避免 React 持有 style 反而在拖动中途把值冲掉。
  useEffect(() => {
    layoutRef.current?.style.setProperty('--right-panel-w', `${rightPanelWidth}px`)
  }, [rightPanelWidth])
  // 拖动中只改 CSS 变量（不落 state），松手才写 localStorage
  useEffect(() => {
    if (typeof window === 'undefined') return
    window.localStorage.setItem(RIGHT_LIST_WIDTH_KEY, String(rightListWidth))
  }, [rightListWidth])
  useEffect(() => {
    if (typeof window === 'undefined') return
    window.localStorage.setItem(RIGHT_PREVIEW_WIDTH_KEY, String(rightPreviewWidth))
  }, [rightPreviewWidth])

  const [rightProcessOpen, setRightProcessOpen] = useState(true)
  const [rightFilesOpen, setRightFilesOpen] = useState(true)
  const [rightCallsOpen, setRightCallsOpen] = useState(true)
  const [rightTrajectoryOpen, setRightTrajectoryOpen] = useState(true)
  const autoSidebarCollapsedRef = useRef(false)
  const autoTaskFilesClosedRef = useRef(false)
  const prevSidebarCollapsedRef = useRef(sidebarCollapsed)
  const prevTaskFilesOpenRef = useRef(taskFilesOpen)
  useEffect(() => {
    if (!autoSidebarCollapsedRef.current) {
      prevSidebarCollapsedRef.current = sidebarCollapsed
    }
  }, [sidebarCollapsed])

  useEffect(() => {
    if (!autoTaskFilesClosedRef.current) {
      prevTaskFilesOpenRef.current = taskFilesOpen
    }
  }, [taskFilesOpen])

  useEffect(() => {
    const CHAT_MIN_W = 550
    const LEFT_W = 240
    const RIGHT_W = 340
    const OUTER_W = 24

    const evaluate = () => {
      const w = typeof window !== 'undefined' ? window.innerWidth : 0
      const needLeft = CHAT_MIN_W + LEFT_W + OUTER_W
      const needBoth = CHAT_MIN_W + LEFT_W + RIGHT_W + OUTER_W

      if (w < needBoth) {
        if (taskFilesOpen) {
          autoTaskFilesClosedRef.current = true
          setTaskFilesOpen(false)
        }
      } else if (autoTaskFilesClosedRef.current) {
        autoTaskFilesClosedRef.current = false
        setTaskFilesOpen(prevTaskFilesOpenRef.current)
      }

      if (w < needLeft) {
        if (!sidebarCollapsed) {
          autoSidebarCollapsedRef.current = true
          setSidebarCollapsed(true)
        }
      } else if (autoSidebarCollapsedRef.current) {
        autoSidebarCollapsedRef.current = false
        setSidebarCollapsed(prevSidebarCollapsedRef.current)
      }
    }

    evaluate()
    window.addEventListener('resize', evaluate)
    return () => window.removeEventListener('resize', evaluate)
  }, [setSidebarCollapsed, sidebarCollapsed, taskFilesOpen])

  useEffect(() => {
    if (!userMenuOpen) return
    const onMouseDown = (e: MouseEvent) => {
      const target = e.target as Node | null
      if (!target) return
      if (userMenuRef.current?.contains(target)) return
      if (userMenuButtonRef.current?.contains(target)) return
      setUserMenuOpen(false)
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setUserMenuOpen(false)
    }
    window.addEventListener('mousedown', onMouseDown)
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('mousedown', onMouseDown)
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [userMenuOpen])

  const normalizeBackendError = useCallback((raw: string) => {
    const text = (raw || '').trim()
    if (!text) return '操作失败'
    try {
      const parsed = JSON.parse(text) as unknown
      if (parsed && typeof parsed === 'object') {
        const detail = (parsed as { detail?: unknown }).detail
        if (typeof detail === 'string' && detail.trim()) return detail.trim()
      }
    } catch {}
    return text
  }, [])

  const loadWorkspaces = useCallback(async (): Promise<void> => {
    if (!token) return
    setWorkspacesLoading(true)
    setWorkspacesError(null)
    try {
      const res = await fetch(apiUrl('/api/workspaces'), {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (res.status === 401) {
        logout()
        throw new Error('登录已过期，请重新登录')
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = (await res.json()) as { workspaces?: Array<{ id?: unknown; name?: unknown }> }
      const list = Array.isArray(data.workspaces)
        ? data.workspaces
            .map((w) => ({
              id: typeof w?.id === 'string' ? w.id : '',
              name: typeof w?.name === 'string' ? w.name : '',
            }))
            .filter((w) => w.id)
        : []
      setWorkspaces(list.length > 0 ? list : [{ id: 'default', name: '默认工作区' }])
    } catch (err) {
      const msg = err instanceof Error ? err.message : '加载失败'
      setWorkspacesError(normalizeBackendError(String(msg)))
    } finally {
      setWorkspacesLoading(false)
    }
  }, [logout, normalizeBackendError, setWorkspaces, token])

  const handleCreateServerWorkspace = useCallback(async (name: string): Promise<void> => {
    if (!token) throw new Error('未登录')
    const trimName = name.trim()
    if (!trimName) throw new Error('请输入工作区名称')
    const baseId = trimName
      .replace(/[^a-zA-Z0-9\u4e00-\u9fa5_-]+/g, '-')
      .replace(/-+/g, '-')
      .replace(/^-+|-+$/g, '')

    let attempt = 0
    const maxAttempts = 3
    while (attempt < maxAttempts) {
      const suffix = attempt === 0 ? '' : `-${Date.now().toString(36).slice(-4)}`
      const id = (baseId || `ws`).slice(0, 64 - suffix.length) + suffix
      if (!id) throw new Error('无法生成工作区 ID')

      const res = await fetch(apiUrl('/api/workspaces'), {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ id, name: trimName }),
      })
      if (res.status === 401) {
        logout()
        throw new Error('登录已过期，请重新登录')
      }
      if (res.status === 409) {
        attempt += 1
        if (attempt >= maxAttempts) {
          throw new Error('工作区 ID 冲突，请更换名称重试')
        }
        continue
      }
      if (!res.ok) {
        const text = await res.text().catch(() => '')
        throw new Error(normalizeBackendError(text || `HTTP ${res.status}`))
      }
      await loadWorkspaces()
      setActiveWorkspaceId(id)
      return
    }
  }, [loadWorkspaces, logout, normalizeBackendError, setActiveWorkspaceId, token])

  const handleUploadServerFiles = useCallback(async (files: File[]): Promise<void> => {
    if (!token) throw new Error('未登录')
    if (files.length === 0) return
    const form = new FormData()
    for (const f of files) {
      const rel = (f as any).relativePath || (f as any).webkitRelativePath || f.name
      form.append('files', f, rel)
    }
    const params = new URLSearchParams()
    params.set('workspace_id', activeWorkspaceId || 'default')
    if (activeConvId) params.set('session_id', activeConvId)
    const res = await fetch(
      apiUrl(`/api/files/upload?${params.toString()}`),
      {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: form,
      },
    )
    if (res.status === 401) {
      logout()
      throw new Error('登录已过期，请重新登录')
    }
    if (!res.ok) {
      const text = await res.text().catch(() => '')
      throw new Error(normalizeBackendError(text || `HTTP ${res.status}`))
    }
    const data = (await res.json()) as { files: unknown[]; skipped: SkippedFile[]; session_id?: string }
    // 后端自动创建 session_id 时（无 activeConvId 的新对话场景），同步到 store
    if (!activeConvId && data.session_id) {
      useSessionStore.getState().setActiveConvId(data.session_id)
    }
    if (data.skipped && data.skipped.length > 0) {
      throw new Error(data.skipped.map((s) => skipCodeToMessage(s.code, s.name)).join('；'))
    }
  }, [activeConvId, activeWorkspaceId, logout, normalizeBackendError, token])

  const handleRemoveServerWorkspace = useCallback(async (id: string): Promise<void> => {
    if (!token) throw new Error('未登录')
    if (id === 'default') throw new Error('默认工作区不能删除')
    const res = await fetch(apiUrl(`/api/workspaces/${encodeURIComponent(id)}`), {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${token}` },
    })
    if (res.status === 401) {
      logout()
      throw new Error('登录已过期，请重新登录')
    }
    if (!res.ok) {
      const text = await res.text().catch(() => '')
      throw new Error(normalizeBackendError(text || `HTTP ${res.status}`))
    }
    await loadWorkspaces()
    if (activeWorkspaceId === id) {
      setActiveWorkspaceId('default')
    }
  }, [activeWorkspaceId, loadWorkspaces, logout, normalizeBackendError, setActiveWorkspaceId, token])

  useEffect(() => {
    if (!token) return
    void loadWorkspaces()
  }, [token, loadWorkspaces])

  useEffect(() => {
    const previous = previousWorkspaceIdRef.current
    previousWorkspaceIdRef.current = backendWorkspaceId
    if (previous === null || previous === backendWorkspaceId) return
    // historyStatus 已下沉 store 且按会话维度独立，切换工作区无需重置任何加载标记。
    // 若当前选中的会话属于新工作区（跨工作区点击任务），保留选中；否则清空。
    const currentConvId = useSessionStore.getState().activeConvId
    const conv = useSessionStore.getState().conversations.find((c) => c.id === currentConvId)
    if (!conv || (conv.workspaceId || 'default') !== backendWorkspaceId) {
      setActiveConvId(null)
    }
  }, [backendWorkspaceId, setActiveConvId])

  useEffect(() => {
    // 仅在工作区列表已加载完成时做校验，避免列表还在拉取就把
    //  localStorage 恢复的 activeWorkspaceId 强制回落到 default。
    if (workspaces.length === 0 || workspacesLoading) return
    const found = workspaces.find((w) => w.id === activeWorkspaceId)
    if (found) return
    console.warn(
      '[ChatPage] activeWorkspaceId 不在 workspaces 列表中，强制回落 default。',
      { activeWorkspaceId, available: workspaces.map((w) => w.id) },
    )
    setActiveWorkspaceId('default')
  }, [activeWorkspaceId, setActiveWorkspaceId, workspaces, workspacesLoading])

  // 右侧面板派生数据：全部从 store 直订计算（useTaskPanelData），本组件不感知派生细节。
  const panelData = useTaskPanelData()

  return (
    <div className="h-screen bg-[var(--c-canvas)] text-[var(--c-text)] p-3">
      <div className="h-full w-full rounded-[var(--radius-card)] overflow-hidden flex flex-col">
      <div
        ref={layoutRef}
        className="flex flex-1 overflow-hidden relative bg-[var(--c-canvas)]"
      >
        <ErrorBanner
          visible={wsState === 'disconnected' && Boolean(wsDebug)}
          onReconnect={forceReconnect}
          details={wsDebug}
        />

        <ChatSidebar
          sidebarCollapsed={sidebarCollapsed}
          activeSection={activeSection}
          activeConvId={activeConvId}
          onNavigate={setActiveSection}
          onNewSession={handleNewSession}
          allTasksExpanded={allTasksExpanded}
          setAllTasksExpanded={setAllTasksExpanded}
          sessionSearchOpen={sessionSearchOpen}
          setSessionSearchOpen={setSessionSearchOpen}
          setSessionSearchQuery={setSessionSearchQuery}
          historySortOpen={historySortOpen}
          setHistorySortOpen={setHistorySortOpen}
          historySortRef={historySortRef}
          historyGroupBy={historyGroupBy}
          setHistoryGroupBy={setHistoryGroupBy}
          historySortBy={historySortBy}
          setHistorySortBy={setHistorySortBy}
          historyFilter={historyFilter}
          setHistoryFilter={setHistoryFilter}
          groupedHistoryConversations={groupedHistoryConversations}
          collapsedWsGroups={collapsedWsGroups}
          setCollapsedWsGroups={setCollapsedWsGroups}
          wsListVisibleCount={wsListVisibleCount}
          setWsListVisibleCount={setWsListVisibleCount}
          onNewSessionInWorkspace={handleNewSessionInWorkspace}
          onRenameSession={handleRenameSession}
          onDeleteSession={handleDeleteRequest}
          convStates={convStates}
          unreadConvIds={unreadConvIds}
          backendWorkspaceId={backendWorkspaceId}
          setActiveWorkspaceId={setActiveWorkspaceId}
          setActiveSection={setActiveSection}
          onSelectSession={handleSelectSession}
          userMenuOpen={userMenuOpen}
          setUserMenuOpen={setUserMenuOpen}
          userMenuRef={userMenuRef}
          userMenuButtonRef={userMenuButtonRef}
          setSettingsPageOpen={setSettingsPageOpen}
          theme={theme}
          setTheme={setTheme}
          logout={logout}
          displayName={nickname || userId}
          formatRelativeTime={formatRelativeTime}
        />

        {activeSection === 'chat' && (
          <>
            <ChatSection
              key={activeConvId ?? 'landing'}
              sidebarCollapsed={sidebarCollapsed}
              onToggleSidebar={toggleSidebar}
              onNewSession={handleNewSession}
              title={conversations.find((c) => c.id === activeConvId)?.title ?? ''}
              isLanding={showLandingHome}
              showPanelButton={!showLandingHome}
              taskFilesOpen={taskFilesOpen}
              onTogglePanel={() => setTaskFilesOpen((v: boolean) => !v)}
              token={token ?? ''}
              workspaceId={backendWorkspaceId}
              layout={showLandingHome ? 'landing' : 'default'}
              workspaceUploadDir=""
              workspaces={workspaces}
              onSelectWorkspace={(id: string) => setActiveWorkspaceId(id)}
              onCreateServerWorkspace={handleCreateServerWorkspace}
              onUploadServerFiles={handleUploadServerFiles}
              onRemoveServerWorkspace={handleRemoveServerWorkspace}
              isWorkspacesLoading={workspacesLoading}
              onOpenReviewInPanel={isXl ? filePreview.openReviewInPanel : undefined}
              conversationId={activeConvId ?? ''}
              controller={controller}
              prefillInput={pendingInputPrefill ?? navigationPrefill}
              onOpenFilePreview={filePreview.openFileFromOutputList}
              showLandingHome={showLandingHome}
            />
            {/* 拖拽分隔条：仅在预览栏展开且非落地页时出现 */}
            {taskFilesOpen && !showLandingHome && (
              <RightPanelResizer
                width={rightPanelWidth}
                min={rightPanelMinWidth}
                max={rightPanelMaxWidth}
                onDrag={(next) => layoutRef.current?.style.setProperty('--right-panel-w', `${next}px`)}
                onCommit={commitRightPanelWidth}
                onReset={resetRightPanelWidth}
              />
            )}
            <ChatRightPanel
              showLandingHome={showLandingHome}
              taskFilesOpen={taskFilesOpen}
              width={rightPanelWidth}
              selectedFile={filePreview.selectedFile}
              previewBlob={filePreview.previewBlob}
              reviewingChange={filePreview.reviewingChange}
              reviewHtml={filePreview.reviewHtml}
              reviewHtmlError={filePreview.reviewHtmlError}
              selectedFileError={filePreview.selectedFileError}
              activeConvId={activeConvId}
              token={token}
              rightProcessOpen={rightProcessOpen}
              setRightProcessOpen={setRightProcessOpen}
              rightFilesOpen={rightFilesOpen}
              setRightFilesOpen={setRightFilesOpen}
              rightCallsOpen={rightCallsOpen}
              setRightCallsOpen={setRightCallsOpen}
              rightTrajectoryOpen={rightTrajectoryOpen}
              setRightTrajectoryOpen={setRightTrajectoryOpen}
              subagentsForPanel={panelData.subagentsForPanel}
              runningToolCallsForPanel={panelData.runningToolCallsForPanel}
              isStreaming={isStreaming}
              isQueued={isQueued}
              liveTaskRuntime={liveTaskRuntime}
              taskRuntimeSummary={panelData.taskRuntimeSummary}
              queuePosition={queuePosition}
              formatToolProgressText={panelData.formatToolProgressText}
              panelTaskOutputFiles={panelData.panelTaskOutputFiles}
              normalizeWorkspacePath={normalizeWorkspacePath}
              openFileFromOutputList={filePreview.openFileFromOutputList}
              downloadSingleFile={filePreview.downloadSingleFile}
              toolNamesForPanel={panelData.toolNamesForPanel}
              skillsForPanel={panelData.skillsForPanel}
              setTrajectoryOpen={setTrajectoryOpen}
              closePreviewModal={filePreview.closePreviewModal}
              reviewHandlers={filePreview.reviewHandlers}
            />
          </>
        )}

        {activeSection === 'files' && (
          <ScheduledTasksPage
            token={token}
            onSelectSession={handleSelectSession as (convId: string) => Promise<void>}
            onLogout={logout}
          />
        )}

        {activeSection === 'experts' && <SkillsPanel />}

        {activeSection === 'outputs' && (
          <OutputsPage token={token} onSelectSession={handleSelectSession} />
        )}

        {activeSection.startsWith('domain:') && (
          <DomainSection domainKey={activeSection.slice('domain:'.length)} />
        )}
      </div>
      </div>

      <CommandPalette
        open={commandPaletteOpen}
        conversations={conversations}
        theme={theme}
        onClose={() => setCommandPaletteOpen(false)}
        onSelect={handlePaletteSelect}
      />

      <ShortcutsModal open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />

      {/* 运行轨迹 Modal: 弹出审计面板, 不占用咨询区 */}
      <TrajectoryPanel
        open={trajectoryOpen}
        onClose={() => setTrajectoryOpen(false)}
        sessionId={activeConvId ?? null}
        token={token}
        onUnauthorized={logout}
      />

      <ConfirmModal
        open={pendingDelete !== null}
        title="删除会话？"
        message={`将永久删除"${pendingDelete?.title ?? ''}"及其消息，且无法撤销。`}
        confirmLabel="删除"
        destructive
        onConfirm={handleDeleteConfirm}
        onCancel={() => setPendingDelete(null)}
      />

      <SessionSearchModal
        open={sessionSearchOpen}
        onClose={() => {
          setSessionSearchOpen(false)
          setSessionSearchQuery('')
        }}
        query={sessionSearchQuery}
        onQueryChange={setSessionSearchQuery}
        results={sessionSearchResults}
        activeIndex={sessionSearchActiveIndex}
        onActiveIndexChange={setSessionSearchActiveIndex}
        onSelect={(id) => {
          setSessionSearchOpen(false)
          setSessionSearchQuery('')
          setActiveSection('chat')
          void handleSelectSession(id)
        }}
        totalCount={sessionSearchBase.length}
      />

      <WorkspaceSelectorModal
        open={workspaceSelectOpen}
        onClose={() => setWorkspaceSelectOpen(false)}
        workspaces={workspaces}
        activeWorkspaceId={activeWorkspaceId}
        onSelectWorkspace={(id) => {
          setActiveWorkspaceId(id)
          setWorkspaceSelectOpen(false)
        }}
        onRefresh={() => void loadWorkspaces()}
        busy={false}
        loading={workspacesLoading}
        error={workspacesError}
      />

      <SettingsPage
        open={settingsPageOpen}
        onClose={() => setSettingsPageOpen(false)}
        displayName={nickname || userId || null}
        onLogout={logout}
        desktopNotifications={prefDesktopNotifications}
        onToggleDesktopNotifications={() => {
          const next = !prefDesktopNotifications
          if (!next) {
            setPrefDesktopNotifications(false)
            return
          }
          if (typeof window === 'undefined') {
            setPrefDesktopNotifications(true)
            return
          }
          if (!window.isSecureContext) {
            setPrefDesktopNotifications(false)
            return
          }
          const anyWin = window as any
          const Notif = anyWin.Notification as typeof Notification | undefined
          if (!Notif) {
            setPrefDesktopNotifications(true)
            return
          }
          if (Notif.permission === 'granted') {
            setPrefDesktopNotifications(true)
            return
          }
          if (Notif.permission === 'denied') {
            setPrefDesktopNotifications(false)
            return
          }
          Notif.requestPermission()
            .then((perm: NotificationPermission) => {
              const granted = perm === 'granted'
              setPrefDesktopNotifications(granted)
              setNotificationPermission(perm as 'granted' | 'denied' | 'default')
            })
            .catch(() => setPrefDesktopNotifications(false))
        }}
        notificationPermission={notificationPermission}
        onTestDesktopNotification={handleTestDesktopNotification}
        theme={theme}
        onChangeTheme={setTheme}
        chatWidth={chatWidth}
        onChangeChatWidth={setChatWidth}
      />

    </div>
  )
}
