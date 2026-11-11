import {
  ChevronUp,
  LoaderCircle,
  Sparkles,
  Clock3,
  Clock,
  Wrench,
  Bot,
  Download,
  X,
} from 'lucide-react'
import { FilePreviewWithReview } from '../../components/file/FilePreviewWithReview'
import FileIcon from '../../components/file/FileIcon'
import type { FileChange, ToolCallInfo } from '../../types'
import type { JSONContent } from '@tiptap/core'
import iconOpen from '../../images/other_icon/open.png'

interface TaskRuntimeSummary {
  title: string
  subtitle: string
  icon: 'streaming' | 'finalizing'
}

interface RunningSubagent {
  sub_session_id: string
  subagent_name: string
  current_tool: string | null
  partial_status?: string
}

interface ReviewHandlers {
  onConfirm: () => Promise<void>
  onReject: (force?: boolean) => Promise<void>
  onSaveContent: (content: string) => Promise<void>
  onRollback: () => Promise<void>
  onDocxNativeCommitted: () => Promise<void>
  onDocxNativeRejected: () => Promise<void>
  onReviewDone: () => void
}

interface ChatRightPanelProps {
  showLandingHome: boolean
  taskFilesOpen: boolean
  /** 面板当前宽度（px）。实际宽度由父级 CSS 变量 `--right-panel-w` 驱动，此值仅作 fallback */
  width: number
  selectedFile: string | null
  previewBlob: Blob | null
  reviewingChange: FileChange | null
  reviewHtml: JSONContent | null
  reviewHtmlError: string | null
  selectedFileError: string | null
  activeConvId: string | null
  token: string | null
  rightProcessOpen: boolean
  setRightProcessOpen: (v: boolean | ((prev: boolean) => boolean)) => void
  rightFilesOpen: boolean
  setRightFilesOpen: (v: boolean | ((prev: boolean) => boolean)) => void
  rightCallsOpen: boolean
  setRightCallsOpen: (v: boolean | ((prev: boolean) => boolean)) => void
  rightTrajectoryOpen: boolean
  setRightTrajectoryOpen: (v: boolean | ((prev: boolean) => boolean)) => void
  runningSubagentsForPanel: RunningSubagent[]
  runningToolCallsForPanel: ToolCallInfo[]
  isStreaming: boolean
  isQueued: boolean
  liveTaskRuntime: any
  taskRuntimeSummary: TaskRuntimeSummary
  queuePosition: number
  formatToolProgressText: (tc: ToolCallInfo) => string
  panelTaskOutputFiles: string[]
  normalizeWorkspacePath: (raw: string) => string | null
  openFileFromOutputList: (path: string) => void
  downloadSingleFile: (path: string) => Promise<void>
  toolNamesForPanel: string[]
  skillsForPanel: string[]
  setTrajectoryOpen: (v: boolean) => void
  closePreviewModal: () => void
  reviewHandlers: ReviewHandlers
}

const rightRailSectionClass = 'rounded-[var(--radius-card)] bg-transparent overflow-hidden'
const rightRailHeaderClass = 'w-full px-4 py-3 flex items-center justify-between cursor-pointer transition-colors'
const rightRailBodyClass = 'px-4 pb-4 pt-0'

export function ChatRightPanel(props: ChatRightPanelProps) {
  if (props.showLandingHome) return null

  return (
    <aside
      className={`hidden xl:flex shrink-0 min-w-0 app-sidebar app-sidebar-right flex-col h-full transition-opacity duration-200 ${
        props.taskFilesOpen ? '' : 'w-0'
      }`}
      style={props.taskFilesOpen ? { width: `var(--right-panel-w, ${props.width}px)` } : undefined}
    >
      {props.taskFilesOpen ? (
        <>
          {props.selectedFile ? (
            props.previewBlob && props.reviewingChange ? (
              <FilePreviewWithReview
                key={props.selectedFile}
                file={props.previewBlob}
                fileName={props.selectedFile?.split('/').pop() || ''}
                filePath={props.selectedFile}
                change={props.reviewingChange}
                reviewDoc={props.reviewHtml}
                reviewError={props.reviewHtmlError}
                conversationId={props.activeConvId ?? undefined}
                token={props.token ?? undefined}
                onConfirm={props.reviewHandlers.onConfirm}
                onReject={props.reviewHandlers.onReject}
                onSaveContent={props.reviewHandlers.onSaveContent}
                onRollback={props.reviewHandlers.onRollback}
                onDocxNativeCommitted={props.reviewHandlers.onDocxNativeCommitted}
                onDocxNativeRejected={props.reviewHandlers.onDocxNativeRejected}
                onReviewDone={props.reviewHandlers.onReviewDone}
                onClose={props.closePreviewModal}
                className="w-full h-full"
              />
            ) : props.previewBlob ? (
              <FilePreviewWithReview
                key={props.selectedFile}
                file={props.previewBlob}
                fileName={props.selectedFile?.split('/').pop() || ''}
                filePath={props.selectedFile}
                onSaveContent={props.reviewHandlers.onSaveContent}
                onClose={props.closePreviewModal}
                className="w-full h-full"
              />
            ) : props.selectedFileError ? (
              <div className="h-full flex flex-col">
                <div className="flex items-center justify-between px-4 py-2.5 shrink-0">
                  <span className="text-[length:var(--fs-sm)] font-medium text-[var(--c-text)]">{props.selectedFile?.split('/').pop()}</span>
                  <button type="button" onClick={props.closePreviewModal} className="btn btn-icon btn-ghost">
                    <X size={16} />
                  </button>
                </div>
                <div className="flex-1 flex items-center justify-center text-[length:var(--fs-sm)] text-[var(--c-error)]">{props.selectedFileError}</div>
              </div>
            ) : (
              <div className="h-full flex flex-col">
                <div className="flex items-center justify-between px-4 py-2.5 shrink-0">
                  <span className="text-[length:var(--fs-sm)] font-medium text-[var(--c-text)]">{props.selectedFile?.split('/').pop()}</span>
                  <button type="button" onClick={props.closePreviewModal} className="btn btn-icon btn-ghost">
                    <X size={16} />
                  </button>
                </div>
                <div className="flex-1 flex items-center justify-center text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]">加载中…</div>
              </div>
            )
          ) : (
            <div className="flex-1 overflow-y-auto px-4 pt-4 space-y-4">

            {/* 进程区块 */}
            <div className={rightRailSectionClass}>
              <button
                type="button"
                onClick={() => props.setRightProcessOpen((v) => !v)}
                className={rightRailHeaderClass}
              >
                <div className="text-[length:var(--fs-md)] font-semibold text-[var(--c-text)]">任务进程</div>
                <ChevronUp size={18} className={`text-[var(--c-text-secondary)] transition-transform ${props.rightProcessOpen ? '' : '-rotate-180'}`} />
              </button>
              {props.rightProcessOpen && (
                <div className={rightRailBodyClass}>
                  {props.runningSubagentsForPanel.length === 0 && props.runningToolCallsForPanel.length === 0 && !props.isStreaming && !props.isQueued && !props.liveTaskRuntime ? (
                    <div className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)] leading-relaxed opacity-60">
                      查看任务的运行状态
                    </div>
                  ) : (
                    <div className="space-y-4">
                      {(props.isStreaming || props.liveTaskRuntime) && (
                        <div className="flex items-center gap-2.5">
                          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full">
                            {props.taskRuntimeSummary.icon === 'finalizing' ? (
                              <Sparkles size={13} className="text-[var(--c-accent)]" />
                            ) : (
                              <LoaderCircle size={13} className="text-[var(--c-accent)] animate-spin" />
                            )}
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2.5">
                              <div className="text-[length:var(--fs-sm)] font-semibold text-[var(--c-text)]">
                                {props.taskRuntimeSummary.title}
                              </div>
                            </div>
                          </div>
                        </div>
                      )}
                      {props.isQueued && (
                        <div className="flex items-center gap-2.5">
                          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full">
                            <Clock3 size={13} className="text-[var(--c-icon-soft-blue)]" />
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="text-[length:var(--fs-sm)] font-semibold text-[var(--c-text)]">
                              队列等待{props.queuePosition > 0 ? `（第 ${props.queuePosition} 位）` : ''}
                            </div>
                            <div className="mt-0.5 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/72">
                              等待执行…
                            </div>
                          </div>
                        </div>
                      )}
                      {props.runningToolCallsForPanel.slice(0, 4).map((tc) => (
                        <div key={tc.id} className="flex items-center gap-2.5">
                          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full">
                            {tc.progressPhase === 'waiting_approval' ? (
                              <Clock size={13} className="text-[var(--c-icon-soft-blue)]" />
                            ) : (
                              <Wrench size={13} className="text-[var(--c-accent)]" />
                            )}
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="text-[length:var(--fs-sm)] font-semibold text-[var(--c-text)] truncate">
                              {tc.name}
                            </div>
                            <div className="mt-0.5 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/66 truncate">
                              {props.formatToolProgressText(tc)}
                            </div>
                          </div>
                        </div>
                      ))}
                      {props.runningSubagentsForPanel.slice(0, 6).map((s) => (
                        <div key={s.sub_session_id} className="flex items-center gap-2.5">
                          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full">
                            <Bot size={13} className="text-[var(--c-text-secondary)]" />
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="text-[length:var(--fs-sm)] font-semibold text-[var(--c-text)] truncate">
                              {s.subagent_name}
                            </div>
                            <div className="mt-0.5 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/66 truncate">
                              {s.current_tool ? s.current_tool : '运行中'}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* 文件区块 */}
            <div className={rightRailSectionClass}>
              <button
                type="button"
                onClick={() => props.setRightFilesOpen((v) => !v)}
                className={rightRailHeaderClass}
              >
                <div className="text-[length:var(--fs-md)] font-semibold text-[var(--c-text)]">产物输出</div>
                <ChevronUp size={18} className={`text-[var(--c-text-secondary)] transition-transform ${props.rightFilesOpen ? '' : '-rotate-180'}`} />
              </button>
              {props.rightFilesOpen && (
                <div className={rightRailBodyClass}>
                  {props.panelTaskOutputFiles.length === 0 ? (
                    <div className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)] leading-relaxed opacity-60">
                      查看和打开任务产出的文件
                    </div>
                  ) : (
                    <div className="mt-2 space-y-2">
                      {props.panelTaskOutputFiles.slice(0, 6).map((p) => {
                        const name = p.split('/').pop() || p
                        const normalized = props.normalizeWorkspacePath(p)
                        return (
                          <div
                            key={`tf-${p}`}
                            onClick={() => props.openFileFromOutputList(p)}
                            className="w-full px-3 py-2.5 flex items-center gap-3 text-left relative group
                                       transition-colors cursor-pointer"
                            title={p}
                          >
                            <div className="absolute top-1.5 right-1.5 flex items-center gap-1 transition-opacity">
                              {normalized ? (
                                <button
                                  type="button"
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    void props.downloadSingleFile(normalized)
                                  }}
                                  className="h-6 w-6 grid place-items-center transition-all
                                             hover:bg-[var(--c-surface-2)] active:scale-[0.98] cursor-pointer
                                             text-[var(--c-text)]"
                                  title="下载文件"
                                  aria-label="下载文件"
                                >
                                  <Download size={14} className="text-[var(--c-text)]" />
                                </button>
                              ) : null}
                            </div>
                            <FileIcon filename={name} size={22} className="shrink-0" />
                            <div className="min-w-0 flex-1 pr-16">
                              <div className="text-[length:var(--fs-sm)] font-semibold text-[var(--c-text)] truncate">
                                {name}
                              </div>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* 调用区块 */}
            <div className="rounded-[var(--radius-card)] bg-transparent overflow-hidden">
              <button
                type="button"
                onClick={() => props.setRightCallsOpen((v) => !v)}
                className={rightRailHeaderClass}
              >
                <div className="text-[length:var(--fs-md)] font-semibold text-[var(--c-text)]">调用能力</div>
                <ChevronUp size={18} className={`text-[var(--c-text-secondary)] transition-transform ${props.rightCallsOpen ? '' : '-rotate-180'}`} />
              </button>
              {props.rightCallsOpen && (
                <div className="px-4 pb-4 pt-0 space-y-4">
                  {props.toolNamesForPanel.length === 0 && props.skillsForPanel.length === 0 ? (
                    <div className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)] leading-relaxed opacity-60">
                      根据实际执行展示调用能力
                    </div>
                  ) : (
                    <>
                      {props.toolNamesForPanel.length > 0 ? (
                        <div className="space-y-2">
                           <div className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/70">工具</div>
                          {props.toolNamesForPanel.map((toolName) => (
                            <div
                              key={toolName}
                              className="flex items-center gap-2.5 px-3 py-2 cursor-default"
                            >
                              <div className="w-5 h-5 flex items-center justify-center shrink-0">
                                <Wrench size={12} className="text-[var(--c-text-secondary)]" />
                              </div>
                              <div className="min-w-0 flex-1">
                                <div className="text-[length:var(--fs-sm)] font-semibold text-[var(--c-text)] truncate">
                                  {toolName}
                                </div>
                              </div>
                            </div>
                          ))}
                        </div>
                      ) : null}
                      {props.skillsForPanel.length > 0 ? (
                        <div className="space-y-2">
                           <div className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/70">技能</div>
                          {props.skillsForPanel.map((name) => (
                            <div
                              key={name}
                              className="flex items-center gap-2.5 px-3 py-2 cursor-default"
                            >
                              <div className="w-5 h-5 flex items-center justify-center shrink-0">
                                <Sparkles size={12} className="text-[var(--c-text-secondary)]" />
                              </div>
                              <div className="min-w-0 flex-1">
                                <div className="text-[length:var(--fs-sm)] font-semibold text-[var(--c-text)] truncate">
                                  {name}
                                </div>
                              </div>
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </>
                  )}
                </div>
              )}
            </div>
            {/* 运行轨迹: 折叠区块, 子项点击弹出审计面板 Modal */}
            <div className={rightRailSectionClass}>
              <button
                type="button"
                onClick={() => props.setRightTrajectoryOpen((v) => !v)}
                className={rightRailHeaderClass}
              >
                <div className="text-[length:var(--fs-md)] font-semibold text-[var(--c-text)]">运行轨迹</div>
                <ChevronUp size={18} className={`text-[var(--c-text-secondary)] transition-transform ${props.rightTrajectoryOpen ? '' : '-rotate-180'}`} />
              </button>
              {props.rightTrajectoryOpen && (
                <div className="px-4 pb-4 pt-0">
                  <button
                    type="button"
                    onClick={() => props.setTrajectoryOpen(true)}
                    className="w-full flex items-center gap-1.5 py-2 text-left transition-colors hover:bg-[var(--c-surface-2)] rounded-[var(--radius-control)]"
                  >
                    <span className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/70">
                      查看轨迹面板
                    </span>
                    <img
                      src={iconOpen}
                      alt=""
                      width={12}
                      height={12}
                      className="shrink-0 object-contain opacity-70"
                    />
                  </button>
                </div>
              )}
            </div>
          </div>
          )}
        </>
      ) : null}
    </aside>
  )
}
