/**
 * useTrajectoryCells — DSH-aligned per-conversation-event cell fetcher.
 *
 * Calls GET /api/sessions/{sid}/trajectory/cells and returns TrajectoryCell[]
 * (one cell per tree node).  This is the DSH-style data source for the
 * timeline + ledger — they render cells, NOT per-LLM-iteration records.
 *
 * Incremental polling (C8): while the panel is open, polls
 * ?after_seq=<latest_seq> every POLL_INTERVAL_MS. The backend returns new
 * rows (seq > after_seq) plus rows still pending metrics enrichment
 * (completed_at_ms IS NULL — assistant cells get usage/model/output
 * backfilled after insert), which are merged by cell_id and re-sorted by
 * seq. Full refresh stays available for the manual refresh button.
 *
 * Records (RequestAuditRecord) are fetched separately via useTrajectory,
 * and used only by the Inspector panel for debugging the 4-layer pipeline.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { apiUrl } from '../../lib/system-adapter'
import type { TrajectoryCellSnapshot, TrajectoryCell } from '../../types'

const POLL_INTERVAL_MS = 3_000

interface UseTrajectoryCellsResult {
  cells: TrajectoryCell[]
  loading: boolean
  error: string | null
  refresh: () => Promise<void>
}

/**
 * Chronological order for the trajectory ledger:
 *  1. group by conversation turn (turn ASC)
 *  2. float the system-prompt cell (Input / lane 0) to the head of its turn,
 *     so each turn reads "system → user → assistant → tools" instead of the
 *     system snapshot being pinned to the end of the whole timeline (its seq
 *     is MAX+1 by design — see INSERT_SYSTEM_CELL in sql_models.py).
 *  3. preserve natural seq order within the turn.
 *
 * Mirrors the backend ORDER BY in sql_models.SELECT_CELLS_* so the full-load
 * and incremental-poll paths render identically.
 */
function cellOrder(c: TrajectoryCell): [number, number, number] {
  return [c.turn, c.kind === 'system' ? 0 : 1, c.seq]
}
function sortCells(cells: readonly TrajectoryCell[]): TrajectoryCell[] {
  return [...cells].sort((a, b) => {
    const ka = cellOrder(a)
    const kb = cellOrder(b)
    return ka[0] - kb[0] || ka[1] - kb[1] || ka[2] - kb[2]
  })
}

/** Merge an incremental batch into the current cells (by cell_id, chronological order). */
function mergeCells(
  current: readonly TrajectoryCell[],
  batch: readonly TrajectoryCell[],
): TrajectoryCell[] {
  if (batch.length === 0) return [...current]
  const byId = new Map(current.map(c => [c.cell_id, c]))
  for (const cell of batch) byId.set(cell.cell_id, cell)
  return sortCells([...byId.values()])
}

export function useTrajectoryCells(
  sessionId: string | null | undefined,
  token: string | null,
  enabled: boolean,
  onUnauthorized?: () => void,
): UseTrajectoryCellsResult {
  const [cells, setCells] = useState<TrajectoryCell[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const latestSeqRef = useRef(0)

  const refresh = useCallback(async () => {
    if (!sessionId || !token) return
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(
        apiUrl(`/api/sessions/${encodeURIComponent(sessionId)}/trajectory/cells`),
        { headers: { Authorization: `Bearer ${token}` } },
      )
      if (res.status === 401) {
        onUnauthorized?.()
        return
      }
      if (!res.ok) {
        setError(`请求失败 (${res.status})`)
        return
      }
      const json = await res.json() as TrajectoryCellSnapshot
      const next = Array.isArray(json.cells) ? json.cells : []
      latestSeqRef.current = json.latest_seq ?? next.reduce(
        (max, c) => Math.max(max, c.seq), 0,
      )
      setCells(sortCells(next))
    } catch (e) {
      setError(e instanceof Error ? e.message : '网络异常')
    } finally {
      setLoading(false)
    }
  }, [sessionId, token, onUnauthorized])

  // Incremental poll: rows newer than the seq cursor, merged client-side.
  const pollIncremental = useCallback(async () => {
    if (!sessionId || !token) return
    try {
      const res = await fetch(
        apiUrl(
          `/api/sessions/${encodeURIComponent(sessionId)}/trajectory/cells?after_seq=${latestSeqRef.current}`,
        ),
        { headers: { Authorization: `Bearer ${token}` } },
      )
      if (res.status === 401) {
        onUnauthorized?.()
        return
      }
      if (!res.ok) return
      const json = await res.json() as TrajectoryCellSnapshot
      const batch = Array.isArray(json.cells) ? json.cells : []
      if (batch.length === 0) return
      latestSeqRef.current = json.latest_seq ?? latestSeqRef.current
      setCells(prev => mergeCells(prev, batch))
    } catch {
      // Polling is best-effort; surface nothing — the manual refresh reports errors.
    }
  }, [sessionId, token, onUnauthorized])

  // Initial full load on open / session switch.
  useEffect(() => {
    if (!enabled) return
    latestSeqRef.current = 0
    setCells([])
    void refresh()
  }, [enabled, refresh])

  // Poll while open.
  useEffect(() => {
    if (!enabled) return
    const timer = window.setInterval(() => { void pollIncremental() }, POLL_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [enabled, pollIncremental])

  return { cells, loading, error, refresh }
}
