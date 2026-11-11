import type { FileChangeKind, OfficeChangeDetail } from '../../types'

export const KIND_LABEL: Record<FileChangeKind, string> = {
  text: '文本',
  code: '代码',
  config: '配置',
  docx: 'Word',
  xlsx: 'Excel',
  pptx: 'PPT',
  image: '图片',
  binary: '二进制',
}

export const OP_LABEL: Record<string, string> = {
  replace_paragraph: '改写段落',
  insert_after: '插入段落',
  delete_paragraph: '删除段落',
  replace_run: '改写文本',
  set_cell: '改单元格',
  insert_row: '插入行',
  delete_row: '删除行',
  insert_column: '插入列',
  delete_column: '删除列',
  set_text_frame: '改文本框',
  delete_text_frame: '删除文本框',
}

/**
 * 文件审阅弹窗 — 文档修订模式交互范式。
 * 正文区按 kind 自动选择文档模式（prose/内联高亮）或代码模式（等宽行级 diff）。
 * 办公文件展示逐处改动差异；底部操作栏仅 Reject All / Confirm All（全有或全无）。
 */
export function OfficeChangeRow({ d }: { d: OfficeChangeDetail }) {
  const label = OP_LABEL[d.op] ?? d.op
  // Category-based rendering works uniformly for structured office rows:
  //   delete* → show only the removed content (strikethrough)
  //   insert* → show only the added content (emerald)
  //   everything else → before (strikethrough) + after (emerald)
  const isDelete = d.op.startsWith('delete')
  const isInsert = d.op.startsWith('insert')

  return (
    <div className="rounded-[var(--radius-md)] border px-3.5 py-2.5 border-[var(--c-border)]/20 bg-[var(--c-surface-2)]/40">
      <div className="flex items-center gap-2 mb-1.5">
        <span className="font-mono text-[length:var(--fs-sm)] text-[var(--c-accent)]/70 bg-[var(--c-accent)]/8 px-1.5 py-0.5 rounded-[var(--radius-control)]">
          {d.ref}
        </span>
        <span className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/60">{label}</span>
      </div>
      {isDelete ? (
        <p className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/50 line-through">{d.before}</p>
      ) : isInsert ? (
        <p className="text-[length:var(--fs-sm)] text-[var(--c-success)] bg-[var(--c-success-soft)] rounded-[var(--radius-control)] px-2 py-1">{d.after}</p>
      ) : (
        <div className="space-y-1">
          <p className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/50 line-through">{d.before}</p>
          <p className="text-[length:var(--fs-sm)] text-[var(--c-success)] bg-[var(--c-success-soft)] rounded-[var(--radius-control)] px-2 py-1">{d.after}</p>
        </div>
      )}
    </div>
  )
}
