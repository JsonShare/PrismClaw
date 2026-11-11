import { Check, ShieldOff } from 'lucide-react'
import { ModalShell } from './ModalShell'

export interface WorkspaceItem {
  id: string
  name: string
  trusted?: boolean
}

export interface WorkspaceSelectorModalProps {
  open: boolean
  onClose: () => void
  workspaces: WorkspaceItem[]
  activeWorkspaceId: string
  onSelectWorkspace: (id: string) => void
  onRefresh: () => void
  busy: boolean
  loading: boolean
  error: string | null
}

/**
 * 工作区选择弹窗 — 从 Chat.tsx 提取。
 * 约转出 105 行 JSX。
 */
export function WorkspaceSelectorModal({
  open,
  onClose,
  workspaces,
  activeWorkspaceId,
  onSelectWorkspace,
  onRefresh,
  busy,
  loading,
  error,
}: WorkspaceSelectorModalProps) {
  return (
    <ModalShell open={open} onClose={onClose} title="设置工作区" maxWidth="max-w-lg">
      <div className="p-3">
        {error && (
          <div className="mb-2 px-3 py-2 rounded-[var(--radius-card)] border border-[var(--c-error)]/30 bg-[var(--c-error)]/10 text-[var(--c-error)] text-[length:var(--fs-md)]">
            {error}
          </div>
        )}
        <div className="max-h-80 overflow-y-auto">
          {loading ? (
            <div className="px-3 py-3 text-[length:var(--fs-md)] text-[var(--c-text-secondary)]">加载中…</div>
          ) : workspaces.length === 0 ? (
            <div className="px-3 py-3 text-[length:var(--fs-md)] text-[var(--c-text-secondary)]">未发现工作区</div>
          ) : (
            <div className="space-y-1">
              {workspaces.map((w) => {
                const active = w.id === activeWorkspaceId
                const subtitle = (w.name || w.id) as string
                return (
                  <button
                    key={w.id}
                    type="button"
                    onClick={() => {
                      onSelectWorkspace(w.id)
                      onClose()
                    }}
                    className={`w-full flex items-center gap-2 px-3 py-2 rounded-[var(--radius-md)] text-left transition-colors cursor-pointer ${
                      active
                        ? 'bg-[var(--c-surface-2)] text-[var(--c-text)]'
                        : 'text-[var(--c-text)] hover:bg-[var(--c-surface-2)]'
                    }`}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <div className="text-[length:var(--fs-md)] font-medium truncate">{w.name}</div>
                        {w.trusted === false && (
                          <span title="不受信任"><ShieldOff size={14} className="text-[var(--c-error)] shrink-0" /></span>
                        )}
                      </div>
                      <div className="mt-0.5 flex items-center gap-1 min-w-0" title={subtitle}>
                        <div className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)] truncate min-w-0 flex-1">
                          {subtitle}
                        </div>
                      </div>
                    </div>
                    {active && <Check size={16} className="text-[var(--c-text-secondary)] shrink-0" />}
                  </button>
                )
              })}
            </div>
          )}
        </div>

        <div className="mt-3 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onRefresh}
            disabled={busy || loading}
            className="btn btn-lg btn-secondary"
          >
            刷新
          </button>
        </div>
      </div>
    </ModalShell>
  )
}
