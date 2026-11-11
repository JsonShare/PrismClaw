import { Component, type ReactNode, type ErrorInfo } from 'react'

interface ErrorBoundaryProps {
  children: ReactNode
  fallback?: ReactNode
  /** 出错时的回调（用于日志上报） */
  onError?: (error: Error, info: ErrorInfo) => void
}

interface ErrorBoundaryState {
  hasError: boolean
  error: Error | null
}

/**
 * 错误边界 — 防止子组件渲染崩溃导致整个页面白屏。
 * 包裹关键区域（消息列表、模态框、输入区）。
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    this.props.onError?.(error, info)
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback ?? (
          <div className="p-6 text-center">
            <div className="text-[length:var(--fs-lg)] font-semibold text-[var(--c-error)] mb-2">渲染出错</div>
            <div className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)] mb-4">
              {this.state.error?.message || '未知错误'}
            </div>
            <button
              type="button"
              onClick={() => this.setState({ hasError: false, error: null })}
              className="btn btn-md btn-primary"
            >
              重试
            </button>
          </div>
        )
      )
    }
    return this.props.children
  }
}
