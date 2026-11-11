import type { DiffHunk, FileChangeKind } from '../../types'

/** 根据文件类型自动选择渲染模式 */
const CODE_KINDS: FileChangeKind[] = ['code', 'config']

function isCodeLike(kind: FileChangeKind): boolean {
  return CODE_KINDS.includes(kind)
}

/* ─── 文档模式（prose 字体，内联高亮）────────────────────
   适用于 text / docx / xlsx / pptx 等"非代码"文件。
   模仿 Word「修订模式」/ Google Docs「建议编辑」的视觉效果：
   - equal   → 正常流式文本，浅灰淡化上下文
   - insert  → 绿底高亮，内联在段落中
   - delete  → 灰淡 + 删除线，内联在段落中
*/
function DocumentDiffViewer({ hunks }: { hunks: DiffHunk[] }) {
  return (
    <div className="rounded-[var(--radius-md)] border border-[var(--c-border)]/40 bg-[var(--c-surface)]/60 overflow-hidden text-[length:var(--fs-md)] leading-[2] text-[var(--c-text)]">
      {hunks.map((h, hi) => {
        if (h.type === 'equal') {
          return (
            <div key={hi} className="px-4 py-0.5 text-[var(--c-text-secondary)]/50">
              {h.lines.map((l, i) => (
                <div key={i} className="whitespace-pre-wrap break-all">{l || ' '}</div>
              ))}
            </div>
          )
        }
        const isIns = h.type === 'insert'
        return (
          <div
            key={hi}
            className={
              'px-4 py-0.5 ' +
              (isIns
                ? 'bg-[var(--c-success-soft)] text-[var(--c-success)]'
                : 'text-[var(--c-danger)] line-through decoration-[var(--c-danger)]')
            }
          >
            {h.lines.map((l, i) => (
              <div key={i} className="whitespace-pre-wrap break-all">{l || ' '}</div>
            ))}
          </div>
        )
      })}
    </div>
  )
}

/* ─── 代码模式（等宽字体，行级 diff）─────────────────────
   适用于 code / config 等程序文件。
   类似 git diff / GitHub PR diff 的经典样式。
*/
function CodeDiffViewer({ hunks }: { hunks: DiffHunk[] }) {
  return (
    <div className="rounded-[var(--radius-md)] border border-[var(--c-border)]/50 bg-[var(--c-surface)]/40 overflow-hidden font-mono text-[length:var(--fs-sm)] leading-[1.7]">
      {hunks.map((h, hi) => {
        if (h.type === 'equal') {
          return (
            <div
              key={hi}
              className="px-3 py-0.5 text-[var(--c-text-secondary)]/35 whitespace-pre-wrap break-all"
            >
              {h.lines.map((l, i) => (
                <div key={i}>{l || ' '}</div>
              ))}
            </div>
          )
        }
        const isIns = h.type === 'insert'
        return (
          <div
            key={hi}
            className={
              'px-3 py-0.5 whitespace-pre-wrap break-all ' +
              (isIns
                ? 'bg-[var(--c-success-soft)] text-[var(--c-success)]'
                : 'bg-[var(--c-danger-soft)] text-[var(--c-danger)] line-through decoration-[var(--c-danger)]')
            }
          >
            {h.lines.map((l, i) => (
              <div key={i}>
                <span className="select-none opacity-60 mr-2">{isIns ? '+' : '−'}</span>
                {l || ' '}
              </div>
            ))}
          </div>
        )
      })}
    </div>
  )
}

/**
 * 统一 diff 渲染器入口。
 * 根据 kind 自动选择文档模式或代码模式；
 * 也可通过 mode 强制覆盖。
 * truncated=true 时显示截断提示。
 */
export function DiffViewer({
  hunks,
  kind,
  mode,
  truncated,
}: {
  hunks: DiffHunk[]
  kind?: FileChangeKind
  mode?: 'document' | 'code'
  truncated?: boolean
}) {
  const resolved =
    mode ?? (kind && isCodeLike(kind) ? 'code' : 'document')
  return (
    <>
      {truncated && (
        <div className="mb-2 px-3 py-1.5 rounded-[var(--radius-control)] text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/70 bg-[var(--c-surface-2)]/50 border border-[var(--c-border)]/20">
          差异过大，已截断显示。未变更行已折叠，仅保留变更附近上下文。
        </div>
      )}
      {resolved === 'code' ? <CodeDiffViewer hunks={hunks} /> : <DocumentDiffViewer hunks={hunks} />}
    </>
  )
}
