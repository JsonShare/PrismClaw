import { useEffect, useState } from 'react'
import { apiUrl } from '../../lib/system-adapter'
import { fetchOutputs } from '../../lib/file/outputFiles'
import { workspaceRef, type ContextRef } from '../../lib/chat/contextRefs'

// ---------------------------------------------------------------------------
// 工作区文件检索
//
// 空查询走 `/api/workspaces/outputs`（跨会话聚合最近产出物）——`/api/files/search`
// 在空查询下直接返回空数组，不能作为默认组；非空查询走 `/api/files/search`，
// 200ms 防抖。两条接口返回的路径都是工作区根相对路径，与文件工具入参同源。
// ---------------------------------------------------------------------------

interface SearchMatch {
  path: string
  kind: 'file' | 'dir'
}

const DEFAULT_LIMIT = 30
const SEARCH_LIMIT = 50

interface Params {
  token: string | null
  workspaceId: string
  query: string
  enabled: boolean
}

export function useWorkspaceFileSearch({ token, workspaceId, query, enabled }: Params) {
  const [files, setFiles] = useState<ContextRef[]>([])
  const [loading, setLoading] = useState(false)
  const [failed, setFailed] = useState(false)
  const q = query.trim()

  useEffect(() => {
    if (!enabled || !token) return
    let cancelled = false

    const run = async () => {
      setLoading(true)
      setFailed(false)
      try {
        if (!q) {
          const recent = (await fetchOutputs(workspaceId, token))
            .slice()
            .sort((a, b) => (b.mtime_ms ?? 0) - (a.mtime_ms ?? 0))
            .slice(0, DEFAULT_LIMIT)
          if (!cancelled) setFiles(recent.map((item) => workspaceRef(item.rel_path, item.name)))
        } else {
          const res = await fetch(
            apiUrl(
              `/api/files/search?workspace_id=${encodeURIComponent(workspaceId)}`
              + `&query=${encodeURIComponent(q)}&limit=${SEARCH_LIMIT}`,
            ),
            { headers: { Authorization: `Bearer ${token}` } },
          )
          if (!res.ok) throw new Error(`HTTP ${res.status}`)
          const data: { matches?: SearchMatch[] } = await res.json()
          if (!cancelled) {
            setFiles(
              (data.matches ?? [])
                .filter((m) => m.kind === 'file')
                .map((m) => workspaceRef(m.path)),
            )
          }
        }
      } catch {
        if (!cancelled) {
          setFiles([])
          setFailed(true)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    const timer = window.setTimeout(run, q ? 200 : 0)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [enabled, q, token, workspaceId])

  return { files, loading, failed }
}
