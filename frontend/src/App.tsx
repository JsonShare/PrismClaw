import React, { useEffect, type ReactNode } from 'react'
import { AuthProvider, useAuth } from './context/AuthContext'
import Login from './pages/LoginPage'
import ChatPage from './pages/ChatPage'
import { ToastProvider } from './lib/toast'
import { discoverAndRegisterDomains } from './domains/registry'

type RootBoundaryState = {
  hasError: boolean
  message: string | null
}

class RootErrorBoundary extends React.Component<{ children: ReactNode }, RootBoundaryState> {
  state: RootBoundaryState = { hasError: false, message: null }

  static getDerivedStateFromError(error: unknown): RootBoundaryState {
    const message =
      error && typeof error === 'object' && 'message' in error ? String((error as any).message) : null
    return { hasError: true, message: message || '页面渲染失败' }
  }

  componentDidCatch(error: unknown) {
    console.error('Root render error:', error)
  }

  render() {
    if (!this.state.hasError) return this.props.children
    return (
      <div className="min-h-screen grid place-items-center bg-[var(--c-bg)] px-4">
        <div className="app-panel w-full max-w-lg bg-[var(--c-surface)] border border-[var(--c-border)] p-5">
          <div className="text-[length:var(--fs-lg)] font-semibold text-[var(--c-text)]">页面异常</div>
          <div className="mt-2 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)] whitespace-pre-wrap break-words">
            {this.state.message}
          </div>
          <div className="mt-4 flex items-center gap-2">
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="h-9 px-3 rounded-[var(--radius-control)] bg-[var(--c-btn-primary-bg)] text-[var(--c-btn-primary-fg)] text-[length:var(--fs-md)] font-medium hover:opacity-90 active:scale-[0.98] transition-all cursor-pointer"
            >
              刷新页面
            </button>
            <button
              type="button"
              onClick={() => {
                try {
                  localStorage.removeItem('prismclaw.auth.v1')
                } catch {}
                window.location.reload()
              }}
              className="h-9 px-3 rounded-[var(--radius-control)] bg-[var(--c-surface-2)] text-[var(--c-text)] text-[length:var(--fs-md)] font-medium hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer"
            >
              清除登录并刷新
            </button>
          </div>
        </div>
      </div>
    )
  }
}

function Router() {
  const { token } = useAuth()
  return token ? <ChatPage /> : <Login />
}

export default function App() {
  useEffect(() => {
    // 启动时从后端发现可用域并注册
    discoverAndRegisterDomains().catch(() => {
      // 域发现失败时降级为本地注册，discoverAndRegisterDomains 内部已处理
    })
  }, [])

  return (
    <RootErrorBoundary>
      <AuthProvider>
        <ToastProvider>
          <div className="h-screen flex flex-col">
            <div className="flex-1 flex flex-col overflow-hidden">
              <Router />
            </div>
          </div>
        </ToastProvider>
      </AuthProvider>
    </RootErrorBoundary>
  )
}
