import { useCallback, useEffect, useState } from 'react'
import { useEditor, EditorContent } from '@tiptap/react'
import type { JSONContent } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import Underline from '@tiptap/extension-underline'
import { OP_LABEL } from '../../modals/FileReviewModal'
import type { OfficeChangeDetail } from '../../../types'
import { InsertionMark } from './tiptap/InsertionMark'
import { DeletionMark } from './tiptap/DeletionMark'

interface OfficeReviewEditorProps {
  /** Backend-synthesized review document as TipTap JSON, with
   *  insertion/deletion marks whose changeId === index into `details`. */
  doc: JSONContent | null
  details: OfficeChangeDetail[]
}

const EMPTY_DOC: JSONContent = { type: 'doc', content: [{ type: 'paragraph' }] }

const REVIEW_STYLES = `
.office-review-doc [data-change-id] {
  border-radius: 3px;
  transition: background 0.15s ease;
}
.office-review-doc [data-change-id]:hover {
  background: rgba(99, 102, 241, 0.10);
  cursor: pointer;
}
.tracked-change {
  border-radius: 0.3rem;
  transition: background-color 150ms ease, color 150ms ease, box-shadow 150ms ease;
  cursor: pointer;
}
.tracked-change--insertion {
  background: color-mix(in srgb, var(--c-success) 18%, transparent);
  color: var(--c-success);
  box-shadow: inset 0 -2px 0 var(--c-success);
}
.tracked-change--insertion:hover {
  background: color-mix(in srgb, var(--c-success) 28%, transparent);
  box-shadow: inset 0 -2px 0 var(--c-success), 0 0 0 1px color-mix(in srgb, var(--c-success) 30%, transparent);
}
.tracked-change--deletion {
  background: color-mix(in srgb, var(--c-error) 14%, transparent);
  color: var(--c-error);
  text-decoration-line: line-through;
  text-decoration-color: var(--c-error);
  text-decoration-thickness: 2px;
}
.tracked-change--deletion:hover {
  background: color-mix(in srgb, var(--c-error) 24%, transparent);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--c-error) 25%, transparent);
}
.office-sidebar-row[data-active="1"] {
  outline: 1px solid color-mix(in srgb, var(--c-accent) 50%, transparent);
}
`

export function OfficeReviewEditor({ doc, details }: OfficeReviewEditorProps) {
  const [activeIdx, setActiveIdx] = useState<number | null>(null)

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const editor = useEditor({
    // Cast to `any[]` works around a pnpm peer-resolution artifact:
    //   InsertionMark/DeletionMark import Mark from the "hoisted" copy of
    //   @tiptap/core (3.27.3) while `@tiptap/react` pulls a nested copy
    //   (3.30.1).  The two Mark types are structurally identical but TS
    //   sees them as distinct (private `commandManager`).  Runtime use is
    //   unaffected: useEditor() just forwards the array to
    //   `new Editor({ extensions })`.  Remove the cast once all
    //   @tiptap/* packages are pinned to the same version.
    extensions: [StarterKit, Underline, InsertionMark, DeletionMark] as any[],
    content: doc ?? EMPTY_DOC,
    editable: false,
    editorProps: {
      attributes: { class: 'office-review-doc' },
    },
  })

  // Sync content when a new review document arrives.
  useEffect(() => {
    if (editor && doc) editor.commands.setContent(doc)
  }, [editor, doc])

  const scrollToChange = useCallback(
    (i: number) => {
      const el = editor?.view.dom.querySelector(`[data-change-id="${i}"]`)
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
        setActiveIdx(i)
        window.setTimeout(() => setActiveIdx((v) => (v === i ? null : v)), 1200)
      }
    },
    [editor],
  )

  return (
    <div className="flex-1 min-h-0 flex">
      <style>{REVIEW_STYLES}</style>
      {/* 文档区 */}
      <div className="flex-1 min-h-0 overflow-y-auto px-6 py-5 bg-white">
        <div className="max-w-[760px] mx-auto leading-relaxed text-[length:var(--fs-md)] text-gray-800 [word-break:break-word]">
          {doc && editor ? (
            <EditorContent editor={editor} />
          ) : (
            <div className="text-[length:var(--fs-sm)] text-gray-400 py-10 text-center">
              无可用审阅内容
            </div>
          )}
        </div>
      </div>

      {/* 侧边栏：改动清单（仅展示差异，决策在底部全局操作） */}
      <aside className="w-72 shrink-0 border-l border-[var(--c-border)]/30 overflow-y-auto bg-white/80">
        <div className="px-3 py-2.5 text-[length:var(--fs-sm)] font-medium text-[var(--c-text-secondary)]/70 sticky top-0 bg-white/90 backdrop-blur-sm">
          改动清单（{details.length}）
        </div>
        <div className="p-2.5 space-y-2">
          {details.map((d, i) => {
            const label = OP_LABEL[d.op] ?? d.op
            const isDelete = d.op.startsWith('delete')
            const isInsert = d.op.startsWith('insert')
            const rowCls =
              'office-sidebar-row rounded-[var(--radius-md)] border px-3 py-2 transition-colors cursor-pointer ' +
              'border-[var(--c-border)]/20 bg-[var(--c-surface-2)]/40 hover:border-[var(--c-accent)]/30' +
              (activeIdx === i ? ' outline outline-1 outline-[var(--c-accent)]/40' : '')
            return (
              <div
                key={i}
                className={rowCls}
                data-active={activeIdx === i ? '1' : '0'}
                onMouseEnter={() => setActiveIdx(i)}
                onMouseLeave={() => setActiveIdx((v) => (v === i ? null : v))}
                onClick={() => scrollToChange(i)}
              >
                <div className="flex items-center gap-2 mb-1">
                  <span className="font-mono text-[length:var(--fs-sm)] text-[var(--c-accent)]/70 bg-[var(--c-accent)]/8 px-1.5 py-0.5 rounded-[var(--radius-control)]">
                    {d.ref}
                  </span>
                  <span className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/60">{label}</span>
                </div>
                {isDelete ? (
                  <p className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/50 line-through">
                    {d.before}
                  </p>
                ) : isInsert ? (
                  <p className="text-[length:var(--fs-sm)] text-[var(--c-success)] bg-[var(--c-success-soft)] rounded-[var(--radius-control)] px-2 py-1">
                    {d.after}
                  </p>
                ) : (
                  <div className="space-y-1">
                    <p className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/50 line-through">
                      {d.before}
                    </p>
                    <p className="text-[length:var(--fs-sm)] text-[var(--c-success)] bg-[var(--c-success-soft)] rounded-[var(--radius-control)] px-2 py-1">
                      {d.after}
                    </p>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </aside>
    </div>
  )
}
