/**
 * TrajectoryInspector — Cell 驱动的详情面板。
 *
 * 由选中的 TrajectoryCell（per-conversation-event）驱动，
 * 独立于 RequestAuditRecord，避免 cells[] 和 records[] 不同数组导致的索引错位。
 *
 * Tab 结构：
 *   • Markdown kind（user/context/message/compacted）：原文 + 预览
 *   • Tool kind：概要 / 参数 / 结果 / Schema
 *   • Tokens tab（assistant）：本次 token 分解 + 会话累计
 *   • Timing tab（assistant）：TTFT/Decoding/Total/Throughput
 *   • 调用快照 tab（assistant + matching record）：4 层快照
 */
import { useState } from 'react'
import {
  ChevronDown, ChevronRight, CheckCircle2, XCircle, Clock,
  Cpu, Zap, Layers, Wrench, FileJson, Eye,
} from 'lucide-react'
import MarkdownRenderer from '../chat/MarkdownRenderer'
import { HairlineDivider } from '../ui/HairlineDivider'
import type { TrajectoryCell, TrajectoryCellKind, RequestAuditRecord } from '../../types'

/** 会话累计 usage 的 5 类 token 桶。 */
export interface TrajectoryCumulativeUsage {
  input: number
  cacheRead: number
  cacheWrite: number
  output: number
  reasoning: number
}

interface TrajectoryInspectorProps {
  cell: TrajectoryCell | null
  /** 匹配的 per-iteration record（assistant cell 的 4 层快照用）。 */
  record: RequestAuditRecord | null
  /** 截止到当前 cell 的会话累计 usage（assistant）。 */
  cumulativeUsage?: TrajectoryCumulativeUsage | null
}

const KIND_LABEL: Record<TrajectoryCellKind, string> = {
  system: 'SYSTEM',
  user: 'USER',
  context: 'CONTEXT',
  compacted: 'COMPACTED',
  message: 'ASSISTANT',
  tool: 'TOOL',
  subtool: 'SUBTOOL',
}

function formatMs(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 10_000) return `${(ms / 1000).toFixed(2)}s`
  return `${Math.round(ms / 1000)}s`
}

function formatTokens(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—'
  if (n < 1000) return String(n)
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`
  return `${(n / 1_000_000).toFixed(2)}M`
}

type TabId = 'content' | 'preview' | 'payload' | 'result' | 'tokens' | 'timing' | 'schema' | 'snapshot'

/** 从 cell 中提取 Markdown 源文本。 */
function markdownSource(cell: TrajectoryCell): string | null {
  if (cell.kind === 'user' || cell.kind === 'context' || cell.kind === 'system') {
    return cell.input_detail
  }
  if (cell.kind === 'message' || cell.kind === 'compacted') {
    return cell.output_detail ?? cell.thinking_detail
  }
  return null
}

/** 根据 cell kind 决定可用的 tabs。 */
function availableTabs(cell: TrajectoryCell, hasRecord: boolean): { id: TabId; label: string; icon: typeof Cpu }[] {
  const tabs: { id: TabId; label: string; icon: typeof Cpu }[] = []
  if (cell.kind === 'tool' || cell.kind === 'subtool') {
    tabs.push(
      { id: 'content', label: '概要', icon: FileJson },
      ...(cell.input_detail ? [{ id: 'payload' as const, label: '参数', icon: FileJson }] : []),
      ...(cell.output_detail ? [{ id: 'result' as const, label: '结果', icon: FileJson }] : []),
      { id: 'schema', label: 'Schema', icon: Wrench },
    )
    return tabs
  }
  // Markdown kind：原文 + 渲染预览
  tabs.push({ id: 'content', label: '原文', icon: FileJson })
  if (markdownSource(cell)) tabs.push({ id: 'preview', label: '预览', icon: Eye })
  if (cell.kind === 'message') {
    tabs.push(
      { id: 'tokens', label: 'Token', icon: Zap },
      { id: 'timing', label: '耗时', icon: Clock },
    )
    if (hasRecord) tabs.push({ id: 'snapshot', label: '调用快照', icon: Layers })
  }
  return tabs
}

export function TrajectoryInspector({ cell, record, cumulativeUsage }: TrajectoryInspectorProps) {
  const [activeTab, setActiveTab] = useState<TabId>('content')

  if (!cell) {
    return (
      <div className="flex-1 min-w-[320px] border-l border-[var(--c-border)] bg-[var(--c-surface)] flex items-center justify-center">
        <div className="text-center text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]">
          <div className="mb-2">
            <Layers size={28} className="mx-auto opacity-30" />
          </div>
          选择一个 cell 查看详情
        </div>
      </div>
    )
  }

  const hasError = !!cell.error || cell.is_error
  const hasRecord = record != null
  const tabs = availableTabs(cell, hasRecord)
  // 如果当前 activeTab 对该 kind 不可用，回退到第一个
  const tab = tabs.some(t => t.id === activeTab) ? activeTab : tabs[0].id

  const durationMs = cell.completed_at_ms != null
    ? cell.completed_at_ms - cell.started_at_ms
    : null

  return (
    <div className="flex-1 min-w-[340px] max-w-[560px] border-l border-[var(--c-border)] bg-[var(--c-surface)] flex flex-col overflow-hidden">
      {/* Header — kind + turn + seq + status */}
      <div className="px-4 py-3 border-b border-[var(--c-border)]">
        <div className="flex items-center gap-2 mb-1">
          {hasError ? (
            <span className="inline-flex items-center gap-1 text-[length:var(--fs-xs)] font-medium text-[var(--c-danger, #dc2626)]">
              <XCircle size={12} /> 错误
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-[length:var(--fs-xs)] font-medium text-[var(--c-success, #16a34a)]">
              <CheckCircle2 size={12} /> 完成
            </span>
          )}
          <span className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]">
            Turn {cell.turn} · Seq {cell.seq}
            {cell.iteration != null && ` · Iter ${cell.iteration}`}
          </span>
        </div>
        <div className="text-[length:var(--fs-md)] font-semibold text-[var(--c-text)]">
          {KIND_LABEL[cell.kind]}
          {cell.kind === 'message' && cell.model && (
            <span className="ml-2 text-[length:var(--fs-xs)] font-normal text-[var(--c-text-secondary)]">
              <Cpu size={11} className="inline mr-1 opacity-70" />
              {cell.model}
            </span>
          )}
        </div>
        {cell.kind === 'tool' && (cell.tool_name || cell.tool_calls_summary[0]) && (
          <div className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)] mt-0.5 font-mono truncate">
            {cell.tool_name
              ?? cell.tool_calls_summary[0].match(/^([^(]*)\(/)?.[1]
              ?? cell.tool_calls_summary[0]}
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="flex items-center px-2 py-1 border-b border-[var(--c-border)] bg-[var(--c-surface-2)]">
        {tabs.map((t) => {
          const TabIcon = t.icon
          const isActive = tab === t.id
          return (
            <button
              key={t.id}
              type="button"
              onClick={() => setActiveTab(t.id)}
              className={`flex items-center gap-1 px-2 py-1 rounded-[var(--radius-control)] text-[length:var(--fs-xs)] font-medium transition-colors cursor-pointer
                ${isActive
                  ? 'bg-[var(--c-surface)] text-[var(--c-text)]'
                  : 'text-[var(--c-text-secondary)] hover:text-[var(--c-text)] hover:bg-[var(--c-surface)]'
                }`}
            >
              <TabIcon size={11} />
              {t.label}
            </button>
          )
        })}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4 text-[length:var(--fs-xs)]">
        {tab === 'content' && <ContentTab cell={cell} />}
        {tab === 'preview' && <PreviewTab cell={cell} />}
        {tab === 'payload' && <PayloadTab cell={cell} />}
        {tab === 'result' && <ResultTab cell={cell} />}
        {tab === 'tokens' && <TokensTab cell={cell} cumulativeUsage={cumulativeUsage} />}
        {tab === 'timing' && <TimingTab cell={cell} durationMs={durationMs} />}
        {tab === 'schema' && <SchemaTab cell={cell} />}
        {tab === 'snapshot' && record && <SnapshotTab record={record} />}
      </div>
    </div>
  )
}

/* ─── Tab: Content — tool 概要 / markdown 原文全文 ─── */

function ContentTab({ cell }: { cell: TrajectoryCell }) {
  const isTool = cell.kind === 'tool' || cell.kind === 'subtool'
  const isAssistant = cell.kind === 'message' || cell.kind === 'compacted'
  const isInput = cell.kind === 'user' || cell.kind === 'system' || cell.kind === 'context'

  // Tool kind 在这里展示概要；完整 payload/result 放到各自的 tab
  if (isTool) {
    return (
      <div className="space-y-3">
        {cell.error && (
          <div className="p-2.5 rounded-[var(--radius-control)] bg-[var(--c-danger, #dc2626)]/10 border border-[var(--c-danger, #dc2626)]/30">
            <div className="font-medium text-[var(--c-danger, #dc2626)] mb-0.5">错误</div>
            <div className="text-[length:var(--fs-xs)] text-[var(--c-text)] whitespace-pre-wrap break-all">
              {cell.error}
            </div>
          </div>
        )}
        {cell.tool_calls_summary.length > 0 && (
          <DetailBlock label="调用摘要" icon={<Wrench size={12} />}>
            <pre className="text-[length:var(--fs-xs)] text-[var(--c-text)] font-mono whitespace-pre-wrap break-all">
              {cell.tool_calls_summary.join('\n')}
            </pre>
          </DetailBlock>
        )}
        {!cell.input_detail && !cell.output_detail && cell.content_preview && (
          <DetailBlock label="内容预览" icon={<FileJson size={12} />}>
            <pre className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)] whitespace-pre-wrap break-all">
              {cell.content_preview}
            </pre>
          </DetailBlock>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {/* Error banner */}
      {cell.error && (
        <div className="p-2.5 rounded-[var(--radius-control)] bg-[var(--c-danger, #dc2626)]/10 border border-[var(--c-danger, #dc2626)]/30">
          <div className="font-medium text-[var(--c-danger, #dc2626)] mb-0.5">错误</div>
          <div className="text-[var(--fs-xs)] text-[var(--c-text)] whitespace-pre-wrap break-all">
            {cell.error}
          </div>
        </div>
      )}

      {/* Tool calls (assistant with tool_calls) */}
      {isAssistant && cell.tool_calls_summary.length > 0 && (
        <DetailBlock label="工具调用" icon={<Wrench size={12} />}>
          <pre className="text-[length:var(--fs-xs)] text-[var(--c-text)] font-mono whitespace-pre-wrap break-all">
            {cell.tool_calls_summary.join('\n')}
          </pre>
        </DetailBlock>
      )}

      {/* Thinking detail (assistant reasoning) */}
      {isAssistant && cell.thinking_detail && (
        <DetailBlock label="推理过程" icon={<Zap size={12} />}>
          <pre className="text-[length:var(--fs-xs)] text-[var(--c-text-tertiary)] italic whitespace-pre-wrap break-all">
            {cell.thinking_detail}
          </pre>
        </DetailBlock>
      )}

      {/* Input detail */}
      {isInput && cell.input_detail && (
        <DetailBlock label="输入内容" icon={<FileJson size={12} />}>
          <pre className="text-[length:var(--fs-xs)] text-[var(--c-text)] font-mono whitespace-pre-wrap break-all">
            {cell.input_detail}
          </pre>
        </DetailBlock>
      )}

      {/* Output detail */}
      {isAssistant && cell.output_detail && (
        <DetailBlock label="输出内容" icon={<FileJson size={12} />}>
          <pre className={`text-[length:var(--fs-xs)] whitespace-pre-wrap break-all font-mono
            ${cell.is_error ? 'text-[var(--c-danger, #dc2626)]' : 'text-[var(--c-text)]'}`}
          >
            {cell.output_detail}
          </pre>
        </DetailBlock>
      )}

      {/* Fallback: content_preview */}
      {!cell.input_detail && !cell.output_detail && cell.content_preview && (
        <DetailBlock label="内容预览" icon={<FileJson size={12} />}>
          <pre className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)] whitespace-pre-wrap break-all">
            {cell.content_preview}
          </pre>
        </DetailBlock>
      )}
    </div>
  )
}

/* ─── Tab: Preview (渲染后的 markdown) ─── */

function PreviewTab({ cell }: { cell: TrajectoryCell }) {
  const source = markdownSource(cell)
  if (!source) return <p className="text-[var(--c-text-secondary)]">无可渲染内容</p>
  return (
    <div className="rounded-[var(--radius-control)] border border-[var(--c-border)] bg-[var(--c-surface-2)] p-3">
      <MarkdownRenderer content={source} />
    </div>
  )
}

/* ─── Tab: Payload (tool input_detail / 参数) ─── */

function PayloadTab({ cell }: { cell: TrajectoryCell }) {
  if (!cell.input_detail) return <p className="text-[var(--c-text-secondary)]">未记录参数</p>
  return (
    <pre className="text-[length:var(--fs-xs)] text-[var(--c-text)] font-mono whitespace-pre-wrap break-all">
      {cell.input_detail}
    </pre>
  )
}

/* ─── Tab: Result (tool output_detail / 结果) ─── */

function ResultTab({ cell }: { cell: TrajectoryCell }) {
  if (!cell.output_detail) return <p className="text-[var(--c-text-secondary)]">未记录结果</p>
  return (
    <pre className={`text-[length:var(--fs-xs)] whitespace-pre-wrap break-all font-mono
      ${cell.is_error ? 'text-[var(--c-danger, #dc2626)]' : 'text-[var(--c-text)]'}`}
    >
      {cell.output_detail}
    </pre>
  )
}

function DetailBlock({
  label, icon, children,
}: {
  label: string
  icon: React.ReactNode
  children: React.ReactNode
}) {
  const [expanded, setExpanded] = useState(true)
  return (
    <div className="rounded-[var(--radius-control)] bg-[var(--c-surface-2)] border border-[var(--c-border)]">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-1.5 px-2.5 py-1.5 text-left text-[length:var(--fs-xs)] font-medium text-[var(--c-text)] hover:bg-[var(--c-surface)] rounded-t-md transition-colors cursor-pointer"
      >
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {icon}
        {label}
      </button>
      {expanded && (
        <div className="px-2.5 pb-2.5 max-h-72 overflow-auto">{children}</div>
      )}
    </div>
  )
}

/* ─── Tab: Tokens (assistant only / Token 分解 + 会话累计) ─── */

function TokensTab({
  cell,
  cumulativeUsage,
}: {
  cell: TrajectoryCell
  cumulativeUsage?: TrajectoryCumulativeUsage | null
}) {
  const input = cell.usage_input_tokens
  const cacheRead = cell.usage_cache_read_tokens
  const cacheWrite = cell.usage_cache_write_tokens
  const otherInput = input
  const totalInput = (input ?? 0) + (cacheRead ?? 0) + (cacheWrite ?? 0)
  const output = cell.usage_output_tokens
  const reasoning = cell.usage_reasoning_tokens
  const content = output != null && reasoning != null
    ? Math.max(0, output - reasoning)
    : null
  const totalTokens = totalInput + (output ?? 0)

  const breakdown: { label: string; value: number | null; color: string }[] = [
    { label: '输入 (Input)', value: otherInput, color: 'bg-[var(--c-accent)]' },
    { label: '缓存命中 (Cache Read)', value: cacheRead, color: 'bg-[var(--c-success, #16a34a)]' },
    { label: '缓存写入 (Cache Write)', value: cacheWrite, color: 'bg-[var(--c-warning, #d97706)]' },
    { label: '输出 (Output)', value: output, color: 'bg-[var(--c-accent)]/70' },
    { label: '推理 (Reasoning)', value: reasoning, color: 'bg-[var(--c-text-secondary)]' },
    { label: '正文 (Content)', value: content, color: 'bg-[var(--c-text-tertiary)]' },
  ]

  const cumulative = cumulativeUsage == null ? null : [
    { label: '输入合计 (Input)', value: cumulativeUsage.input + cumulativeUsage.cacheRead + cumulativeUsage.cacheWrite },
    { label: '缓存命中 (Cache Read)', value: cumulativeUsage.cacheRead },
    { label: '缓存写入 (Cache Write)', value: cumulativeUsage.cacheWrite },
    { label: '输出 (Output)', value: cumulativeUsage.output },
    { label: '推理 (Reasoning)', value: cumulativeUsage.reasoning },
  ]

  return (
    <div className="space-y-4">
      <div className="flex items-baseline gap-2">
        <span className="text-[length:var(--fs-lg)] font-semibold text-[var(--c-text)]">
          {formatTokens(totalTokens)}
        </span>
        <span className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]">本次请求总 tokens</span>
      </div>
      <div className="space-y-2">
        {breakdown.map((item) => {
          if (item.value == null) return null
          const pct = totalTokens > 0 ? (item.value / totalTokens) * 100 : 0
          return (
            <div key={item.label} className="space-y-0.5">
              <div className="flex items-center justify-between text-[length:var(--fs-xs)]">
                <span className="text-[var(--c-text-secondary)]">{item.label}</span>
                <span className="text-[var(--c-text)] tabular-nums">{formatTokens(item.value)}</span>
              </div>
              <div className="h-1.5 rounded-full bg-[var(--c-surface-2)] overflow-hidden">
                <div className={`h-full rounded-full ${item.color} transition-all`} style={{ width: `${pct}%` }} />
              </div>
            </div>
          )
        })}
      </div>
      {cumulative != null && (
        <div>
          <HairlineDivider className="my-3" />
          <div className="text-[length:var(--fs-xs)] font-medium text-[var(--c-text)] mb-2">
            会话累计 (Session cumulative)
          </div>
          <dl className="space-y-1">
            {cumulative.map((row) => (
              <div key={row.label} className="flex items-center justify-between gap-2">
                <dt className="text-[var(--c-text-secondary)]">{row.label}</dt>
                <dd className="text-[var(--c-text)] tabular-nums">{formatTokens(row.value)}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </div>
  )
}

/* ─── Tab: Timing (assistant only / TTFT·Decoding·Total·Throughput) ─── */

function TimingTab({ cell, durationMs }: { cell: TrajectoryCell; durationMs: number | null }) {
  const ttftMs = cell.ttft_ms
  const decodingMs = cell.decoding_ms
  const total = durationMs ?? (ttftMs != null && decodingMs != null ? ttftMs + decodingMs : null)
  const outputTokens = cell.usage_output_tokens
  const throughput = decodingMs != null && outputTokens != null && decodingMs > 0
    ? outputTokens / (decodingMs / 1000)
    : null

  const rows: { label: string; value: string }[] = [
    { label: '总耗时 (Total)', value: formatMs(total) },
    { label: '首 Token (TTFT)', value: formatMs(ttftMs) },
    { label: '解码 (Decoding)', value: formatMs(decodingMs) },
    { label: '吞吐量 (Throughput)', value: throughput != null ? `${throughput.toFixed(1)} tok/s` : '—' },
  ]

  return (
    <div className="space-y-3">
      {total != null && total > 0 && ttftMs != null && decodingMs != null && (
        <div className="space-y-1">
          <div className="h-3 rounded-full bg-[var(--c-surface-2)] overflow-hidden flex">
            <div
              className="bg-[var(--c-warning, #d97706)]"
              style={{ width: `${(ttftMs / total) * 100}%` }}
              title={`TTFT ${formatMs(ttftMs)}`}
            />
            <div
              className="bg-[var(--c-danger, #dc2626)]"
              style={{ width: `${(decodingMs / total) * 100}%` }}
              title={`Decoding ${formatMs(decodingMs)}`}
            />
          </div>
          <div className="flex items-center gap-3 text-[length:var(--fs-xs)]">
            <span className="inline-flex items-center gap-1">
              <span className="w-2 h-2 rounded-[var(--radius-control)] bg-[var(--c-warning, #d97706)]" /> TTFT
            </span>
            <span className="inline-flex items-center gap-1">
              <span className="w-2 h-2 rounded-[var(--radius-control)] bg-[var(--c-danger, #dc2626)]" /> Decoding
            </span>
          </div>
        </div>
      )}
      <dl className="space-y-1.5 pt-2">
        {rows.map((row) => (
          <div key={row.label} className="flex items-center justify-between gap-2">
            <dt className="text-[var(--c-text-secondary)]">{row.label}</dt>
            <dd className="text-[var(--c-text)] tabular-nums">{row.value}</dd>
          </div>
        ))}
      </dl>
      {cell.finish_reason && (
        <div>
          <HairlineDivider className="my-2" />
          <div className="flex items-center justify-between gap-2">
            <dt className="text-[var(--c-text-secondary)]">完成原因</dt>
            <dd className="text-[var(--c-text)]">{cell.finish_reason}</dd>
          </div>
        </div>
      )}
    </div>
  )
}

/* ─── Tab: Schema (tool only) ─── */

function SchemaTab({ cell }: { cell: TrajectoryCell }) {
  if (!cell.schema_detail) {
    return <p className="text-[var(--c-text-secondary)]">无 schema 信息</p>
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(cell.schema_detail)
  } catch {
    parsed = null
  }
  return (
    <div className="space-y-2">
      <DetailBlock label="Tool Schema" icon={<Wrench size={12} />}>
        <pre className="text-[length:var(--fs-xs)] text-[var(--c-text)] font-mono whitespace-pre-wrap break-all">
          {parsed != null ? JSON.stringify(parsed, null, 2) : cell.schema_detail}
        </pre>
      </DetailBlock>
      {cell.tool_calls_summary.length > 0 && (
        <DetailBlock label="调用记录" icon={<FileJson size={12} />}>
          <pre className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)] font-mono whitespace-pre-wrap break-all">
            {cell.tool_calls_summary.join('\n')}
          </pre>
        </DetailBlock>
      )}
    </div>
  )
}

/* ─── Tab: 调用快照 (assistant + matching record) ─── */

function SnapshotTab({ record }: { record: RequestAuditRecord }) {
  const layers = [
    {
      label: '① Projector', count: record.projector_msg_count,
      roles: record.projector_msg_roles, head: record.projector_head, tail: record.projector_tail,
    },
    {
      label: '② Assemble', count: record.assemble_entry_msg_count,
      roles: record.assemble_entry_roles, head: record.assemble_entry_head, tail: record.assemble_entry_tail,
    },
    {
      label: '③ Pipeline', count: record.pipeline_out_msg_count,
      roles: record.pipeline_out_roles, head: record.pipeline_out_head, tail: record.pipeline_out_tail,
      budgetSummary: record.pipeline_out_budget_summary,
      compressorsUsed: record.pipeline_out_compressors_used,
      compactionHappened: record.pipeline_out_compaction_happened,
    },
    {
      label: '④ Packer', count: record.packer_final_msg_count,
      roles: record.packer_final_roles, head: record.packer_final_head, tail: record.packer_final_tail,
    },
  ]

  return (
    <div className="space-y-3">
      {record.projector_input_order_count > 0 && (
        <div className="p-2 rounded-[var(--radius-control)] bg-[var(--c-surface-2)] border border-[var(--c-border)]">
          <div className="text-[length:var(--fs-xs)] font-medium text-[var(--c-text)]">
            Projector Input (Tree)
          </div>
          <div className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)] mt-0.5">
            {record.projector_input_order_count} entries · [{record.projector_input_entry_types.join(', ')}]
          </div>
        </div>
      )}
      {layers.map((layer) => (
        <LayerBlock key={layer.label} {...layer} />
      ))}
    </div>
  )
}

function LayerBlock({
  label, count, roles, head, tail,
  budgetSummary, compressorsUsed, compactionHappened,
}: {
  label: string
  count: number
  roles: string[]
  head: string[]
  tail: string[]
  budgetSummary?: Record<string, number>
  compressorsUsed?: string[]
  compactionHappened?: boolean
}) {
  if (count === 0) {
    return (
      <div className="p-2 rounded-[var(--radius-control)] bg-[var(--c-surface-2)]/50 border border-[var(--c-border)]/50">
        <div className="flex items-center gap-2">
          <span className="text-[length:var(--fs-xs)] font-medium text-[var(--c-text)]">{label}</span>
          <span className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]">空</span>
        </div>
      </div>
    )
  }
  return (
    <div className="p-2.5 rounded-[var(--radius-control)] bg-[var(--c-surface-2)] border border-[var(--c-border)]">
      <div className="flex items-center gap-2 mb-1">
        <span className="text-[length:var(--fs-xs)] font-medium text-[var(--c-text)]">{label}</span>
        <span className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]">{count} 条</span>
        {roles.length > 0 && (
          <span className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)]">[{roles.join(', ')}]</span>
        )}
        {compactionHappened && (
          <span className="px-1 py-0.5 rounded-[var(--radius-control)] text-[length:var(--fs-xs)] bg-[var(--c-warning, #d97706)]/15 text-[var(--c-warning, #d97706)]">压缩</span>
        )}
      </div>
      <div className="space-y-0.5">
        {head.map((s, i) => (
          <div key={`h${i}`} className="text-[length:var(--fs-xs)] text-[var(--c-text)] font-mono whitespace-pre-wrap break-all opacity-70">{s}</div>
        ))}
        {tail.length > 0 && head.length < count && (
          <div className="text-[length:var(--fs-xs)] text-[var(--c-text-secondary)] opacity-50">…</div>
        )}
        {tail.map((s, i) => (
          <div key={`t${i}`} className="text-[length:var(--fs-xs)] text-[var(--c-text)] font-mono whitespace-pre-wrap break-all opacity-70">{s}</div>
        ))}
      </div>
      {budgetSummary && Object.keys(budgetSummary).length > 0 && (
        <div className="mt-2">
          <HairlineDivider className="my-2" />
          <div className="text-[length:var(--fs-xs)] font-medium text-[var(--c-text-secondary)] mb-1">预算摘要</div>
          <div className="flex flex-wrap gap-1">
            {Object.entries(budgetSummary).map(([k, v]) => (
              <span key={k} className="px-1.5 py-0.5 rounded-[var(--radius-control)] text-[length:var(--fs-xs)] bg-[var(--c-surface)] text-[var(--c-text-secondary)] tabular-nums">{k}: {v}</span>
            ))}
          </div>
        </div>
      )}
      {compressorsUsed && compressorsUsed.length > 0 && (
        <div className="mt-1.5">
          <div className="text-[length:var(--fs-xs)] font-medium text-[var(--c-text-secondary)] mb-0.5">压缩器</div>
          <div className="flex flex-wrap gap-1">
            {compressorsUsed.map((c) => (
              <span key={c} className="px-1.5 py-0.5 rounded-[var(--radius-control)] text-[length:var(--fs-xs)] bg-[var(--c-warning, #d97706)]/10 text-[var(--c-warning, #d97706)]">{c}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
