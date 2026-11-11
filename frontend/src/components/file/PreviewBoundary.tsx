import { Component, type ReactNode } from 'react'

interface PreviewBoundaryProps {
  children: ReactNode
}

interface PreviewBoundaryState {
  failed: boolean
}

/**
 * 右面板预览器的错误边界：FilePreview / @file-viewer 卸载竞态或渲染异常
 * 只降级为单面板错误提示，不再冒泡导致整页白屏（项目根无 ErrorBoundary，
 * 任何组件树异常都会卸载整个应用，症状为「只剩顶栏的纯白页面」）。
 */
export class PreviewBoundary extends Component<PreviewBoundaryProps, PreviewBoundaryState> {
  state: PreviewBoundaryState = { failed: false }

  static getDerivedStateFromError(): PreviewBoundaryState {
    return { failed: true }
  }

  componentDidCatch(error: unknown) {
    console.warn('[PreviewBoundary] 文件预览渲染失败:', error)
  }

  render() {
    if (this.state.failed) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">
          <span className="text-[var(--c-danger)]">文件预览渲染失败</span>
          <button
            type="button"
            onClick={() => this.setState({ failed: false })}
            className="rounded-[var(--radius-control)] px-3 py-1.5 text-[var(--c-accent)] hover:bg-[var(--c-accent)]/8 transition-colors cursor-pointer"
          >
            重试
          </button>
        </div>
      )
    }
    return this.props.children
  }
}