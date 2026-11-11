/**
 * searchIndex.ts — Incremental full-text index for the trajectory ledger.
 *
 * Ported from DSH:
 *   packages/client/ui-trajectory/src/client/trajectory-search-index.ts
 *
 * Adaptations to PrismClaw's model (documented inline):
 *   - Input is a FLAT TrajectoryCell[] instead of DSH's nested
 *     turns[].groups[].cells layouts; entries are keyed by the stable
 *     cell_id (DSH keys by trajectoryRecordId).
 *   - Field mapping: DSH's text/previewMarkdown/result* → PrismClaw's
 *     content_preview / tool_calls_summary / output_detail; token and
 *     timing fields do not exist on DSH cells and are added here.
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

/** Tool display name — backend tool_name first, tool_calls_summary fallback. */
function toolNameOf(cell: TrajectoryCell): string {
  if (cell.tool_name) return cell.tool_name
  const summary = cell.tool_calls_summary[0]
  if (summary === undefined) return ''
  const match = summary.match(/^([^(]*)\(/)
  return match !== null ? match[1] ?? '' : summary
}

/** DSH markdownPreview: content_preview is the backend-rendered preview. */
function markdownPreview(cell: TrajectoryCell): string {
  if (cell.content_preview === null) return ''
  return trajectoryPreviewText(cell.content_preview)
}

/** DSH resultPreview: bounded plain-text projection of the tool result. */
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

/** Session-view-local index that reparses Markdown only when one record's source changes. */
export class TrajectorySearchIndex {
  private readonly entries = new Map<string, SearchEntry>()
  private cells: readonly TrajectoryCell[] | undefined

  /**
   * Incrementally synchronize the current flat cell list.
   * @param cells - Unfiltered flat trajectory cells from the same view.
   * @returns Whether the indexed cell list changed.
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
   * Match a query against the latest committed index version.
   * @param query - Space-separated case-insensitive search terms.
   * @returns Matching cell ids, or `null` without a query.
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
