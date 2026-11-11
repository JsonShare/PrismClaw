import { useCallback, useEffect, useState } from 'react'
import { apiUrl } from '../../lib/system-adapter'
import type { TrajectorySnapshot } from '../../types'

interface UseTrajectoryResult {
  data: TrajectorySnapshot | null
  /** 后端降级原因 (read_warning 字段), 如 "redis_client=None" / "load_state failed". */
  readWarning: string | null
  loading: boolean
  /** fetch 层错误 (网络/401/非 2xx), 与 readWarning 互补. */
  error: string | null
  refresh: () => Promise<void>
}

/**
 * 封装 GET /api/sessions/{sid}/trajectory 调用.
 * - token 401 时回调 onUnauthorized.
 * - 后端降级时 records 为空但返回 200 + read_warning 字段, 单独暴露给 UI.
 */
export function useTrajectory(
  sessionId: string | null | undefined,
  token: string | null,
  enabled: boolean,
  onUnauthorized?: () => void,
): UseTrajectoryResult {
  const [data, setData] = useState<TrajectorySnapshot | null>(null)
  const [readWarning, setReadWarning] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (!sessionId || !token) return
    setLoading(true)
    setError(null)
    setReadWarning(null)
    try {
      const res = await fetch(
        apiUrl(`/api/sessions/${encodeURIComponent(sessionId)}/trajectory?limit=200`),
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
      const json = await res.json() as TrajectorySnapshot & { read_warning?: string }
      setData(json)
      setReadWarning(typeof json.read_warning === 'string' && json.read_warning ? json.read_warning : null)
    } catch (e) {
      setError(e instanceof Error ? e.message : '网络异常')
    } finally {
      setLoading(false)
    }
  }, [sessionId, token, onUnauthorized])

  useEffect(() => {
    if (!enabled) return
    void refresh()
  }, [enabled, refresh])

  return { data, readWarning, loading, error, refresh }
}
