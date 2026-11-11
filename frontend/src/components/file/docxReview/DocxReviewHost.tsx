import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react'
import { Loader2 } from 'lucide-react'
import { DocxEditor, type DocxEditorRef } from '@eigenpal/docx-editor-react'
import { acceptAllChanges, rejectAllChanges } from '@eigenpal/docx-editor-core/prosemirror/commands'
import '@eigenpal/docx-editor-react/styles.css'
import { docxNativeReviewArtifactUrl } from '../../../lib/file/fileReview'

export interface DocxReviewHostRef {
  acceptAll: () => void
  rejectAll: () => void
  save: () => Promise<ArrayBuffer>
}

interface DocxReviewHostProps {
  conversationId: string
  token: string
  reviewDocxRef: string
  fileName: string
}

export const DocxReviewHost = forwardRef<DocxReviewHostRef, DocxReviewHostProps>(
  function DocxReviewHost({ conversationId, token, reviewDocxRef, fileName }, ref) {
    const editorRef = useRef<DocxEditorRef | null>(null)
    const [buffer, setBuffer] = useState<ArrayBuffer | null>(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState<string | null>(null)

    useEffect(() => {
      let cancelled = false
      setLoading(true)
      setError(null)
      setBuffer(null)

      void (async () => {
        try {
          const res = await fetch(
            docxNativeReviewArtifactUrl(conversationId, reviewDocxRef, fileName),
            {
              headers: { Authorization: `Bearer ${token}` },
              cache: 'no-store',
            },
          )
          if (!res.ok) {
            const text = await res.text().catch(() => '')
            throw new Error(text || `HTTP ${res.status}`)
          }
          const nextBuffer = await res.arrayBuffer()
          if (!cancelled) setBuffer(nextBuffer)
        } catch (e) {
          if (!cancelled) {
            setError(e instanceof Error ? e.message : String(e))
          }
        } finally {
          if (!cancelled) setLoading(false)
        }
      })()

      return () => {
        cancelled = true
      }
    }, [conversationId, reviewDocxRef, fileName, token])

    useImperativeHandle(ref, () => ({
      acceptAll() {
        const view = editorRef.current?.getEditorRef()?.getView()
        if (!view) throw new Error('DOCX editor is not ready')
        if (!acceptAllChanges()(view.state, view.dispatch)) {
          throw new Error('Failed to accept all tracked changes')
        }
      },
      rejectAll() {
        const view = editorRef.current?.getEditorRef()?.getView()
        if (!view) throw new Error('DOCX editor is not ready')
        if (!rejectAllChanges()(view.state, view.dispatch)) {
          throw new Error('Failed to reject all tracked changes')
        }
      },
      async save() {
        const payload = await editorRef.current?.save()
        if (!payload) throw new Error('Failed to export reviewed DOCX')
        return payload
      },
    }), [])

    if (loading) {
      return (
        <div className="h-full flex items-center justify-center gap-2 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/70">
          <Loader2 size={16} className="animate-spin" />
          加载 DOCX 审阅内容…
        </div>
      )
    }

    if (error || !buffer) {
      return (
        <div className="h-full flex items-center justify-center p-5">
          <div className="text-[length:var(--fs-sm)] text-[var(--c-error)] bg-[color-mix(in_srgb,var(--c-error)_10%,transparent)] rounded-[var(--radius-md)] px-3.5 py-2.5 leading-relaxed border border-[color-mix(in_srgb,var(--c-error)_30%,transparent)]">
            原生 DOCX 审阅加载失败：{error ?? '未知错误'}
          </div>
        </div>
      )
    }

    return (
      <div className="h-full overflow-hidden bg-white">
        <DocxEditor
          ref={editorRef}
          documentBuffer={buffer}
          author="PrismClaw User"
          readOnly
          mode="suggesting"
          showToolbar={false}
          showFileOpen={false}
          showOutline={false}
          showOutlineButton={false}
          className="h-full"
        />
      </div>
    )
  },
)
