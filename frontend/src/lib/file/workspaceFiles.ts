import { apiUrl } from '../system-adapter'

/** 右栏文件项（path 为工作区相对路径）。 */
export interface PanelFile {
  path: string
  name: string
  sizeBytes: number
  ext: string
  mtimeMs: number
}

/** 兼容旧命名 */
export type PanelUploadFile = PanelFile
export type PanelOutputFile = PanelFile

export interface WorkspaceTree {
  dirs: string[]
  files: PanelFile[]
}

/** 目录递归深度上限，避免异常深层目录造成请求风暴 */
const MAX_TREE_DEPTH = 5
/** 单目录条目上限（后端允许的最大值） */
const LIST_LIMIT = 5000

function extOf(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : ''
}

function authHeaders(token: string | null, json = false): Record<string, string> {
  const headers: Record<string, string> = {}
  if (json) headers['Content-Type'] = 'application/json'
  if (token) headers.Authorization = `Bearer ${token}`
  return headers
}

async function postJson(path: string, body: unknown, token: string | null): Promise<void> {
  const res = await fetch(apiUrl(path), {
    method: 'POST',
    headers: authHeaders(token, true),
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    let detail = ''
    try {
      const text = await res.text()
      try {
        const parsed = JSON.parse(text) as { detail?: unknown }
        detail = typeof parsed.detail === 'string' ? parsed.detail : text
      } catch {
        detail = text
      }
    } catch {
      // 忽略读取失败，保留状态码错误
    }
    const err = new Error(detail || `请求失败（HTTP ${res.status}）`) as Error & { status?: number }
    err.status = res.status
    throw err
  }
}

interface FileEntry {
  name: string
  path: string
  kind: string
  size_bytes?: number | null
  mtime_ms?: number | null
}

/**
 * 递归读取工作区内的目录与文件。
 * 目录不存在（404）按空处理；根目录读取失败（非 404）抛出，便于上层展示错误。
 */
export async function listWorkspaceTree(
  workspaceId: string,
  rootPath: string,
  token: string | null,
  maxDepth = MAX_TREE_DEPTH,
): Promise<WorkspaceTree> {
  const dirs: string[] = []
  const files: PanelFile[] = []

  const walk = async (path: string, depth: number): Promise<void> => {
    if (depth > maxDepth) return
    const params = new URLSearchParams({
      workspace_id: workspaceId,
      path,
      limit: String(LIST_LIMIT),
    })
    let res: Response
    try {
      res = await fetch(apiUrl(`/api/files/list?${params.toString()}`), {
        headers: authHeaders(token),
      })
    } catch {
      return
    }
    if (!res.ok) {
      if (depth === 0 && res.status !== 404) {
        throw new Error(`加载目录失败：HTTP ${res.status}`)
      }
      return
    }
    let entries: FileEntry[]
    try {
      const data = (await res.json()) as { entries?: FileEntry[] }
      entries = data.entries ?? []
    } catch {
      return
    }
    const childDirs: FileEntry[] = []
    for (const entry of entries) {
      if (entry.kind === 'dir') {
        childDirs.push(entry)
        dirs.push(entry.path)
      } else if (entry.kind === 'file') {
        files.push({
          path: entry.path,
          name: entry.name,
          sizeBytes: entry.size_bytes ?? 0,
          ext: extOf(entry.name),
          mtimeMs: entry.mtime_ms ?? 0,
        })
      }
    }
    // 同层目录并行读取，避免串行等待。
    await Promise.all(childDirs.map((entry) => walk(entry.path, depth + 1)))
  }

  if (rootPath) await walk(rootPath, 0)
  return { dirs, files }
}

/** 当前会话的交付物目录树（outbox）。 */
export async function listSessionOutputs(
  workspaceId: string,
  sessionId: string,
  token: string | null,
): Promise<WorkspaceTree> {
  return listWorkspaceTree(workspaceId, `sessions/${sessionId}/outbox`, token)
}

/** 某个 session 的上传目录树（inbox，含嵌套目录与文件）。 */
export async function listSessionInbox(
  workspaceId: string,
  sessionId: string,
  token: string | null,
): Promise<WorkspaceTree> {
  return listWorkspaceTree(workspaceId, `sessions/${sessionId}/inbox`, token)
}

/** 删除工作区内的文件/目录；目录非空时必须递归删除。 */
export async function deleteWorkspacePath(
  workspaceId: string,
  path: string,
  token: string | null,
  recursive = false,
): Promise<void> {
  const params = new URLSearchParams({ workspace_id: workspaceId })
  await postJson(`/api/files/delete?${params.toString()}`, { path, recursive }, token)
}

/** 移动/重命名工作区内文件或目录。 */
export async function moveWorkspacePath(
  workspaceId: string,
  src: string,
  dst: string,
  token: string | null,
): Promise<void> {
  const params = new URLSearchParams({ workspace_id: workspaceId })
  await postJson(
    `/api/files/move?${params.toString()}`,
    { src, dst, overwrite: false },
    token,
  )
}

/** 在工作区内创建文件夹（与「创建工作区」的 /api/workspaces 无关）。 */
export async function mkdirWorkspacePath(
  workspaceId: string,
  path: string,
  token: string | null,
): Promise<void> {
  const params = new URLSearchParams({ workspace_id: workspaceId })
  await postJson(`/api/files/mkdir?${params.toString()}`, { path }, token)
}
