import { useEffect, useMemo, useRef, useState } from 'react'
import {
  CheckCircle2,
  ChevronDown,
  Clock,
  FilePenLine,
  FileSearch,
  FileText,
  Globe,
  HelpCircle,
  Loader2,
  Search,
  SquareTerminal,
  Terminal,
  XCircle,
} from 'lucide-react'
import type { TimelineNode } from '../../types'
import { buildStepRows, type IconType, type StepRow } from '../../lib/chat/stepRows'
import { domainOf, extractArgObject, parseWebSearchJson } from '../../lib/chat/timelineParse'
import SearchResults from './SearchResults'

/* ─── L2 步骤详情列表（设计图标准）──────────────────────────────────────
 * 行几何：图标盒 h-[22px] = 文字行盒 leading-[22px]，无垂直 padding，
 * 像素级对齐。行标题两段式——深色动词前缀（深度思考/网络搜索/读取文件…）
 * + 灰色目标摘要（查询词/文件名）；执行命令、浏览目录仅显示动词，
 * 具体指令只在展开卡内查看。
 * chevron 紧随文本。展开内容为行下方【全宽】浅灰圆角卡片：
 *   - 联网检索 → SearchResults 结果卡（品牌 favicon + 标题 + 域名）
 *   - 思考     → 思考原文卡（约 5 行内滚动）
 *   - 其他操作 → 参数/输出详情卡（沿用既有结构）
 * ─────────────────────────────────────────────────────────────────────── */

interface Props {
  nodes: TimelineNode[]
  className?: string
  /** 整条时间轴是否流式执行中：仅最后一行（当前实时行）自动展开，其余折叠 */
  isStreaming?: boolean
}

/** 已下线内置抓取名（历史会话兼容） */
const WEB_FETCH_TOOL_NAMES = new Set(['web_fetch', 'web_extract'])

/* ─── 展开内容：思考原文卡 ─── */

function ReasoningExpandContent({ row }: { row: StepRow }) {
  const scrollRef = useRef<HTMLDivElement>(null)
  // 用户停留在底部附近时才自动跟随（手动上滚查看历史则暂停贴底，
  // 滚回底部即恢复跟随）——流式期间内容持续增长，初始必然贴底。
  const pinnedRef = useRef(true)
  const content = row.reasoningFull ?? row.text

  useEffect(() => {
    const el = scrollRef.current
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight
  }, [content])

  const handleScroll = () => {
    const el = scrollRef.current
    if (!el) return
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24
  }

  return (
    <div className="rounded-[var(--radius-md)] border border-[var(--c-border)] bg-[var(--c-exec-block)] px-4 py-3">
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="max-h-[140px] overflow-y-auto overflow-x-hidden whitespace-pre-wrap break-words text-[length:var(--fs-sm)] leading-[24px] text-[var(--c-text-secondary)]"
      >
        {content}
      </div>
    </div>
  )
}

/* ─── 展开内容：联网检索结果卡（SearchResults 品牌卡片） ─── */

function WebSearchExpandContent({ row }: { row: StepRow }) {
  const op = row.ops[0]
  if (!op) return null
  const parsed = parseWebSearchJson(op)
  if (!parsed) {
    // parse 失败（output 不是 JSON 也没有 <!-- wsm --> 索引块）— 可能是后端 search
    // 工具输出格式变了，或者流式期间 output 还没写入。fallback 显示原始 output
    // 而不是空白卡片，让用户至少能看到返回了什么。
    const raw = (op.output ?? '').trim()
    if (!raw) return null
    return (
      <div className="rounded-[var(--radius-md)] border border-[var(--c-border)] bg-[var(--c-exec-block)]">
        <div className="px-3 py-2 max-h-[7rem] overflow-y-auto overflow-x-hidden min-w-0">
          <div className="mb-1 text-[length:var(--fs-xs)] font-medium uppercase tracking-wide text-[var(--c-text-secondary)]/45">
            原始输出
          </div>
          <pre className="m-0 whitespace-pre-wrap break-words font-mono text-[length:var(--fs-sm)] leading-relaxed text-[var(--c-text-secondary)]/80" style={{ overflowWrap: 'anywhere' }}>
            {raw}
          </pre>
        </div>
      </div>
    )
  }
  if (parsed.results.length === 0) {
    return (
      <div className="rounded-[var(--radius-md)] border border-[var(--c-border)] bg-[var(--c-exec-block)] px-4 py-2.5 text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/70">
        未命中检索结果
      </div>
    )
  }
  return (
    <SearchResults
      groups={[
        {
          id: op.callId,
          items: parsed.results.map((r, i) => ({
            id: `${op.callId}-${i}`,
            title: r.title || domainOf(r.url),
            domain: domainOf(r.url),
          })),
        },
      ]}
    />
  )
}

/** 参数/输出文本块：等宽、自动换行；滚动由外层色块容器统一接管，避免双滚动条 */
function DetailTextBlock({ text, emptyHint }: { text?: string | null; emptyHint: string }) {
  const content = (text ?? '').trim()
  if (!content) {
    return (
      <div className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/30 italic">{emptyHint}</div>
    )
  }
  return (
    <pre className="m-0 min-w-0 whitespace-pre-wrap break-words px-2.5 py-1.5 font-mono text-[length:var(--fs-sm)] leading-relaxed text-[var(--c-text-secondary)]/80" style={{ overflowWrap: 'anywhere' }}>
      {content}
    </pre>
  )
}

function OpExpandContent({ row }: { row: StepRow }) {
  const op = row.ops[0]
  if (!op) return null

  const target = row.target ?? extractArgObject(op.argsText)
  const toolName = (op.toolName ?? '').toLowerCase()
  const isWebFetch = WEB_FETCH_TOOL_NAMES.has(toolName)
  const isWebSearch = !isWebFetch && (toolName.startsWith('web') || toolName.includes('__search') || toolName.includes(':search'))
  const parsed = isWebSearch ? parseWebSearchJson(op) : null
  const hasError = op.status === 'error'

  return (
    <div className="rounded-[var(--radius-md)] border border-[var(--c-border)] bg-[var(--c-exec-block)]">
      <div className="space-y-2 px-3 py-2 max-h-[7rem] overflow-y-auto overflow-x-hidden min-w-0">
        {/* 参数区 */}
        <div>
          <div className="mb-1 text-[length:var(--fs-xs)] font-medium uppercase tracking-wide text-[var(--c-text-secondary)]/45">
            参数
          </div>
          <DetailTextBlock text={op.argsText} emptyHint="（无参数）" />
        </div>

        {/* 联网检索结果（结构化优先于原始 output；仅历史 legacy 检索工具走此分支，
            联网检索行已由 WebSearchExpandContent 渲染品牌卡片） */}
        {parsed && parsed.results.length > 0 && (
          <div>
            <div className="mb-1 text-[length:var(--fs-xs)] font-medium uppercase tracking-wide text-[var(--c-text-secondary)]/45">
              检索结果
            </div>
            <div className="flex flex-col gap-0.5 rounded-[var(--radius-control)] bg-[var(--c-exec-block-inset)] px-2.5 py-1.5">
              {parsed.results.slice(0, 3).map((r, i) => (
                <div key={i} className="flex items-baseline gap-1.5 text-[length:var(--fs-sm)]">
                  <span className="min-w-0 flex-1 truncate text-[var(--c-text-secondary)]/80" title={r.title}>
                    {r.title || r.url}
                  </span>
                  <span className="shrink-0 text-[var(--c-text-secondary)]/35">{domainOf(r.url)}</span>
                </div>
              ))}
              {parsed.resultCount > 3 && (
                <span className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/35">
                  +{parsed.resultCount - 3} 条
                </span>
              )}
            </div>
          </div>
        )}
        {parsed && parsed.resultCount === 0 && (
          <div className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/70">未命中检索结果</div>
        )}

        {/* 输出区（非检索工具直接展示原始 output）
            失败时跳过：后端 timeline.py 在 is_error 时把同一段错误文本同时写入
            output 与 error 字段（数据层完整性），下方红色错误块已完整展示——
            再渲染「输出」区会把同一句错误显示两遍。 */}
        {!isWebSearch && !(hasError && op.error) && (
          <div>
            <div className="mb-1 text-[length:var(--fs-xs)] font-medium uppercase tracking-wide text-[var(--c-text-secondary)]/45">
              输出
            </div>
            <DetailTextBlock text={op.output} emptyHint={hasError ? '（无输出）' : '（无输出内容）'} />
          </div>
        )}

        {/* 错误信息 */}
        {hasError && op.error && (
          <div className="break-words rounded-[var(--radius-control)] bg-[var(--c-exec-block)] px-2.5 py-1.5 text-[length:var(--fs-sm)] text-[var(--c-danger)]" style={{ overflowWrap: 'anywhere' }}>
            {op.error}
          </div>
        )}

        {/* 操作目标（路径/URL）作为辅助信息 */}
        {target && !isWebSearch && (
          <div className="text-[length:var(--fs-sm)] text-[var(--c-text-secondary)]/40">
            目标：<span className="truncate" title={target}>{target}</span>
          </div>
        )}
      </div>
    </div>
  )
}

/** 工具类型 → 对应图标（16px，整条时间轴同色） */
function ToolIcon({ iconType, status }: { iconType: IconType; status: string }) {
  const size = 16
  const stroke = 2
  const gray = 'shrink-0 text-[var(--c-text-secondary)]/70'

  if (status === 'running') {
    return <Loader2 size={size} strokeWidth={stroke} className="shrink-0 animate-spin text-[var(--c-accent)]" />
  }
  if (status === 'error') {
    return <XCircle size={size} strokeWidth={stroke} className="shrink-0 text-[var(--c-danger)]" />
  }

  switch (iconType) {
    case 'file_read':
      return <FileSearch size={size} strokeWidth={stroke} className={gray} />
    case 'file_edit':
      return <FilePenLine size={size} strokeWidth={stroke} className={gray} />
    case 'web_search':
    case 'fetch':
      return <Globe size={size} strokeWidth={stroke} className={gray} />
    case 'search':
      return <Search size={size} strokeWidth={stroke} className={gray} />
    case 'command':
      return <SquareTerminal size={size} strokeWidth={stroke} className={gray} />
    case 'execute':
      return <Terminal size={size} strokeWidth={stroke} className={gray} />
    case 'ask_user':
      return <HelpCircle size={size} strokeWidth={stroke} className={gray} />
    case 'clock':
      return <Clock size={size} strokeWidth={stroke} className={gray} />
    case 'check':
      return <CheckCircle2 size={size} strokeWidth={stroke} className={gray} />
    default:
      return <FileText size={size} strokeWidth={stroke} className={gray} />
  }
}

/* ─── 行视图 ─── */

function StepRowView({ row, autoOpen }: {
  row: StepRow
  /** 是否为当前实时行（流式中的最后一行）——驱动自动展开/折叠 */
  autoOpen: boolean
}) {
  // 展开状态 = 用户手动干预优先，否则跟随 autoOpen：
  // 新行出现 → 上一行 autoOpen 变 false 自动折叠；流式结束全部自动折叠；
  // 用户点开的行保持展开，不被自动收起
  const [userOverride, setUserOverride] = useState<boolean | null>(null)
  const open = userOverride ?? autoOpen
  const op = row.ops[0]
  const hasError = op?.status === 'error'
  const isWebSearch = row.iconType === 'web_search'

  // 思考行：有完整原文可展开；联网检索行恒可展开（结果可能流式补齐）；
  // 其余操作行：有参数/输出/错误可展开查看详情
  const hasDetails = row.kind === 'reasoning'
    ? !!row.reasoningFull
    : isWebSearch || !!(op && (op.argsText || op.output || op.error))

  return (
    <div>
      {/* 标题行：图标列 + 两段式文本 + 紧随文本的 chevron（设计图标准） */}
      <div className="flex items-start gap-4">
        <div className="shrink-0 w-4">
          <div className="flex items-center justify-center h-[22px]">
            <ToolIcon iconType={row.iconType} status={row.kind === 'reasoning' ? 'done' : (op?.status ?? 'pending')} />
          </div>
        </div>

        <div className="flex-1 min-w-0">
          <button
            type="button"
            onClick={() => {
              if (!hasDetails) return
              setUserOverride(!(userOverride ?? autoOpen))
            }}
            className={`flex w-full items-center gap-2 text-left ${hasDetails ? 'cursor-pointer hover:bg-[var(--c-exec-block)]/20' : 'cursor-default'}`}
            aria-expanded={hasDetails ? open : undefined}
          >
            <span
              className="block min-w-0 truncate text-[length:var(--fs-sm)] leading-[22px] font-normal"
              title={row.kind === 'reasoning' ? row.reasoningFull ?? row.text : row.text}
            >
              {row.label && (
                <span className="font-medium text-[var(--c-text-secondary)]">{row.label}</span>
              )}
              {row.label && row.text ? ' ' : ''}
              <span className={hasError ? 'text-[var(--c-danger)]/80' : 'text-[var(--c-text-secondary)]'}>
                {row.text}
              </span>
            </span>

            {hasDetails && (
              <ChevronDown
                size={14}
                strokeWidth={2}
                className={`shrink-0 text-[var(--c-text-secondary)]/50 transition-transform duration-200 ${open ? 'rotate-0' : '-rotate-90'}`}
              />
            )}
          </button>

          {/* HITL 答案子行：用户回答到达后（合成 ToolCallEnd 落 output）显示，
              缩进归属到「请求用户澄清」步骤下；前缀「你的回答」明确这是用户作答，
              灰色但加粗——与提问摘要区分靠字重（执行过程整体降噪为灰） */}
          {row.answer && (
            <div className="flex items-center gap-1.5">
              <span className="shrink-0 text-[length:var(--fs-sm)] leading-[22px] font-medium text-[var(--c-text-secondary)]/70">
                你的回答
              </span>
              <span className="block min-w-0 truncate text-[length:var(--fs-sm)] leading-[22px] font-medium text-[var(--c-text-secondary)]" title={row.answer}>
                {row.answer}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* 展开卡片：全宽（不缩进图标列），紧贴标题行下方（设计图标准） */}
      {open && hasDetails && (
        <div className="mt-2">
          {row.kind === 'reasoning'
            ? <ReasoningExpandContent row={row} />
            : isWebSearch
              ? <WebSearchExpandContent row={row} />
              : <OpExpandContent row={row} />}
        </div>
      )}
    </div>
  )
}

/* ─── 主组件 ─── */

export function StepDetailList({ nodes, className, isStreaming = false }: Props) {
  const rows = useMemo(() => buildStepRows(nodes), [nodes])
  if (rows.length === 0) return null
  return (
    <div className={className ?? ''}>
      {rows.map((row, i) => (
        <div key={row.key} className={i > 0 ? 'mt-4' : ''}>
          <StepRowView
            row={row}
            // 仅流式中的最后一行（当前实时行）自动展开；历史会话全部折叠
            autoOpen={isStreaming && i === rows.length - 1}
          />
        </div>
      ))}
    </div>
  )
}
