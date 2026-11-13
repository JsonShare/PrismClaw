/**
 * searchIndex.ts — ledger 增量全文索引。
 *
 * 适配 PrismClaw 的数据模型：
 *   - 输入是扁平 TrajectoryCell[]（不是嵌套的 turns[].groups[].cells），
 *     条目以稳定的 cell_id 为键。
 *   - 字段映射：content_preview / tool_calls_summary / output_detail 等，
 *     token 和 timing 字段为 PrismClaw 特有，在索引里一并纳入。
 */

import type { TrajectoryCell } from '../../../types'
import { trajectoryPreviewText } from './preview'

interface SearchEntry {
  readonly sources: readonly string[]
  readonly text: string
}

function sameSources(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index])
}

/** 工具显示名 — 优先 backend tool_name，否则从 tool_calls_summary 提取。 */
function toolNameOf(cell: TrajectoryCell): string {
  if (cell.tool_name) return cell.tool_name
  const summary = cell.tool_calls_summary[0]
  if (summary === undefined) return ''
  const match = summary.match(/^([^(]*)\(/)
  return match !== null ? match[1] ?? '' : summary
}

/** Markdown 预览投影（content_preview 经 trajectoryPreviewText 截断）。 */
function markdownPreview(cell: TrajectoryCell): string {
  if (cell.content_preview === null) return ''
  return trajectoryPreviewText(cell.content_preview)
}

/** 工具结果预览投影。 */
function resultPreview(cell: TrajectoryCell): string {
  return cell.output_detail === null ? '' : trajectoryPreviewText(cell.output_detail)
}

function recordSources(cell: TrajectoryCell): readonly string[] {
  return [
    `turn ${cell.turn}`,
    cell.kind,
    cell.kind === 'message' ? 'assistant' : '',
    cell.content_preview ?? '',
    ...cell.tool_calls_summary,
    cell.input_detail ?? '',
    cell.output_detail ?? '',
    cell.thinking_detail ?? '',
    cell.schema_detail ?? '',
    cell.model ?? '',
    cell.finish_reason ?? '',
    cell.error ?? '',
    cell.tool_call_id ?? '',
    toolNameOf(cell),
  ]
}

/** 会话级增量索引，仅在 cell 源文本变化时重新解析 Markdown。 */
export class TrajectorySearchIndex {
  private readonly entries = new Map<string, SearchEntry>()
  private cells: readonly TrajectoryCell[] | undefined

  /**
   * 增量同步当前扁平 cell 列表。
   * @param cells — 未过滤的轨迹 cells
   * @returns 索引的 cell 列表是否发生变化
   */
  update(cells: readonly TrajectoryCell[]): boolean {
    if (this.cells === cells) return false
    this.cells = cells
    const seen = new Set<string>()
    for (const cell of cells) {
      const id = cell.cell_id
      const sources = recordSources(cell)
      const previous = this.entries.get(id)
      const entry = previous !== undefined && sameSources(previous.sources, sources)
        ? previous
        : {
          sources,
          text: [
            ...sources,
            markdownPreview(cell),
            resultPreview(cell),
          ].join('\n').toLocaleLowerCase(),
        }
      this.entries.set(id, entry)
      seen.add(id)
    }
    for (const id of this.entries.keys()) {
      if (!seen.has(id)) this.entries.delete(id)
    }
    return true
  }

  /**
   * 用最新已提交的索引版本匹配查询。
   * @param query — 空格分隔的大小写不敏感搜索词
   * @returns 匹配的 cell id 集合，或无查询时返回 null
   */
  search(query: string): ReadonlySet<string> | null {
    const terms = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean)
    if (terms.length === 0) return null
    const matches = new Set<string>()
    for (const [id, entry] of this.entries) {
      if (terms.every(term => entry.text.includes(term))) matches.add(id)
    }
    return matches
  }
}
