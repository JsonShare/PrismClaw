import type { OperationDetail } from '../../types'

/* ─── timeline 输出/参数解析（轻/重路径共享） ──────────────────────────────
 * 原 StepTimeline（重路径）与 LightToolSummary（轻路径）各自维护同构的
 * JSON 检测、域名提取、参数对象提取——两份实现已在细节上漂移（正则集合、
 * 判空条件不一致）。本模块是唯一解析实现；2026-09-03 两组件已合并为
 * ExecutionTimeline，共享关系从"两处调用"变成"唯一视图映射"。
 * ──────────────────────────────────────────────────────────────────────── */

/** 从 URL 提取域名（去 www. 前缀）；非法 URL 原样返回（展示锚文本用） */
export function domainOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

/**
 * 从工具参数文本提取「动作对象」（查询词 / URL / 命令 / 路径）。
 * 优先级：常见键名前缀（query/q/pattern/url/command/file_path/path/file/skill: xxx）
 * → 首个 http(s) URL → 首个非空行；提取不到返回 fallback。
 */
export function extractArgObject(raw: string | null | undefined, fallback = ''): string {
  const text = (raw ?? '').trim()
  if (!text) return fallback
  const m = text.match(/^(?:query|q|pattern|url|command|file_path|path|file|skill)\s*[:：]\s*(.+)$/im)
  if (m) return m[1].trim()
  const url = text.match(/https?:\/\/\S+/)
  if (url) return url[0]
  const firstLine = text
    .split('\n')
    .map((s) => s.trim())
    .find((s) => s.length > 0)
  return firstLine || fallback
}

/* ─── AskUserQuestion（HITL）时间线解析 ─────────────────────────────────── */

/** 超时兜底文本前缀（后端 wait_for_clarification 落树的系统提示） */
const CLARIFY_TIMEOUT_PREFIX = '[System: clarification timed out'

export interface AskUserInteraction {
  /** 第一题的问题文本（行标题展示用） */
  question: string
  /** 用户答案（HITL 合成 ToolCallEnd 的结果文本）；未回答（进行中/历史旧行）为 null */
  answer: string | null
}

/**
 * 解析 ask_user_question 操作的「提问 + 用户答案」。
 * 参数形态（后端 _format_args_text）：questions 为 list → json.dumps 单行
 * `questions: [{...}]`；取第一题的 question 文本做行标题。
 * 答案来自 HITL 路径补发的合成 ToolCallEnd 结果文本；超时兜底文本映射为短提示。
 */
export function parseAskUserInteraction(
  op: Pick<OperationDetail, 'argsText' | 'output'>,
): AskUserInteraction | null {
  let question = ''
  const m = (op.argsText ?? '').match(/^questions\s*[:：]\s*(\[[\s\S]*)$/m)
  if (m) {
    try {
      const arr: unknown = JSON.parse(m[1])
      if (Array.isArray(arr)) {
        for (const q of arr) {
          const t = (q as { question?: unknown })?.question
          if (typeof t === 'string' && t.trim()) {
            question = t.trim()
            break
          }
        }
      }
    } catch {
      /* 非法 JSON 落 extractArgObject 兜底 */
    }
  }
  if (!question) question = extractArgObject(op.argsText)
  if (!question) return null

  const out = (op.output ?? '').trim()
  const answer = out
    ? out.startsWith(CLARIFY_TIMEOUT_PREFIX)
      ? '（超时未回答）'
      : out
    : null
  return { question, answer }
}

/** ask_user_question 家族判定（与后端 _is_ask_user_question 同判据） */
export function isAskUserToolName(name: string | null | undefined): boolean {
  const n = (name ?? '').toLowerCase()
  return n.includes('askuserquestion') || n.includes('ask_user_question') || n.includes('ask_user') || n.includes('askuser')
}

/** 独立 MCP 检索服务 search 工具输出的结构化形态（两组件视图映射的公共中间态） */
export interface WebSearchJson {
  query: string
  resultCount: number
  results: Array<{ title: string; url: string; content: string }>
  truncated: boolean
}

/**
 * 从工具输出文本中提取检索结果 JSON。
 * 兼容两种形态：
 * 1. 历史会话：整个 output 是 JSON（{query, result_count, results, truncated}）；
 * 2. 新会话（检索 agent 版）：output 是 markdown 报告，尾部携带
 *    `<!-- wsm:{...} -->` 机器索引块（渲染不可见，LLM 上下文廉价）。
 * 非检索输出返回 null。
 */
export function parseWebSearchJson(op: Pick<OperationDetail, 'output' | 'argsText'>): WebSearchJson | null {
  if (!op.output) return null
  let data: unknown = null
  try {
    data = JSON.parse(op.output)
  } catch {
    const m = op.output.match(/<!-- wsm:(\{[\s\S]*\})\s*-->\s*$/)
    if (!m) return null
    try {
      data = JSON.parse(m[1])
    } catch {
      return null
    }
  }
  const results = Array.isArray((data as { results?: unknown })?.results)
    ? ((data as { results: unknown[] }).results as Array<Record<string, unknown>>)
    : null
  if (!results) return null
  let query = String((data as { query?: unknown })?.query || '')
  if (!query && op.argsText) {
    // argsText 本身是 JSON（后端结构化参数）时从中取 query/q
    try {
      const args: unknown = JSON.parse(op.argsText)
      query = String((args as { query?: unknown })?.query || (args as { q?: unknown })?.q || '')
    } catch {
      /* argsText 是自由文本，落入 extractArgObject */
    }
  }
  if (!query) query = extractArgObject(op.argsText)
  return {
    query,
    resultCount:
      typeof (data as { result_count?: unknown })?.result_count === 'number'
        ? (data as { result_count: number }).result_count
        : results.length,
    results: results.map((r) => ({
      title: String(r?.title || ''),
      url: String(r?.url || ''),
      content: String(r?.content || r?.snippet || ''),
    })),
    truncated: Boolean((data as { truncated?: unknown })?.truncated),
  }
}
