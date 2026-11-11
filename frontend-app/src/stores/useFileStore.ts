import { create } from 'zustand'

import { deleteOutput, listOutputs } from '../services/files'
import type { OutputFile } from '../services/files'

interface FileStore {
  items: OutputFile[]
  loading: boolean
  loadedWorkspaceId: string

  load: (workspaceId: string, force?: boolean) => Promise<void>
  remove: (file: OutputFile) => Promise<void>
}

export const useFileStore = create<FileStore>((set, get) => ({
  items: [],
  loading: false,
  loadedWorkspaceId: '',

  load: async (workspaceId, force = false) => {
    if (get().loading) return
    if (!force && get().loadedWorkspaceId === workspaceId) return
    set({ loading: true })
    try {
      const res = await listOutputs(workspaceId)
      set({ items: res.items, loadedWorkspaceId: workspaceId })
    } finally {
      set({ loading: false })
    }
  },

  remove: async (file) => {
    await deleteOutput(file)
    set({ items: get().items.filter((item) => !(item.workspace_id === file.workspace_id && item.rel_path === file.rel_path)) })
  },
}))
