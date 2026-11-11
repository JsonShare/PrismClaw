import { ErrorBoundary } from '../../../components/ui/ErrorBoundary'
import { ChatHeader } from '../../../components/chat/ChatHeader'
import ChatArea from '../../../components/chat/ChatArea'
import type { FileChange, Workspace } from '../../../types'
import type { ConversationController } from '../../../hooks/chat/useConversationController'

interface ChatSectionProps {
  sidebarCollapsed: boolean
  onToggleSidebar: () => void
  onNewSession: () => void
  title: string
  isLanding: boolean
  showPanelButton: boolean
  taskFilesOpen: boolean
  onTogglePanel: () => void
  token: string
  workspaceId: string
  layout: 'landing' | 'default'
  workspaceUploadDir: string
  workspaces: Workspace[]
  onSelectWorkspace: (id: string) => void
  onCreateServerWorkspace: (name: string) => Promise<void>
  onUploadServerFiles: (files: File[]) => Promise<void>
  onRemoveServerWorkspace: (id: string) => Promise<void>
  isWorkspacesLoading: boolean
  onOpenReviewInPanel?: (change: FileChange) => void
  conversationId: string
  // 会话级状态/动作不再逐层透传：ChatArea 自行订阅 store，交互统一走 controller。
  controller: ConversationController
  prefillInput: { text: string; nonce: number } | null
  onOpenFilePreview: (path: string) => void
  showLandingHome: boolean
}

export function ChatSection(props: ChatSectionProps) {
  return (
    <div className="flex-1 min-w-0 min-h-0 flex flex-col relative app-panel">
      <ChatHeader
        sidebarCollapsed={props.sidebarCollapsed}
        onToggleSidebar={props.onToggleSidebar}
        onNewSession={props.onNewSession}
        title={props.title}
        isLanding={props.isLanding}
        showPanelButton={props.showPanelButton}
        taskFilesOpen={props.taskFilesOpen}
        onTogglePanel={props.onTogglePanel}
      />

      <ErrorBoundary>
        <ChatArea
          token={props.token}
          workspaceId={props.workspaceId}
          layout={props.layout}
          workspaceUploadDir={props.workspaceUploadDir}
          workspaces={props.workspaces}
          onSelectWorkspace={props.onSelectWorkspace}
          onCreateServerWorkspace={props.onCreateServerWorkspace}
          onUploadServerFiles={props.onUploadServerFiles}
          onRemoveServerWorkspace={props.onRemoveServerWorkspace}
          isWorkspacesLoading={props.isWorkspacesLoading}
          onOpenReviewInPanel={props.onOpenReviewInPanel}
          conversationId={props.conversationId}
          controller={props.controller}
          prefillInput={props.prefillInput}
          onOpenFilePreview={props.onOpenFilePreview}
        />
      </ErrorBoundary>
    </div>
  )
}
