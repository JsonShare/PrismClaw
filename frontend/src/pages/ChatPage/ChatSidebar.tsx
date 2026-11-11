import {
  ChevronDown,
  ChevronUp,
  Search,
  ListFilter,
  Plus,
  Check,
  Settings,
  LogOut,
  Monitor,
  Loader2,
  MoreHorizontal,
  Pencil,
  Trash2,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import ThemeToggle from '../../components/ui/ThemeToggle'
import { HairlineDivider } from '../../components/ui/HairlineDivider'
import { SidebarNav } from '../../components/sidebar/SidebarNav'
import type { Conversation, Theme } from '../../types'
import type { AppSection } from '../../stores/ui'
import iconHistory from '../../images/other_icon/history.png'
import iconFolder from '../../images/other_icon/folder.png'
import iconClock from '../../images/other_icon/clock.png'

export type HistoryGroup = { id: string; label: string; items: Conversation[] }

interface ChatSidebarProps {
  sidebarCollapsed: boolean
  activeSection: AppSection
  activeConvId: string | null
  onNavigate: (section: AppSection) => void
  onNewSession: () => void
  allTasksExpanded: boolean
  setAllTasksExpanded: (v: boolean | ((prev: boolean) => boolean)) => void
  sessionSearchOpen: boolean
  setSessionSearchOpen: (v: boolean) => void
  setSessionSearchQuery: (q: string) => void
  historySortOpen: boolean
  setHistorySortOpen: (v: boolean | ((prev: boolean) => boolean)) => void
  historySortRef: React.RefObject<HTMLDivElement | null>
  historyGroupBy: 'workspace' | 'time'
  setHistoryGroupBy: (v: 'workspace' | 'time') => void
  historySortBy: 'updatedAt' | 'name'
  setHistorySortBy: (v: 'updatedAt' | 'name') => void
  historyFilter: 'all' | 'manual' | 'scheduled'
  setHistoryFilter: (v: 'all' | 'manual' | 'scheduled') => void
  groupedHistoryConversations: HistoryGroup[]
  collapsedWsGroups: Set<string>
  setCollapsedWsGroups: (v: Set<string> | ((prev: Set<string>) => Set<string>)) => void
  /** 每个工作区分组当前显示的条数，默认 3 */
  wsListVisibleCount: Record<string, number>
  setWsListVisibleCount: (v: Record<string, number> | ((prev: Record<string, number>) => Record<string, number>)) => void
  onNewSessionInWorkspace: (workspaceId: string) => void
  convStates: Record<string, any>
  unreadConvIds: Set<string>
  backendWorkspaceId: string
  setActiveWorkspaceId: (id: string) => void
  setActiveSection: (section: AppSection) => void
  onSelectSession: (id: string) => void | Promise<void>
  userMenuOpen: boolean
  setUserMenuOpen: (v: boolean | ((prev: boolean) => boolean)) => void
  userMenuRef: React.RefObject<HTMLDivElement | null>
  userMenuButtonRef: React.RefObject<HTMLButtonElement | null>
  setSettingsPageOpen: (v: boolean) => void
  theme: Theme
  setTheme: (theme: Theme) => void
  logout: (reason?: string) => void
  displayName: string | null
  formatRelativeTime: (ts: number) => string
  /** 会话项 ⋯ 菜单：重命名（乐观更新+PATCH 回滚，实现在 ChatPage） */
  onRenameSession: (convId: string, title: string) => void
  /** 会话项 ⋯ 菜单：删除（走 pendingDelete → ConfirmModal 确认流） */
  onDeleteSession: (convId: string) => void
}

export function ChatSidebar(props: ChatSidebarProps) {
  // 会话项 ⋯ 操作菜单：同一时刻至多一个打开；外点 / Escape 关闭。
  const [menuConvId, setMenuConvId] = useState<string | null>(null)
  const sessionMenuRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (menuConvId === null) return
    function onDoc(event: MouseEvent) {
      const target = event.target
      if (!(target instanceof Element)) return
      if (sessionMenuRef.current && !sessionMenuRef.current.contains(target)) {
        // ⋯ 按钮自身的 mousedown 不关（随后的 click 负责开合切换）
        if (target.closest('[data-session-menu-btn]')) return
        setMenuConvId(null)
      }
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') setMenuConvId(null)
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [menuConvId])

  return (
    <aside className={`shrink-0 app-sidebar app-sidebar flex flex-col h-full transition-[width] duration-300 ${props.sidebarCollapsed ? 'w-0 overflow-hidden' : 'w-[240px]'}`}>
      <div className="px-4 flex items-center shrink-0 h-6" />

      <SidebarNav
        activeSection={props.activeSection}
        activeConvId={props.activeConvId}
        onNavigate={props.onNavigate}
        onNewSession={props.onNewSession}
      />

      {/* 主菜单与「所有任务」列表的分隔（与全站统一的渐隐发丝线） */}
      <HairlineDivider className="mx-3" />

      <div className="flex items-center justify-between pl-6 pr-3 pt-3 shrink-0 relative" style={{ zIndex: 2 }}>
          <button
            type="button"
            onClick={() => props.setAllTasksExpanded((v) => !v)}
            className="flex items-center gap-1.5 p-0 text-[length:var(--fs-sm)] font-semibold text-[var(--c-text-secondary)] transition-colors cursor-pointer"
            aria-expanded={props.allTasksExpanded}
          >
            所有任务 <ChevronDown size={12} className={`text-[var(--c-text-secondary)] transition-transform ${props.allTasksExpanded ? '' : '-rotate-90'}`} />
          </button>
          <div className="flex items-center gap-0.5">
            <button
              type="button"
              onClick={() => {
                props.setSessionSearchOpen(true)
                props.setSessionSearchQuery('')
              }}
              className="h-8 w-8 rounded-[var(--radius-control)] hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer grid place-items-center"
              title="搜索任务"
              aria-label="搜索任务"
            >
              <Search size={16} className="text-[var(--c-text-secondary)]" />
            </button>
            <div className="relative" data-history-sort-root ref={props.historySortRef}>
              <button
                type="button"
                onClick={() => props.setHistorySortOpen((v) => !v)}
                className={`h-8 w-8 rounded-[var(--radius-control)] hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer grid place-items-center ${props.historySortOpen ? 'bg-[var(--c-bg)]' : ''}`}
                title="分组与排序"
                aria-label="分组与排序"
                aria-expanded={props.historySortOpen}
              >
                <ListFilter size={16} className="text-[var(--c-text-secondary)]" />
              </button>
              {props.historySortOpen && (
                <div className="overlay-panel absolute right-0 top-full mt-1 z-50 min-w-[168px] rounded-[var(--radius-card)] overflow-hidden py-2">
                  <div className="px-3 pb-1.5">
                    <div className="text-[length:var(--fs-sm)] text-[var(--c-text-tertiary)] mb-1">分组方式</div>
                    <div className="space-y-0.5">
                      {[
                        { key: 'workspace', label: '按工作目录' },
                        { key: 'time', label: '按时间' },
                      ].map((opt) => (
                        <button
                          key={opt.key}
                          type="button"
                          onClick={() => props.setHistoryGroupBy(opt.key as 'workspace' | 'time')}
                          className="w-full flex items-center justify-between rounded-[var(--radius-control)] px-2 py-1.5 text-[length:var(--fs-sm)] text-[var(--c-text)] hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer"
                        >
                          <span>{opt.label}</span>
                          {props.historyGroupBy === opt.key && <Check size={14} className="text-[var(--c-accent)]" />}
                        </button>
                      ))}
                    </div>
                  </div>
                  <HairlineDivider className="mx-3 my-1.5" />
                  <div className="px-3 pt-0.5">
                    <div className="text-[length:var(--fs-sm)] text-[var(--c-text-tertiary)] mb-1">排序方式</div>
                    <div className="space-y-0.5">
                      {[
                        { key: 'updatedAt', label: '最近更新' },
                        { key: 'name', label: '名称' },
                      ].map((opt) => (
                        <button
                          key={opt.key}
                          type="button"
                          onClick={() => props.setHistorySortBy(opt.key as 'updatedAt' | 'name')}
                          className="w-full flex items-center justify-between rounded-[var(--radius-control)] px-2 py-1.5 text-[length:var(--fs-sm)] text-[var(--c-text)] hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer"
                        >
                          <span>{opt.label}</span>
                          {props.historySortBy === opt.key && <Check size={14} className="text-[var(--c-accent)]" />}
                        </button>
                      ))}
                    </div>
                  </div>
                  <HairlineDivider className="mx-3 my-1.5" />
                  <div className="px-3 pt-0.5">
                    <div className="text-[length:var(--fs-sm)] text-[var(--c-text-tertiary)] mb-1">任务筛选</div>
                    <div className="space-y-0.5">
                      {[
                        { key: 'all', label: '全部任务' },
                        { key: 'manual', label: '手动任务' },
                        { key: 'scheduled', label: '自动化任务' },
                      ].map((opt) => (
                        <button
                          key={opt.key}
                          type="button"
                          onClick={() => props.setHistoryFilter(opt.key as 'all' | 'manual' | 'scheduled')}
                          className="w-full flex items-center justify-between rounded-[var(--radius-control)] px-2 py-1.5 text-[length:var(--fs-sm)] text-[var(--c-text)] hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer"
                        >
                          <span>{opt.label}</span>
                          {props.historyFilter === opt.key && <Check size={14} className="text-[var(--c-accent)]" />}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

      {props.allTasksExpanded && (
        <div className="flex-1 overflow-y-auto mt-2 space-y-2">
          {props.groupedHistoryConversations.length === 0 ? (
            <div className="pl-10 pr-3 py-2 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">
              暂无任务
            </div>
          ) : (
            props.groupedHistoryConversations.map((group) => {
              const groupCollapsed = props.collapsedWsGroups.has(group.id)
              const INITIAL_VISIBLE = 3
              const EXPAND_STEP = 10
              const visibleCount = props.wsListVisibleCount[group.id] ?? INITIAL_VISIBLE
              const groupItems = group.items
              const shownItems = groupItems.slice(0, visibleCount)
              const hiddenCount = groupItems.length - visibleCount
              const allShown = hiddenCount <= 0
              const toggleGroupCollapsed = () =>
                props.setCollapsedWsGroups((prev) => {
                  const next = new Set(prev)
                  if (next.has(group.id)) next.delete(group.id)
                  else next.add(group.id)
                  return next
                })
              const expandMore = () =>
                props.setWsListVisibleCount((prev) => {
                  const current = prev[group.id] ?? INITIAL_VISIBLE
                  const nextCount = Math.min(current + EXPAND_STEP, groupItems.length)
                  return { ...prev, [group.id]: nextCount }
                })
              const collapseList = () =>
                props.setWsListVisibleCount((prev) => {
                  const { [group.id]: _, ...rest } = prev
                  return rest
                })
              const isWorkspaceGroup = props.historyGroupBy === 'workspace'
              return (
                <div key={group.id} className="space-y-0.5">
                  {/* 分组标题行：文件夹图标 + 名称 + 折叠拐角 + 行末快捷新建（仅工作区分组） */}
                  <div className="flex items-center gap-1 pl-6 pr-2 pt-1.5 pb-0.5">
                    <button
                      type="button"
                      onClick={toggleGroupCollapsed}
                      className="flex items-center gap-1.5 min-w-0 flex-1 text-left cursor-pointer"
                      aria-expanded={!groupCollapsed}
                      title={group.label}
                    >
                      <img src={iconFolder} alt="" className="w-4 h-4 shrink-0 app-icon" />
                      <span className="truncate text-[length:var(--fs-sm)] font-medium text-[var(--c-text-secondary)]">
                        {group.label}
                      </span>
                      <ChevronDown
                        size={12}
                        className={`shrink-0 text-[var(--c-text-tertiary)] transition-transform ${groupCollapsed ? '-rotate-90' : ''}`}
                      />
                    </button>
                    {isWorkspaceGroup && (
                      <button
                        type="button"
                        onClick={() => props.onNewSessionInWorkspace(group.id)}
                        className="shrink-0 h-6 w-6 grid place-items-center rounded-[var(--radius-control)] text-[var(--c-text-secondary)] hover:text-[var(--c-text)] hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer"
                        title={`在「${group.label}」新建任务`}
                        aria-label={`在「${group.label}」新建任务`}
                      >
                        <Plus size={14} />
                      </button>
                    )}
                  </div>
                  {!groupCollapsed && (
                    /* 任务项向内缩进两档（pl-4 = +14px），让列表与 group header 形成视觉嵌套层级 */
                    <div className="pl-4">
                      {shownItems.map((c) => {
                        const active = c.id === props.activeConvId && props.activeSection === 'chat'
                        const itemImg = c.type === 'scheduled' ? iconClock : iconHistory
                        const isRunning = props.convStates[c.id]?.isStreaming ?? false
                        const runGate = props.convStates[c.id]?.runGate ?? null
                        const isAwaitingClarify = runGate === 'awaiting_clarification'
                        const isAwaitingConfirm = runGate === 'awaiting_confirmation'
                        const isAwaiting = isAwaitingClarify || isAwaitingConfirm
                        const isUnread = !isRunning && !isAwaiting && props.unreadConvIds.has(c.id)
                        const menuOpen = menuConvId === c.id
                        const selectConv = () => {
                          const convWs = c.workspaceId || 'default'
                          if (convWs !== props.backendWorkspaceId) props.setActiveWorkspaceId(convWs)
                          props.setActiveSection('chat')
                          props.onSelectSession(c.id)
                        }
                        return (
                          <div
                            key={c.id}
                            role="button"
                            tabIndex={0}
                            onClick={selectConv}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter' || e.key === ' ') {
                                e.preventDefault()
                                selectConv()
                              }
                            }}
                            className={`sidebar-nav-row group relative w-[calc(100%-24px)] mx-3 grid grid-cols-[20px_1fr_auto] items-center gap-2 px-3 py-2 rounded-[var(--radius-control)] text-[length:var(--fs-sm)] text-left transition-colors duration-200 cursor-pointer ${
                              active ? 'is-active' : ''
                            }`}
                            title={c.title}
                          >
                            <img src={itemImg} alt="" className="w-5 h-5 justify-self-start app-icon" />
                            <span className="truncate font-medium text-[var(--c-text-secondary)]">{c.title}</span>
                            <div className="flex items-center gap-1.5 shrink-0">
                              {c.updatedAt && c.updatedAt > 0 && !isRunning && !isAwaiting ? (
                                <span className="text-[length:var(--fs-xs)] text-[var(--c-text-tertiary)] whitespace-nowrap transition-opacity duration-200 group-hover:opacity-0">
                                  {props.formatRelativeTime(c.updatedAt)}
                                </span>
                              ) : null}
                              {isRunning && !isAwaiting && (
                                <Loader2 size={14} className="animate-spin text-[var(--c-accent)] shrink-0" />
                              )}
                              {isAwaiting && !active && (
                                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-[var(--radius-control)] text-[length:var(--fs-xs)] font-medium
                                                  bg-[var(--c-accent-soft)] text-[var(--c-accent)] shrink-0" title={isAwaitingConfirm ? '等待确认' : '等待澄清'}>
                                  <span className="w-1.5 h-1.5 rounded-full bg-[var(--c-accent)] animate-pulse" />
                                  待确认
                                </span>
                              )}
                              {isUnread && (
                                <div className="w-2 h-2 rounded-full bg-[var(--c-accent)] shrink-0" />
                              )}
                                {!isRunning && !isAwaiting && (
                                <button
                                  type="button"
                                  data-session-menu-btn
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    setMenuConvId(menuOpen ? null : c.id)
                                  }}
                                  className={`absolute right-2 top-1/2 -translate-y-1/2 h-6 w-6 grid place-items-center rounded-[var(--radius-control)] text-[var(--c-text-secondary)] hover:text-[var(--c-text)] hover:bg-[var(--c-surface)] transition-colors cursor-pointer z-10 ${
                                    menuOpen ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 pointer-events-none group-hover:pointer-events-auto'
                                  }`}
                                  title="会话操作"
                                  aria-label={`会话操作：${c.title}`}
                                  aria-haspopup="menu"
                                  aria-expanded={menuOpen}
                                >
                                  <MoreHorizontal size={14} />
                                </button>
                                )}
                                {!isRunning && !isAwaiting && menuOpen && (
                                  <div
                                    ref={sessionMenuRef}
                                    role="menu"
                                    className="overlay-panel absolute right-2 top-full mt-1 z-50 min-w-[128px] rounded-[var(--radius-card)] overflow-hidden py-1"
                                  >
                                    <button
                                      type="button"
                                      role="menuitem"
                                      onClick={(e) => {
                                        e.stopPropagation()
                                        setMenuConvId(null)
                                        const next = window.prompt('重命名会话', c.title)
                                        if (next === null) return
                                        props.onRenameSession(c.id, next.trim() || c.title)
                                      }}
                                      className="w-full flex items-center gap-2 px-3 py-1.5 text-[length:var(--fs-sm)] text-[var(--c-text)] hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer"
                                    >
                                      <Pencil size={13} className="text-[var(--c-text-secondary)]" />
                                      重命名
                                    </button>
                                    <button
                                      type="button"
                                      role="menuitem"
                                      onClick={(e) => {
                                        e.stopPropagation()
                                        setMenuConvId(null)
                                        props.onDeleteSession(c.id)
                                      }}
                                      className="w-full flex items-center gap-2 px-3 py-1.5 text-[length:var(--fs-sm)] text-[var(--c-danger)] hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer"
                                    >
                                      <Trash2 size={13} />
                                      删除
                                    </button>
                                  </div>
                                )}
                            </div>
                          </div>
                        )
                      })}
                      {!allShown && (
                        <button
                          type="button"
                          onClick={expandMore}
                          className="w-[calc(100%-24px)] mx-3 flex items-center gap-1 px-3 py-1.5 rounded-[var(--radius-control)] text-[length:var(--fs-sm)] text-left text-[var(--c-text-tertiary)] hover:text-[var(--c-text-secondary)] hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer"
                        >
                          <ChevronDown size={12} />
                          查看更多（{hiddenCount}）
                        </button>
                      )}
                      {allShown && groupItems.length > INITIAL_VISIBLE && (
                        <button
                          type="button"
                          onClick={collapseList}
                          className="w-[calc(100%-24px)] mx-3 flex items-center gap-1 px-3 py-1.5 rounded-[var(--radius-control)] text-[length:var(--fs-sm)] text-left text-[var(--c-text-tertiary)] hover:text-[var(--c-text-secondary)] hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer"
                        >
                          <ChevronUp size={12} />
                          收起
                        </button>
                      )}
                    </div>
                  )}
                </div>
              )
            })
          )}
        </div>
      )}

      {/* 自动化任务已合并到所有任务列表，通过筛选器区分 */}


      <div className="mt-auto">
        <HairlineDivider className="mx-3 mb-0.5" />

        <div className="px-3 pt-1 pb-2 relative">
          {props.userMenuOpen && (
            <div
              ref={props.userMenuRef}
              className="absolute left-3 right-3 bottom-12 z-50 rounded-[var(--radius-card)] overlay-panel overflow-hidden"
            >
              <div className="p-1.5">
                <button
                  type="button"
                  onClick={() => {
                    props.setUserMenuOpen(false)
                    props.setSettingsPageOpen(true)
                  }}
                  className="w-full px-2.5 py-1.5 flex items-center gap-2.5 rounded-[var(--radius-md)] text-left hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer"
                >
                  <Settings size={14} className="shrink-0 text-[var(--c-text-secondary)]" />
                  <span className="text-[length:var(--fs-sm)] font-medium text-[var(--c-text)]">设置</span>
                </button>

                <button
                  type="button"
                  className="w-full px-2.5 py-1.5 flex items-center justify-between gap-2.5 rounded-[var(--radius-md)] text-left hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer"
                >
                  <span className="flex items-center gap-2.5">
                    <Monitor size={14} className="shrink-0 text-[var(--c-text-secondary)]" />
                    <span className="text-[length:var(--fs-sm)] font-medium text-[var(--c-text)]">主题</span>
                  </span>
                  <ThemeToggle theme={props.theme} onChange={props.setTheme} />
                </button>
              </div>

              <HairlineDivider className="mx-3" />

              <div className="p-1.5">
                <button
                  type="button"
                  onClick={() => {
                    props.setUserMenuOpen(false)
                    props.logout()
                  }}
                  className="w-full px-2.5 py-1.5 flex items-center gap-2.5 rounded-[var(--radius-md)] text-left hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer"
                >
                  <LogOut size={14} className="shrink-0 text-[var(--c-error)]" />
                  <span className="text-[length:var(--fs-sm)] font-medium text-[var(--c-error)]">退出登录</span>
                </button>
              </div>
            </div>
          )}

          <button
            ref={props.userMenuButtonRef}
            type="button"
            onClick={() => props.setUserMenuOpen((v) => !v)}
            className={`w-full flex items-center gap-2 rounded-[var(--radius-md)] px-3 py-1 transition-colors cursor-pointer ${
              props.userMenuOpen ? 'bg-[var(--c-surface-3)]' : 'hover:bg-[var(--c-surface-3)]'
            }`}
          >
            <div className="w-6 h-6 rounded-full grid place-items-center text-[length:var(--fs-xs)] font-semibold bg-[var(--c-accent)]/10 text-[var(--c-accent)] ring-1 ring-[var(--c-accent)]/25">
              {(props.displayName || 'U').slice(0, 1).toUpperCase()}
            </div>
            <span className="min-w-0 flex-1 truncate text-left text-[length:var(--fs-sm)] font-medium text-[var(--c-text)]">
              {props.displayName || '未登录'}
            </span>
            <ChevronUp
              size={13}
              className={`shrink-0 text-[var(--c-text-secondary)] transition-transform duration-200 ${props.userMenuOpen ? '' : 'rotate-180'}`}
            />
          </button>
        </div>
      </div>
    </aside>
  )
}
