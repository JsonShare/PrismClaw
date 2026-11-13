/**
 * useTrajectoryCells — per-conversation-event cell 抓取 hook。
 *
 * 调用 GET /api/sessions/{sid}/trajectory/cells 返回 TrajectoryCell[]
 * （每个 tree 节点一个 cell）。这是 timeline + ledger 的数据源 —— 它们
 * 渲染 cells，不是 per-LLM-iteration 的 records。
 *
 * 增量轮询（C8）：面板打开期间，每 POLL_INTERVAL_MS 用
 * ?after_seq=<latest_seq> 轮询一次。后端返回新行（seq > after_seq）
 * 以及仍在等待 metrics 补齐的行（completed_at_ms IS NULL — assistant cell
 * 在 insert 后回填 usage/model/output），前端按 cell_id 合并并按 seq 重排。
 * 手动刷新按钮仍可用作全量刷新。
 *
 * records（RequestAuditRecord）通过 useTrajectory 单独抓取，
 * 仅在 Inspector 面板中用于调试 4 层 pipeline。
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
 * ledger 里的 cell 按时间顺序排列：
 *  1. 按 conversation turn 分组（turn ASC）
 *  2. system-prompt cell（Input / lane 0）浮到所在 turn 的最前面，
 *     让每个 turn 的阅读顺序是 "system → user → assistant → tools"，
 *     而不是 system 快照被钉在整条时间线末尾（它的 seq 设计上是 MAX+1 ——
 *     见 sql_models.py 的 INSERT_SYSTEM_CELL）。
 *  3. turn 内保持自然 seq 顺序。
 *
 * 镜像后端 sql_models.SELECT_CELLS_* 的 ORDER BY，保证全量加载和
 * 增量轮询两条路径渲染结果一致。
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

/** 将一批增量结果合并进当前 cells（按 cell_id，保持时间顺序）。 */
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

  // 增量轮询：取 seq 游标之后的行，前端合并
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
      // 轮询尽力而为，不暴露错误 — 手动刷新负责报错
    }
  }, [sessionId, token, onUnauthorized])

  // 打开 / 切换 session 时初次全量加载
  useEffect(() => {
    if (!enabled) return
    latestSeqRef.current = 0
    setCells([])
    void refresh()
  }, [enabled, refresh])

  // 打开期间持续轮询
  useEffect(() => {
    if (!enabled) return
    const timer = window.setInterval(() => { void pollIncremental() }, POLL_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [enabled, pollIncremental])

  return { cells, loading, error, refresh }
}
