import { http } from './request'

export interface Workspace {
  id: string
  name: string
}

export async function listWorkspaces(): Promise<Workspace[]> {
  const res = await http.get<{ workspaces: Workspace[] }>('/api/workspaces')
  return res?.workspaces ?? []
}
