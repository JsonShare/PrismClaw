import { useState, useCallback, useRef, useEffect } from 'react'
import {
  ArrowLeft,
  Search,
  User,
  Palette,
  SlidersHorizontal,
  LogOut,
  Monitor,
  Sun,
  Moon,
} from 'lucide-react'
import type { Theme } from '../../../types'
import { type ChatWidth, CHAT_WIDTH_MIN, CHAT_WIDTH_MAX } from '../../../stores/ui'
import { HairlineDivider } from '../../../components/ui/HairlineDivider'

export type SettingsPageProps = {
  /** 是否显示 */
  open: boolean
  onClose: () => void

  /** 用户信息 */
  displayName: string | null
  onLogout: () => void

  /** 桌面通知 */
  desktopNotifications: boolean
  onToggleDesktopNotifications: () => void
  /** 发送一条测试通知，用于确认权限与系统通知链路是否生效；返回诊断结果 */
  onTestDesktopNotification: () => Promise<{ ok: boolean; message: string }>
  /** 通知权限状态：granted / denied / default / unsupported（浏览器不支持）/ insecure（非安全上下文） */
  notificationPermission: 'granted' | 'denied' | 'default' | 'unsupported' | 'insecure'

  /** 对话宽度 */
  chatWidth: ChatWidth
  onChangeChatWidth: (w: ChatWidth) => void

  /** 主题 */
  theme: Theme
  onChangeTheme: (theme: Theme) => void
}

type NavItem = {
  id: string
  label: string
  icon: React.ReactNode
}

type NavGroup = {
  label: string
  items: NavItem[]
}

const navGroups: NavGroup[] = [
  {
    label: '个性化',
    items: [
      { id: 'profile', label: '个人资料', icon: <User size={16} /> },
      { id: 'appearance', label: '主题外观', icon: <Palette size={16} /> },
      { id: 'preferences', label: '偏好设置', icon: <SlidersHorizontal size={16} /> },
    ],
  },
]

export function SettingsPage({
  open,
  onClose,
  displayName,
  onLogout,
  desktopNotifications,
  onToggleDesktopNotifications,
  onTestDesktopNotification,
  notificationPermission,
  chatWidth,
  onChangeChatWidth,
  theme,
  onChangeTheme,
}: SettingsPageProps) {
  const [activeNav, setActiveNav] = useState('profile')
  const [searchQuery, setSearchQuery] = useState('')
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null)
  const [testPending, setTestPending] = useState(false)
  const searchRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open) {
      requestAnimationFrame(() => searchRef.current?.focus())
    }
  }, [open])

  const filteredNavGroups = searchQuery.trim()
    ? navGroups
        .map((g) => ({
          ...g,
          items: g.items.filter((i) => i.label.toLowerCase().includes(searchQuery.trim().toLowerCase())),
        }))
        .filter((g) => g.items.length > 0)
    : navGroups

  const activeLabel = navGroups.flatMap((g) => g.items).find((i) => i.id === activeNav)?.label || '设置'

  const renderContent = useCallback(() => {
    switch (activeNav) {
      case 'profile':
        return (
          <div className="space-y-6">
            <div className="text-[length:var(--fs-lg)] font-semibold text-[var(--c-text)]">用户信息</div>
            <div className="rounded-[var(--radius-card)] border border-[var(--c-border)] bg-[var(--c-surface)] overflow-hidden">
              <div className="h-2" />
              <div className="px-5 py-4 flex items-center justify-between gap-4">
                <div className="flex items-center gap-4 min-w-0">
                  <div className="w-12 h-12 rounded-full bg-[var(--c-text)] text-[var(--c-bg)] grid place-items-center text-[length:var(--fs-lg)] font-bold">
                    {(displayName || 'U').slice(0, 1).toUpperCase()}
                  </div>
                  <div className="min-w-0">
                    <div className="text-[length:var(--fs-lg)] font-semibold text-[var(--c-text)] truncate">{displayName || '未登录'}</div>
                    <div className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">免费版</div>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={onLogout}
                  className="btn btn-circle btn-ghost shrink-0"
                  title="登出"
                  aria-label="登出"
                >
                  <LogOut size={20} />
                </button>
              </div>
            </div>
          </div>
        )

      case 'appearance':
        return (
          <div className="space-y-8">
            {/* 主题卡片 */}
            <div className="grid grid-cols-3 gap-4">
              {[
                { value: 'system' as Theme, label: '跟随系统', icon: Monitor, previewBg: 'linear-gradient(to right, #f0f0f0 50%, #2a2a2a 50%)' },
                { value: 'light' as Theme, label: '浅色', icon: Sun, previewBg: '#f0f0f0' },
                { value: 'dark' as Theme, label: '深色', icon: Moon, previewBg: '#2a2a2a' },
              ].map(({ value, label, icon: Icon, previewBg }) => {
                const active = theme === value
                return (
                  <button
                    key={value}
                    type="button"
                    onClick={() => onChangeTheme(value)}
                    className={`group relative flex flex-col gap-3 rounded-[var(--radius-md)] border p-4 transition-all duration-200 cursor-pointer text-left ${
                      active
                        ? 'border-[var(--c-accent)] bg-[var(--c-surface)] shadow-[0_0_0_1px_var(--c-accent)]'
                        : 'border-[var(--c-border)] bg-[var(--c-surface)] hover:border-[var(--c-border-strong)]'
                    }`}
                  >
                    {/* 选中指示器 */}
                    {active && (
                      <div className="absolute top-3 right-3">
                        <div className="h-5 w-5 rounded-full bg-[var(--c-accent)] flex items-center justify-center">
                          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                            <path d="M2.5 6L5 8.5L9.5 4" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                          </svg>
                        </div>
                      </div>
                    )}

                    {/* 缩略图 — 移除 border-black/5 避免系统卡片出现左侧黑线 */}
                    <div
                      className="w-full aspect-[16/10] rounded-[var(--radius-md)] overflow-hidden"
                      style={{ background: previewBg }}
                    >
                      <div className="flex flex-col justify-end h-full p-2 gap-1.5">
                        <div className="flex gap-1">
                          <div className="w-12 h-2.5 rounded-full bg-white/90" />
                        </div>
                        <div className="flex gap-1">
                          <div className="w-20 h-2 rounded-full bg-white/60" />
                          <div className="w-10 h-2 rounded-full bg-white/40" />
                        </div>
                        <div className="mt-1 w-full h-5 rounded-[var(--radius-control)] bg-white/80 flex items-center px-1.5 gap-1">
                          <div className="w-3 h-1.5 rounded-full bg-black/10" />
                        </div>
                      </div>
                    </div>

                    {/* 标签 */}
                    <div className="flex items-center gap-2">
                      <Icon size={16} className="text-[var(--c-text-secondary)]" />
                      <span className={`text-[length:var(--fs-sm)] font-medium ${active ? 'text-[var(--c-accent)]' : 'text-[var(--c-text)]'}`}>
                        {label}
                      </span>
                    </div>
                  </button>
                )
              })}
            </div>
          </div>
        )

      case 'preferences':
        return (
          <div className="space-y-6">
            <div className="text-[length:var(--fs-lg)] font-semibold text-[var(--c-text)]">偏好设置</div>
            <div className="rounded-[var(--radius-card)] border border-[var(--c-border)] bg-[var(--c-surface)] overflow-hidden">
              <div className="px-5 py-4 flex items-center justify-between gap-5">
                <div className="min-w-0">
                  <div className="text-[length:var(--fs-md)] font-semibold text-[var(--c-text)]">对话宽度</div>
                  <div className="mt-1 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">
                    新任务页面中间栏内容区的默认宽度，可拖动调整
                  </div>
                </div>
                <div className="shrink-0 flex items-center gap-3 w-64">
                  <div className="flex-1 flex flex-col gap-0.5">
                    <input
                      type="range"
                      min={CHAT_WIDTH_MIN}
                      max={CHAT_WIDTH_MAX}
                      step={100}
                      value={chatWidth}
                      onChange={(e) => onChangeChatWidth(Number(e.target.value))}
                      className="width-slider w-full"
                      aria-label="对话宽度"
                    />
                    <div className="flex justify-between px-[7px]" aria-hidden="true">
                      {Array.from({ length: 7 }).map((_, i) => (
                        <span key={i} className="h-1 w-px bg-[var(--c-border)]" />
                      ))}
                    </div>
                  </div>
                  <span className="w-24 text-right text-[length:var(--fs-sm)] tabular-nums text-[var(--c-text-secondary)]">
                    {chatWidth === 1000 ? '默认 (1000px)' : `${chatWidth}px`}
                  </span>
                </div>
              </div>
              <HairlineDivider className="mx-5" />
              <div className="px-5 py-4 flex items-center justify-between gap-5">
                <div className="min-w-0">
                  <div className="text-[length:var(--fs-md)] font-semibold text-[var(--c-text)]">桌面通知</div>
                  <div className="mt-1 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">
                    当 PrismClaw 完成任务、需要您确认或执行出错时，显示系统通知
                    {notificationPermission === 'denied' && (
                      <span className="ml-1 text-[var(--c-error)]">（浏览器权限已被拒绝，请在系统设置中开启）</span>
                    )}
                    {notificationPermission === 'insecure' && (
                      <span className="ml-1 text-[var(--c-error)]">（当前访问地址非安全上下文，请改用 localhost 或 HTTPS 访问）</span>
                    )}
                    {notificationPermission === 'unsupported' && (
                      <span className="ml-1 text-[var(--c-error)]">（当前浏览器不支持系统通知）</span>
                    )}
                  </div>
                  {desktopNotifications && notificationPermission === 'granted' && (
                    <div className="mt-2">
                      <button
                        type="button"
                        disabled={testPending}
                        onClick={async () => {
                          setTestPending(true)
                          setTestResult(null)
                          try {
                            setTestResult(await onTestDesktopNotification())
                          } catch (err) {
                            setTestResult({
                              ok: false,
                              message: err instanceof Error ? err.message : String(err),
                            })
                          } finally {
                            setTestPending(false)
                          }
                        }}
                        className="text-[length:var(--fs-sm)] text-[var(--c-accent)] hover:underline disabled:opacity-50"
                      >
                        {testPending ? '正在发送…' : '发送测试通知'}
                      </button>
                      {testResult && (
                        <div
                          className={`mt-1 text-[length:var(--fs-sm)] ${
                            testResult.ok ? 'text-[var(--c-text-secondary)]' : 'text-[var(--c-error)]'
                          }`}
                        >
                          {testResult.message}
                        </div>
                      )}
                    </div>
                  )}
                </div>
                <button
                  type="button"
                  onClick={onToggleDesktopNotifications}
                  disabled={
                    notificationPermission === 'denied' ||
                    notificationPermission === 'unsupported' ||
                    notificationPermission === 'insecure'
                  }
                  className={`h-6 w-11 rounded-full border transition-colors cursor-pointer flex items-center shrink-0 ${
                    desktopNotifications
                      ? 'bg-[var(--c-accent)] border-[var(--c-accent)]'
                      : 'bg-[var(--c-border)] border-[var(--c-border)]'
                  } ${
                    notificationPermission === 'denied' ||
                    notificationPermission === 'unsupported' ||
                    notificationPermission === 'insecure'
                      ? 'opacity-40 cursor-not-allowed'
                      : ''
                  }`}
                  aria-label="桌面通知"
                >
                  <span
                    className={`h-5 w-5 rounded-full bg-white transition-transform ${
                      desktopNotifications ? 'translate-x-5' : 'translate-x-1'
                    }`}
                  />
                </button>
              </div>
            </div>
          </div>
        )

      default:
        return null
    }
  }, [
    activeNav,
    displayName,
    onLogout,
    theme,
    onChangeTheme,
    desktopNotifications,
    onToggleDesktopNotifications,
    notificationPermission,
    onTestDesktopNotification,
    testResult,
    testPending,
    chatWidth,
    onChangeChatWidth,
  ])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-[60] bg-[var(--c-canvas)] text-[var(--c-text)] flex p-3">
      <div className="h-full w-full rounded-[var(--radius-card)] overflow-hidden flex flex-col">
        <div className="flex flex-1 overflow-hidden">
      {/* ── 左侧导航 ── */}
      <aside className="shrink-0 w-[240px] h-full flex flex-col app-sidebar">
        {/* macOS 交通灯避让区域 */}
        <div className="h-[38px] shrink-0" />

        {/* 返回按钮 */}
        <div className="px-4 pb-2">
          <button
            type="button"
            onClick={onClose}
            className="sidebar-nav-row flex items-center gap-2 px-3 py-2 rounded-[var(--radius-control)] text-[length:var(--fs-sm)] text-[var(--c-text)] transition-colors cursor-pointer"
          >
            <ArrowLeft size={16} />
            <span>返回对话</span>
          </button>
        </div>

        {/* 搜索框 */}
        <div className="px-4 pb-3">
          <div className="relative">
            <Search size={14} className={`absolute left-3 top-1/2 -translate-y-1/2 text-[var(--c-text)] opacity-50`} />
            <input
              ref={searchRef}
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="搜索设置..."
              className={`w-full h-9 pl-9 pr-3 rounded-[var(--radius-md)] bg-[var(--c-surface)]/60 border border-[var(--c-border)] text-[length:var(--fs-sm)] text-[var(--c-text)] placeholder:text-[var(--c-text)] placeholder:opacity-50 focus:outline-none focus:border-[var(--c-accent)]/50 transition-colors`}
            />
          </div>
        </div>

        {/* 导航列表 */}
        <div className="flex-1 overflow-y-auto px-4">
          {filteredNavGroups.map((group) => (
            <div key={group.label} className="mb-2">
              <div className={`py-2 text-[length:var(--fs-sm)] font-medium text-[var(--c-text)] opacity-60 uppercase tracking-wider`}>
                {group.label}
              </div>
              {group.items.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setActiveNav(item.id)}
                  className={`sidebar-nav-row w-full rounded-[var(--radius-control)] flex items-center gap-2.5 px-3 py-2 text-[length:var(--fs-sm)] text-left transition-colors cursor-pointer ${
                    activeNav === item.id ? 'is-active font-medium' : ''
                  }`}
                >
                  <span className="shrink-0">{item.icon}</span>
                  <span className="flex-1">{item.label}</span>
                </button>
              ))}
            </div>
          ))}
        </div>
      </aside>

      {/* ── 右侧内容区 ── */}
      <main className="flex-1 min-w-0 min-h-0 flex flex-col relative app-panel">
        <div className="flex-1 overflow-y-auto">
          <div className="max-w-[840px] mx-auto px-8 py-8">
            {/* 页面标题 */}
            <div className="flex items-center justify-between mb-6">
              <h1 className="text-[length:var(--fs-xl)] font-bold text-[var(--c-text)]">{activeLabel}</h1>
            </div>

            {/* 内容区 */}
            {renderContent()}
          </div>
        </div>
      </main>
        </div>
      </div>
    </div>
  )
}
